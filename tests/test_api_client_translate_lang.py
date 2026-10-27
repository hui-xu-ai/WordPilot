# tests/test_prompt_translate_lang.py
"""翻译提示词里的 {lang} 必须被替换（否则模型会看到字面量 {lang}）。"""
from __future__ import annotations


def _client(template: str):
    from core.services.api_client import DeepSeekClient

    class _Config:
        def __init__(self):
            self._data = {"translate_prompt": template, "proof_language": "auto"}

        def get(self, key, default=None):
            return self._data.get(key, default)

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return f"<{key}>"

    client = DeepSeekClient.__new__(DeepSeekClient)
    client.config = _Config()
    client.i18n = _I18n()
    client.last_error = ""
    client.model = "test"
    client.last_usage = {"prompt": 0, "completion": 0, "total": 0}
    client.last_latency_ms = 0
    seen: list[str] = []

    def _capture(messages):
        seen.append(messages[1]["content"])
        return "译文"

    client._call_api = _capture            # type: ignore[assignment]
    return client, seen


def test_translate_fills_lang_placeholder():
    client, seen = _client("将以下{lang}文本翻译为{target_lang}：\n{text}")

    client.translate("这是一段中文。", target_lang="English")

    assert seen, "应当已发出请求"
    assert "{lang}" not in seen[-1], f"占位符未被替换：{seen[-1][:40]}"
    assert "中文" in seen[-1] and "English" in seen[-1]


def test_translate_english_source_detected():
    client, seen = _client("将以下{lang}文本翻译为{target_lang}：\n{text}")

    client.translate("This is an English paragraph.", target_lang="中文")

    assert "English" in seen[-1]
