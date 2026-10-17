# api_client.py
import re  # 新增用于占位符检测
from pathlib import Path

import requests

from core.services.api_logger import log_api_call
from core.services.proof_cache import get_default_cache

LOG_FILE = Path(__file__).parent / "api_calls.log"

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

    # 新增辅助方法：判断文本是否包含占位符(如[IMG0]、[EQ1])
    def _has_placeholders(self, text: str) -> bool:
        return bool(re.search(r'\[(IMG|EQ)\d+\]', text))

    def prompt_tr(self, key: str) -> str:
        """按「提示词语言」取默认提示词；服务缺失时回退到 key 本身。"""
        if self.i18n is None:
            return key
        return self.i18n.prompt_tr(key)

    def proof_text(self, text: str, cache_key: str | None = None) -> str:
        """校对文本。

        cache_key：缓存键。proof_sentence 以 body_text（去尾部的正文）为键调用，
        与直接以原文为键的剪贴板校对共用同一份 SQLite 缓存；
        不传时默认以 text 本身为键。
        """
        key = cache_key if cache_key is not None else text
        cache = get_default_cache()
        cached = cache.get(key)
        if cached is not None:
            return cached.strip()

        template = self.config.get("proof_prompt", "").strip()
        if not template:
            # 默认提示词走「提示词语言」，与界面语言互不影响
            template = self.prompt_tr("prompt.proof.default")
        else:
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

        ero_mes = self.prompt_tr("prompt.error")
        tokens = {"prompt": 0, "completion": 0, "total": 0}
        if hasattr(self, 'last_usage') and self.last_usage:
            tokens = self.last_usage
        log_api_call("proof", text, result, tokens, bool(result))
        if not result:
            return ero_mes
        result = result.strip()
        cache.put(key, result, model=self.model)   # 成功结果写缓存（与查询同键）
        return result

    def translate(self, text: str, target_lang: str = "英文") -> str:
        template = self.config.get("translate_prompt",
            self.prompt_tr("prompt.translate.default"))
        if "{text}" not in template:
            print("❌ 翻译提示词缺少 {text}")
            return ""
        if not text.strip():
            return ""
        prompt = template.replace("{text}", text).replace("{target_lang}", target_lang)
        messages = [
            {"role": "system", "content": self.prompt_tr("prompt.translate.system")},
            {"role": "user", "content": prompt}
        ]
        result = self._call_api(messages)
        ero_mes = self.prompt_tr("prompt.error")
        tokens = {"prompt": 0, "completion": 0, "total": 0}
        if hasattr(self, 'last_usage') and self.last_usage:
            tokens = self.last_usage
        log_api_call("translate", text, result, tokens, bool(result))
        return result if result else ero_mes

    def rewrite(self, text: str, style: str = "更流畅") -> str:
        template = self.config.get("rewrite_prompt",
            self.prompt_tr("prompt.rewrite.default"))
        if "{text}" not in template:
            print("❌ 改写提示词缺少 {text}")
            return ""
        if not text.strip():
            return ""
        prompt = template.replace("{text}", text).replace("{style}", style)
        messages = [
            {"role": "system", "content": self.prompt_tr("prompt.rewrite.system")},
            {"role": "user", "content": prompt}
        ]
        result = self._call_api(messages)
        ero_mes = self.prompt_tr("prompt.error")
        tokens = {"prompt": 0, "completion": 0, "total": 0}
        if hasattr(self, 'last_usage') and self.last_usage:
            tokens = self.last_usage
        log_api_call("rewrite", text, result, tokens, bool(result))
        return result if result else ero_mes

    def clipboard_proof(self, text: str) -> str:
        """兼容旧代码的剪贴板校对"""
        return self.proof_text(text)

    def _call_api(self, messages: list) -> str:
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

        try:
            resp = requests.post(self.base_url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                self.last_messages = messages
                self.last_usage = data.get("usage", None)
                self.last_api_success = True
                return content
            else:
                print(f"API error {resp.status_code}: {resp.text}")
                self.last_usage = None
                self.last_api_success = False
                return ""
        except Exception as e:
            print(f"API call failed: {e}")
            self.last_usage = None
            self.last_api_success = False
            return ""

    def test_connection(self) -> bool:
        return bool(self._call_api([{"role": "user", "content": "ping"}]))