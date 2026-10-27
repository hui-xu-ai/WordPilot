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

#: 改写强度短标签 → i18n key（真实指令写在语言包里，随提示词语言切换）
_MODE_KEYS = {"轻": "light", "中": "medium", "强": "strong"}

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
        self.base_url = self._resolve_base_url(config)
        self.last_messages = None
        self.last_usage = None
        self.last_api_success = False
        self.last_finish_reason: str | None = None
        self.last_error = ""
        #: 最近一次真实网络往返耗时（ms）；命中本地缓存时不更新
        self.last_latency_ms = 0
        self._warm_up_connection()

    @staticmethod
    def _resolve_base_url(config) -> str:
        """B07：配置里的 api_endpoint 必须生效（此前固定官方 URL，形同摆设）。

        支持"主机根"与"完整 chat/completions 路径"两种写法。
        """
        default = "https://api.deepseek.com/v1/chat/completions"
        try:
            raw = (config.get("api_endpoint", "") or "").strip()
        except Exception:                      # noqa: BLE001
            return default
        if not raw:
            return default
        if not raw.lower().startswith(("http://", "https://")):
            return default                     # 非法 scheme 回退，避免把 key 发到奇怪地方
        if raw.rstrip("/").endswith("/chat/completions"):
            return raw
        return raw.rstrip("/") + "/v1/chat/completions"

    def _cache_policy_tag(self, prompt_key: str) -> str:
        """B05：缓存键带上模型 + 该场景的**生效**提示词指纹，换策略后不复用旧结果。

        注意：必须用真正发出去的模板（config 为空时是语言包默认），否则改了默认提示词或
        切换提示词语言，老缓存仍会被命中 —— 那等于用旧提示词的结论冒充新提示词的结果。
        """
        import hashlib

        try:
            template = self._prompt_template(
                prompt_key, f"prompt.{prompt_key.removesuffix('_prompt')}.default") or ""
        except Exception:                      # noqa: BLE001
            template = ""
        digest = hashlib.sha1(
            f"{self.model}|{prompt_key}|{template.strip()}".encode("utf-8"))
        return f"{self.model or 'unknown'}#{digest.hexdigest()[:10]}"

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

    # ---------------- 语言 ----------------
    @staticmethod
    def detect_language(text: str) -> str:
        """粗判文本语言：中日韩字符占比高 → 中文，否则英文。

        只用于给提示词的 `{lang}` 占位符填值，不需要很准 —— 用户也可以在设置里直接指定。
        """
        sample = (text or "")[:2000]
        if not sample.strip():
            return "中文"
        cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
        return "中文" if cjk / max(1, len(sample)) >= 0.15 else "English"

    def target_language(self, text: str, prompt_key: str = "proof_prompt") -> str:
        """{lang} 的值：文本语言。

        逐句校对用 `proof_language`（智能纠错插件的设置）；
        整段纠错用 `correct_language`（翻译改写插件的设置）—— 两者独立，互不影响。
        值为 auto 时按待处理文本自动识别。
        """
        key = "correct_language" if prompt_key == "correct_prompt" else "proof_language"
        configured = (self.config.get(key) or "auto").strip()
        if configured and configured.lower() != "auto":
            return configured
        return self.detect_language(text)

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

    def proof_text(self, text: str, cache_key: str | None = None,
                   prompt_key: str = "proof_prompt",
                   cache_namespace: str = "") -> str | None:
        """校对文本；失败返回 ``None``，原因见 ``last_error``。

        cache_key：缓存键。proof_sentence 以 body_text（去尾部的正文）为键调用，
        与直接以原文为键的剪贴板校对共用同一份 SQLite 缓存；
        不传时默认以 text 本身为键。

        prompt_key / cache_namespace：用于**按调用场景解耦提示词**（例如翻译改写页的"纠错"
        用整段文本提示词，桌宠逐句校对用句子提示词）。缓存键加命名空间前缀，
        避免"同一段文本、不同提示词"互相命中错误结果。
        """
        key = cache_key if cache_key is not None else text
        if cache_namespace:
            key = f"{cache_namespace}:{key}"
        # B05：只有文本 + 场景是不够的——换模型或改提示词后仍会命中旧结果。
        # 把模型与提示词指纹并进键里，策略一变就自然不复用。
        key = f"{self._cache_policy_tag(prompt_key)}:{key}"
        cache = get_default_cache()
        cached = cache.get(key)
        if cached is not None:
            return cached.strip()

        template = (self.config.get(prompt_key) or "").strip()
        # **各场景各自独立**：以前 `correct_prompt` 为空会退回 `proof_prompt`，导致
        # 翻译改写页的"纠错"和智能纠错插件共用一个提示词（用户明确要求解耦）。
        if not template:
            # 默认提示词走「提示词语言」，与界面语言互不影响；
            # 整段纠错有自己的默认文案（强调保持段落结构、不改写）
            default_key = ("prompt.correct.default" if prompt_key == "correct_prompt"
                           else "prompt.proof.default")
            template = self.prompt_tr(default_key)
        if "{text}" not in template:
            template += "\n{text}"
        language = self.target_language(text, prompt_key)
        prompt = (template.replace("{lang}", language)
                  .replace("{language}", language)
                  .replace("{text}", text))

        # system 消息也按场景分开：整段纠错与逐句校对的输出要求不同
        if prompt_key == "correct_prompt":
            system_content = self.prompt_tr("prompt.correct.system")
        elif self._has_placeholders(text):
            system_content = self.prompt_tr("prompt.proof.placeholder_system")
        else:
            system_content = self.prompt_tr("prompt.proof.system")

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt}
        ]

        result = self._call_api(messages)
        tokens = self.last_usage or {"prompt": 0, "completion": 0, "total": 0}
        action = "correct" if prompt_key == "correct_prompt" else "proof"
        log_api_call(action, text, result or "", tokens, bool(result),
                     latency_ms=self.last_latency_ms)
        if result is None:
            return None
        result = result.strip()
        cache.put(key, result, model=self.model)   # 成功结果写缓存（与查询同键）
        return result

    def translate(self, text: str, target_lang: str | None = None) -> str | None:
        """翻译文本。

        `target_lang` 是这个提示词里的 `{target_lang}`（**目标语言**，不是自动识别）：
        不传时读配置 `translate_language`；配置为 auto 表示"中→英、其他→中"的自动互译。
        """
        if not text.strip():
            return None
        target_lang = target_lang or (self.config.get("translate_language") or "auto")
        if target_lang.strip().lower() in {"auto", "自动", ""}:
            source = self.detect_language(text)
            target_lang = "英文" if source == "中文" else "中文"
        template = self._prompt_template("translate_prompt", "prompt.translate.default")
        if template is None:
            return None
        prompt = (template.replace("{lang}", self.detect_language(text))
                  .replace("{language}", self.detect_language(text))
                  .replace("{target_lang}", target_lang)
                  .replace("{text}", text))
        messages = [
            {"role": "system", "content": self.prompt_tr("prompt.translate.system")},
            {"role": "user", "content": prompt}
        ]
        result = self._call_api(messages)
        tokens = self.last_usage or {"prompt": 0, "completion": 0, "total": 0}
        log_api_call("translate", text, result or "", tokens, bool(result), latency_ms=self.last_latency_ms)
        return None if result is None else result.strip()

    def _rewrite_mode(self, value: str) -> str:
        """把「轻/中/强」这类短标签展开成**具体改写指令**（模型不需要猜强度是什么）。

        自定义值原样使用 —— 用户想自己写一段要求，也直接可用。
        """
        key = (value or "").strip()
        mapping = {
            "轻": "轻度：只替换用词与标点，句序与段落顺序完全不动",
            "中": "中度：可拆分或合并句子、调整句内语序，段落顺序不动",
            "强": "深度：可重构段落内部的句子顺序与叙述视角，技术内容与结论不变",
        }
        if key in mapping:
            translated = self.prompt_tr(f"prompt.rewrite.mode.{_MODE_KEYS[key]}")
            return translated if translated and not translated.startswith("prompt.") else mapping[key]
        return key

    def rewrite(self, text: str, style: str | None = None,
                strength: str | None = None) -> str | None:
        if not text.strip():
            return None
        template = self._prompt_template("rewrite_prompt", "prompt.rewrite.default")
        if template is None:
            return None
        level = self._rewrite_mode(strength or style
                                   or self.config.get("rewrite_strength") or "中")
        prompt = (template.replace("{strength}", level).replace("{style}", level)
                  .replace("{text}", text))
        messages = [
            {"role": "system", "content": self.prompt_tr("prompt.rewrite.system")},
            {"role": "user", "content": prompt}
        ]
        result = self._call_api(messages)
        tokens = self.last_usage or {"prompt": 0, "completion": 0, "total": 0}
        log_api_call("rewrite", text, result or "", tokens, bool(result), latency_ms=self.last_latency_ms)
        return None if result is None else result.strip()

    def clipboard_proof(self, text: str) -> str | None:
        """翻译改写页的「纠错」：整段文本 + 独立提示词（`correct_prompt`）+ 独立缓存命名空间。

        历史实现直接转调 `proof_text()`，与桌宠逐句校对共用同一套提示词与缓存：
        两处需求不同（整段 vs 单句），改一处会互相影响，而且同文本会命中对方的缓存结果。
        """
        return self.proof_text(text, prompt_key="correct_prompt",
                               cache_namespace="correct")

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
            choice = data["choices"][0]
            content = choice["message"]["content"].strip()
            finish_reason = choice.get("finish_reason")
        except Exception as exc:  # noqa: BLE001  响应结构不符合预期同样算失败
            self.last_usage = None
            self.last_api_success = False
            self.last_error = f"响应格式异常: {exc}"
            logger.warning("API 响应无法解析: %s", exc)
            return None

        self.last_finish_reason = finish_reason
        if finish_reason == "length":
            # B04：被 max_tokens 截断的半句绝不能当成完整修改（会删掉原文后半段）
            self.last_usage = data.get("usage", None)
            self.last_api_success = False
            self.last_error = "响应被截断（finish_reason=length），未采用"
            logger.warning("API 响应被截断，长度上限 max_tokens 可能过小")
            return None

        self.last_messages = messages
        self.last_usage = data.get("usage", None)
        self.last_api_success = True
        self.last_error = ""
        return content

    def test_connection(self) -> bool:
        return self._call_api([{"role": "user", "content": "ping"}]) is not None
