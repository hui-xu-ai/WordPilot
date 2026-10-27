# tests/test_prompt_placeholder_optional.py
"""语言占位符是可选的：提示词里写死语言时，设置项不再起作用。

用户提问（2026-10-27）：「如果用户不改这个（语言设置），是不是直接改提示词也可以？」
答：可以。设置项只负责填充 `{lang}` / `{target_lang}`；提示词里没有占位符，替换就是空操作。
"""
from __future__ import annotations

import uuid

import pytest


def _client(prompt_template: str, config_data=None):
    from core.services.api_client import DeepSeekClient

    class _Config:
        def __init__(self):
            self.data = {"proof_language": "auto", "translate_language": "auto",
                         "proof_prompt": prompt_template,
                         "correct_prompt": prompt_template,
                         "translate_prompt": prompt_template}
            self.data.update(config_data or {})

        def get(self, key, default=None):
            return self.data.get(key, default)

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return key

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


def test_hardcoded_target_language_ignores_setting():
    """提示词里写死「日文」→ 配置的 auto 不会把目标语言改回中文/英文。"""
    client, sent = _client("把下面的文本翻译成日文，只输出译文：{text}",
                           {"translate_language": "auto"})

    client.translate(f"这是一段中文。{uuid.uuid4().hex}")

    assert "翻译成日文" in sent[-1]
    assert "英文" not in sent[-1]


def test_hardcoded_proof_language_ignores_setting():
    client, sent = _client("你是日语校对专家，只改错字：{text}", {"proof_language": "auto"})

    client.proof_text(f"テスト文。{uuid.uuid4().hex}", prompt_key="proof_prompt",
                      cache_namespace="hardcoded-lang")

    assert "日语校对专家" in sent[-1]
    assert "中文" not in sent[-1]


def test_placeholder_still_honours_setting():
    client, sent = _client("翻译为{target_lang}：{text}", {"translate_language": "法文"})

    client.translate(f"这是一段中文。{uuid.uuid4().hex}")

    assert "法文" in sent[-1]


@pytest.mark.parametrize("plugin,key", [("clipboard", "settings.prompt.translatelang_tip"),
                                        ("proofread", "settings.prompt.prooflang_tip")])
def test_tooltip_explains_placeholder_only(plugin, key):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / f"src/plugins/{plugin}/i18n/zh_CN.json").read_text(encoding="utf-8"))
    tip = data[key]

    assert "{target_lang}" in tip or "{lang}" in tip, tip
    assert ("提示词里保留" in tip) or ("Only used when" in tip), tip
