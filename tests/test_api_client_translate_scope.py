# tests/test_api_client_translate_scope.py
"""`translate_language` 只影响翻译；改写与纠错各用各的配置。

用户提问（2026-10-27）：「这个键值是只影响翻译框，还是影响改写和纠错？」
答：只影响翻译。改写不用 `{target_lang}`；纠错用校对语言 `{lang}`/`proof_language`。
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _client():
    from core.services.api_client import DeepSeekClient

    class _Config:
        def __init__(self):
            self.data = {
                "translate_language": "日文",          # 只有翻译该受影响
                "proof_language": "auto",              # 纠错/校对用它
                "translate_prompt": "翻译为{target_lang}：{text}",
                "rewrite_prompt": "改写（不要日语）：{text}",
                "correct_prompt": "纠错 {lang}：{text}",
            }

        def get(self, key, default=None):
            return self.data.get(key, default)

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return {
                "prompt.translate.system": "SYS-T",
                "prompt.rewrite.system": "SYS-R",
                "prompt.correct.system": "SYS-C",
                "prompt.proof.system": "SYS-P",
                "prompt.proof.placeholder_system": "SYS-P",
            }.get(key, key)

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

    client._call_api = _capture                   # type: ignore[assignment]
    return client, sent


def test_translate_uses_translate_language():
    client, sent = _client()

    client.translate(f"这是一段中文。{uuid.uuid4().hex}")

    assert "日文" in sent[-1]


def test_rewrite_never_reads_translate_language():
    client, sent = _client()

    client.rewrite(f"这是一段中文。{uuid.uuid4().hex}")

    assert "日文" not in sent[-1], "改写不该受翻译目标语言影响"


def test_correct_uses_proof_language_not_translate_language():
    client, sent = _client()

    client.proof_text(f"这是一段中文。{uuid.uuid4().hex}", prompt_key="correct_prompt",
                      cache_namespace="scope-test")

    assert "日文" not in sent[-1], "纠错用的是校对语言，不是翻译目标语言"
    assert "中文" in sent[-1]


def test_only_translate_reads_the_key():
    """静态守卫：`translate_language` 只允许出现在 translate() 里。"""
    source = (ROOT / "src/core/services/api_client.py").read_text(encoding="utf-8")
    matches = [line for line in source.splitlines() if "translate_language" in line]

    assert matches, "应至少有一处读取"
    assert all("translate" in line.lower() or "config.get" in line for line in matches)


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_ui_copy_states_scope(locale):
    data = json.loads((ROOT / f"src/plugins/clipboard/i18n/{locale}.json").read_text(encoding="utf-8"))

    title = data["settings.prompt.translatelang"]
    tip = data["settings.prompt.translatelang_tip"]
    if locale == "zh_CN":
        assert "只影响翻译" in title
        assert "改写" in tip and "纠错" in tip
    else:
        assert "translation only" in title.lower()
        assert "rewrite" in tip.lower() and "correction" in tip.lower()
    assert re.search(r"\{target_lang\}", tip)
