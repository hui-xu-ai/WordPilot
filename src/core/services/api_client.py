# api_client.py
import re  # 新增用于占位符检测
from pathlib import Path

import requests

from core.services.api_logger import log_api_call, get_cached_suggestion

LOG_FILE = Path(__file__).parent / "api_calls.log"

class DeepSeekClient:
    def __init__(self, config):
        self.config = config
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "deepseek-v4-flash")
        self.base_url = "https://api.deepseek.com/v1/chat/completions"
        self.last_messages = None
        self.last_usage = None
        self.last_api_success = False

    # 新增辅助方法：判断文本是否包含占位符(如[IMG0]、[EQ1])
    def _has_placeholders(self, text: str) -> bool:
        return bool(re.search(r'\[(IMG|EQ)\d+\]', text))

    def proof_text(self, text: str) -> str:
        # 查询缓存
        num = self.config.get("proof_cache_lookup_count", 50)
        cached = get_cached_suggestion(text, num)
        if cached is not None:
            return cached.strip()

        template = self.config.get("proof_prompt", "").strip()
        if not template:
            template = (
                "你是中文科技论文校对专家。修正错别字、语病、标点、专业词汇拼写。校对所有单位的空格：数字与℃、%、°之间不加空格（例：2℃、5%、9°）；数字与其他单位（如m）之间加半角空格（例：10 m）。\n"
                "原文：{text}"
            )
        else:
            if "{text}" not in template:
                template += "\n{text}"
        prompt = template.replace("{text}", text)

        # 根据是否含有占位符，动态设置 system 消息，减少无占位符时的 token 消耗
        if self._has_placeholders(text):
            system_content = (
                "只输出完整校对结果，不解释、不提问。"
                "如果输入中有类似[IMG0]或[EQ1]的标记，它们是内嵌对象占位符，必须原样保留，不得修改。"
            )
        else:
            system_content = "只输出完整校对结果，不解释、不提问。"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt}
        ]

        result = self._call_api(messages)

        ero_mes = "出错了，请检查 API 设置和网络连接问题！"
        tokens = {"prompt": 0, "completion": 0, "total": 0}
        if hasattr(self, 'last_usage') and self.last_usage:
            tokens = self.last_usage
        log_api_call("proof", text, result, tokens, bool(result))
        if not result:
            return ero_mes
        return result.strip()

    def translate(self, text: str, target_lang: str = "英文") -> str:
        template = self.config.get("translate_prompt",
            "将以下文本翻译为{target_lang}，只输出译文：\n{text}")
        if "{text}" not in template:
            print("❌ 翻译提示词缺少 {text}")
            return ""
        if not text.strip():
            return ""
        prompt = template.replace("{text}", text).replace("{target_lang}", target_lang)
        messages = [
            {"role": "system", "content": "你是翻译机器人，只返回译文，不解释、不闲聊。"},
            {"role": "user", "content": prompt}
        ]
        result = self._call_api(messages)
        ero_mes = "出错了，请检查 API 设置和网络连接问题！"
        tokens = {"prompt": 0, "completion": 0, "total": 0}
        if hasattr(self, 'last_usage') and self.last_usage:
            tokens = self.last_usage
        log_api_call("translate", text, result, tokens, bool(result))
        return result if result else ero_mes

    def rewrite(self, text: str, style: str = "更流畅") -> str:
        template = self.config.get("rewrite_prompt",
            "按{style}风格改写以下文本，只输出改写后的内容：\n{text}")
        if "{text}" not in template:
            print("❌ 改写提示词缺少 {text}")
            return ""
        if not text.strip():
            return ""
        prompt = template.replace("{text}", text).replace("{style}", style)
        messages = [
            {"role": "system", "content": "你是文本改写机器人。严禁聊天、提问或添加额外文字。直接给出改写结果。"},
            {"role": "user", "content": prompt}
        ]
        result = self._call_api(messages)
        ero_mes = "出错了，请检查 API 设置和网络连接问题！"
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