# api_client.py
"""DeepSeek 客户端：成功返回内容，失败返回 ``None``（并把原因写进 ``last_error``）。

契约（P10-B1）：**失败绝不返回人类可读文案**。调用方一律用 ``is None`` 判失败，
错误文案只由 UI 层从 ``last_error`` 取用。历史实现返回「API 调用失败」这类文案，
被 word_monitor 当成校对结果做 diff、被剪贴板直接写回，属于假成功。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

import requests

from core.services.api_logger import log_api_call
from core.services.proof_cache import get_default_cache

logger = logging.getLogger(__name__)

LOG_FILE = Path(__file__).parent / "api_calls.log"

_SESSION_LOCK = threading.Lock()
_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    """进程内复用的 HTTP 会话（keep-alive）。

    每次 `requests.post` 都会新建连接（DNS + TCP + TLS 握手），实测多出 100~300ms；
    校对是"一句话一次请求"的高频短请求，复用连接收益明显。
    """
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            _SESSION = session
        return _SESSION


class DeepSeekClient:
    def __init__(self, config, i18n=None):
        self.config = config
        self.i18n = i18n
        # ConfigManager 走加密通道；测试场景传入普通 dict 时退回明文读取
        if hasattr(config, "get_api_key"):
            self.api_key = config.get_api_key()
        else:
            self.api_key = config.get("api_key", "")
        self.model = config.get("model", "deepseek-v4-flash")
        self.base_url = "https://api.deepseek.com/v1/chat/completions"
        self.last_messages = None
        self.last_usage = None
        self.last_api_success = False
        self.last_error = ""
        #: 最近一次真实网络往返耗时（ms）；命中本地缓存时不更新
        self.last_latency_ms = 0
        self._warm_up_connection()

    def _warm_up_connection(self) -> None:
        """后台预热连接：首个校对请求省掉 TLS 握手（约 100~300ms）。

        只对 API 主机发一个 HEAD（不消耗 token）；失败静默——离线/被墙时不该影响启动。
        """
        if not self.api_key:
            return

        def _warm() -> None:
            try:
                _session().head("https://api.deepseek.com", timeout=3)
            except Exception:  # noqa: BLE001  预热失败不影响真正请求
                logger.debug("API 预连接失败（忽略）", exc_info=True)

        threading.Thread(target=_warm, name="api-warmup", daemon=True).start()

    # 新增辅助方法：判断文本是否包含占位符(如[IMG0]、[EQ1])
    def _has_placeholders(self, text: str) -> bool:
        return bool(re.search(r'\[(IMG|EQ)\d+\]', text))

    def prompt_tr(self, key: str) -> str:
        """按「提示词语言」取默认提示词；服务缺失时回退到 key 本身。"""
        if self.i18n is None:
            return key
        return self.i18n.prompt_tr(key)

    def _prompt_template(self, config_key: str, default_key: str) -> str | None:
        """取提示词模板；配置里恒存在该键（可能为空串）⇒ 空串要走语言包默认文案。

        历史实现用 ``config.get(key, 默认)``，因为键永远存在，第二参数永不生效，
        导致默认配置下翻译/改写拿到空模板而静默失效。
        """
        template = (self.config.get(config_key) or "").strip() or self.prompt_tr(default_key)
        if not template or "{text}" not in template:
            self.last_error = f"提示词模板缺失或缺少 {{text}}：{config_key}"
            logger.error("%s", self.last_error)
            return None
        return template

    def proof_text(self, text: str, cache_key: str | None = None) -> str | None:
        """校对文本；失败返回 ``None``，原因见 ``last_error``。

        cache_key：缓存键。proof_sentence 以 body_text（去尾部的正文）为键调用，
        与直接以原文为键的剪贴板校对共用同一份 SQLite 缓存；
        不传时默认以 text 本身为键。
        """
        key = cache_key if cache_key is not None else text
        cache = get_default_cache()
        cached = cache.get(key)
        if cached is not None:
            return cached.strip()

        template = (self.config.get("proof_prompt") or "").strip()
        if not template:
            # 默认提示词走「提示词语言」，与界面语言互不影响
            template = self.prompt_tr("prompt.proof.default")
        if "{text}" not in template:
            template += "\n{text}"
        prompt = template.replace("{text}", text)

        # 根据是否含有占位符，动态设置 system 消息，减少无占位符时的 token 消耗
        if self._has_placeholders(text):
            system_content = self.prompt_tr("prompt.proof.placeholder_system")
        else:
            system_content = self.prompt_tr("prompt.proof.system")

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt}
        ]

        result = self._call_api(messages)
        tokens = self.last_usage or {"prompt": 0, "completion": 0, "total": 0}
        log_api_call("proof", text, result or "", tokens, bool(result), latency_ms=self.last_latency_ms)
        if result is None:
            return None
        result = result.strip()
        cache.put(key, result, model=self.model)   # 成功结果写缓存（与查询同键）
        return result

    def translate(self, text: str, target_lang: str = "英文") -> str | None:
        if not text.strip():
            return None
        template = self._prompt_template("translate_prompt", "prompt.translate.default")
        if template is None:
            return None
        prompt = template.replace("{text}", text).replace("{target_lang}", target_lang)
        messages = [
            {"role": "system", "content": self.prompt_tr("prompt.translate.system")},
            {"role": "user", "content": prompt}
        ]
        result = self._call_api(messages)
        tokens = self.last_usage or {"prompt": 0, "completion": 0, "total": 0}
        log_api_call("translate", text, result or "", tokens, bool(result), latency_ms=self.last_latency_ms)
        return None if result is None else result.strip()

    def rewrite(self, text: str, style: str = "更流畅") -> str | None:
        if not text.strip():
            return None
        template = self._prompt_template("rewrite_prompt", "prompt.rewrite.default")
        if template is None:
            return None
        prompt = template.replace("{text}", text).replace("{style}", style)
        messages = [
            {"role": "system", "content": self.prompt_tr("prompt.rewrite.system")},
            {"role": "user", "content": prompt}
        ]
        result = self._call_api(messages)
        tokens = self.last_usage or {"prompt": 0, "completion": 0, "total": 0}
        log_api_call("rewrite", text, result or "", tokens, bool(result), latency_ms=self.last_latency_ms)
        return None if result is None else result.strip()

    def clipboard_proof(self, text: str) -> str | None:
        """兼容旧代码的剪贴板校对"""
        return self.proof_text(text)

    def _call_api(self, messages: list) -> str | None:
        """返回内容或 ``None``；任何失败都写入 ``last_error`` 并落日志（不再 print）。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.config.get("temperature", 0.4),
            "max_tokens": self.config.get("max_tokens", 2000),
            "thinking": {"type": "disabled"}   # 强制禁用思考
        }

        started = time.perf_counter()
        try:
            # 复用 Session：省掉每次请求的 DNS/TCP/TLS 握手（实测每次新建连接多 100~300ms）
            resp = _session().post(self.base_url, json=payload, headers=headers, timeout=30)
        except Exception as exc:  # noqa: BLE001  网络/SSL/代理都可能抛不同异常，统一转成失败信号
            self.last_usage = None
            self.last_api_success = False
            self.last_latency_ms = int((time.perf_counter() - started) * 1000)
            self.last_error = f"请求异常: {exc}"
            logger.warning("API 调用失败: %s", exc)
            return None
        self.last_latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code != 200:
            self.last_usage = None
            self.last_api_success = False
            self.last_error = f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"
            logger.warning("API 返回错误: %s", self.last_error)
            return None

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001  响应结构不符合预期同样算失败
            self.last_usage = None
            self.last_api_success = False
            self.last_error = f"响应格式异常: {exc}"
            logger.warning("API 响应无法解析: %s", exc)
            return None

        self.last_messages = messages
        self.last_usage = data.get("usage", None)
        self.last_api_success = True
        self.last_error = ""
        return content

    def test_connection(self) -> bool:
        return self._call_api([{"role": "user", "content": "ping"}]) is not None
