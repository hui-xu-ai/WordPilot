# tests/test_rewrite_strength_and_lang_inputs.py
"""改写强度 `{strength}` 与纠错语言 `{lang}` 必须有界面入口，且各自独立。

用户反馈（2026-10-27）：提示词里写了「请按『{strength}』强度」「你是{lang}…」，
但界面上没有对应输入项 —— 占位符没地方填。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _client(config_data=None):
    from core.services.api_client import DeepSeekClient

    class _Config:
        def __init__(self):
            self.data = {
                "rewrite_prompt": "按{strength}强度改写：{text}",
                "correct_prompt": "纠错 {lang}：{text}",
                "proof_prompt": "校对 {lang}：{text}",
                "translate_prompt": "翻译为{target_lang}：{text}",
                "rewrite_strength": "强",
                "correct_language": "English",
                "proof_language": "中文",
                "translate_language": "auto",
            }
            self.data.update(config_data or {})

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value):
            self.data[key] = value

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return {"prompt.rewrite.system": "SYS", "prompt.correct.system": "SYS",
                    "prompt.proof.system": "SYS",
                    "prompt.proof.placeholder_system": "SYS"}.get(key, key)

    client = DeepSeekClient.__new__(DeepSeekClient)
    client.config = _Config()
    client.i18n = _I18n()
    client.last_error = ""
    client.model = "test"
    client.last_usage = {"prompt": 0, "completion": 0, "total": 0}
    client.last_latency_ms = 0
    sent: list[str] = []

    def _capture(messages):
        sent.append(messages[1]["content"])
        return "结果"

    client._call_api = _capture                  # type: ignore[assignment]
    return client, sent


def test_rewrite_reads_strength_from_config():
    client, sent = _client()

    client.rewrite(f"这是一段中文。{uuid.uuid4().hex}")

    assert "深度" in sent[-1], f"强度应展开成具体指令：{sent[-1]}"


def test_rewrite_explicit_strength_wins():
    client, sent = _client({"rewrite_strength": "强"})

    client.rewrite(f"这是一段中文。{uuid.uuid4().hex}", strength="轻")

    assert "轻度" in sent[-1]


def test_correct_language_is_independent_of_proof_language():
    client, sent = _client({"correct_language": "English", "proof_language": "中文"})

    client.proof_text(f"某段文本{uuid.uuid4().hex}", prompt_key="correct_prompt",
                      cache_namespace="lang-input-test")
    client.proof_text(f"某段文本{uuid.uuid4().hex}", prompt_key="proof_prompt",
                      cache_namespace="lang-input-test")

    assert "纠错 English" in sent[0], sent[0]
    assert "校对 中文" in sent[1], sent[1]


def test_clipboard_page_has_strength_and_correction_language_controls():
    text = (ROOT / "src/plugins/clipboard/plugin.py").read_text(encoding="utf-8")

    assert "rewrite_strength_edit" in text
    assert "correct_language_edit" in text
    assert '"rewrite_strength": self.rewrite_strength_edit.current_value()' in text
    assert '"correct_language": self.correct_language_edit.current_value()' in text


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_plugin_copy_mentions_both_placeholders(locale):
    data = json.loads((ROOT / f"src/plugins/clipboard/i18n/{locale}.json").read_text(encoding="utf-8"))

    assert "{strength}" in data["settings.prompt.strength"]
    assert "{lang}" in data["settings.prompt.correclang"]
    assert data["settings.prompt.strength_light"] and data["settings.prompt.strength_strong"]


def test_config_defaults_exist(config_env):
    from core.services.config_manager import ConfigManager

    manager = ConfigManager()

    assert manager.get("rewrite_strength") == "中"
    assert manager.get("correct_language") == "auto"
