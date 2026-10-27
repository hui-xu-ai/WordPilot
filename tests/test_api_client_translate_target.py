# tests/test_api_client_translate_target.py
"""`{target_lang}` 是**目标语言**：不传时读配置，auto 表示中↔英自动互译。

现场（用户提问）：「{target_lang} 是程序自动识别目标语言吗？」——不是；
而且旧实现里插件调用 `api.translate(text)` 没传参，默认写死"英文"，翻译永远只出英文。
"""
from __future__ import annotations

import uuid

import pytest


def _client(config_data=None):
    from core.services.api_client import DeepSeekClient

    class _Config:
        def __init__(self):
            self.data = {"proof_language": "auto", "translate_prompt": "翻译为{target_lang}：{text}"}
            self.data.update(config_data or {})

        def get(self, key, default=None):
            return self.data.get(key, default)

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return {"prompt.translate.system": "SYS"}.get(key, key)

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

    client._call_api = _capture                  # type: ignore[assignment]
    return client, seen


def test_auto_translates_chinese_into_english():
    client, seen = _client()

    client.translate(f"这是一段中文。{uuid.uuid4().hex}")

    assert "英文" in seen[-1], seen[-1]


def test_auto_translates_english_into_chinese():
    client, seen = _client()

    client.translate(f"This is an English paragraph. {uuid.uuid4().hex}")

    assert "中文" in seen[-1], seen[-1]


def test_explicit_target_from_config_is_honoured():
    client, seen = _client({"translate_language": "日文"})

    client.translate(f"这是一段中文。{uuid.uuid4().hex}")

    assert "日文" in seen[-1] and "英文" not in seen[-1]


def test_explicit_argument_overrides_config():
    client, seen = _client({"translate_language": "日文"})

    client.translate(f"这是一段中文。{uuid.uuid4().hex}", target_lang="法文")

    assert "法文" in seen[-1]


def test_config_registers_translate_language_default(config_env):
    """默认值在 `ConfigManager` 加载时用 setdefault 注册（隔离到 tmp_path，不碰真实配置）。"""
    from core.services.config_manager import ConfigManager

    manager = ConfigManager()

    assert manager.get("translate_language") == "auto"
    assert manager.get("proof_language") == "auto"
