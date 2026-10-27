# tests/test_prompt_modes_distinct.py
"""逐句校对（智能纠错）与整段纠错（翻译改写页）必须是**两套不同**的提示词。

用户现场：两个编辑框里的文字看起来像复制粘贴 —— 要求两者职责明显不同。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _i18n(locale: str) -> dict:
    return json.loads((ROOT / f"src/assets/i18n/{locale}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_proof_and_correct_prompts_are_different(locale):
    data = _i18n(locale)
    proof = data["prompt.proof.default"]
    correct = data["prompt.correct.default"]

    assert proof != correct
    assert proof.split("\n")[0] != correct.split("\n")[0], "首句不该一样（看起来就是复制）"
    # 逐句校对强调单句；整段纠错强调全文一致性与跨句
    assert ("一句" in proof or "one sentence" in proof.lower())
    assert ("整段" in correct or "whole-passage" in correct.lower()
            or "full paragraph" in correct.lower())
    assert ("一致性" in correct or "consistency" in correct.lower())


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_correct_mode_has_its_own_system_message(locale):
    data = _i18n(locale)

    assert "prompt.correct.system" in data
    assert data["prompt.correct.system"] != data["prompt.proof.system"]
    assert "[IMG0]" in data["prompt.correct.system"], "整段模式必须提醒保留占位符"


def test_api_uses_correct_system_message():
    from core.services.api_client import DeepSeekClient

    class _Config:
        def get(self, key, default=None):
            return {"proof_language": "auto", "correct_prompt": ""}.get(key, default)

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return {
                "prompt.correct.default": "纠错：{text}",
                "prompt.correct.system": "CORRECT-SYSTEM",
                "prompt.proof.default": "校对：{text}",
                "prompt.proof.system": "PROOF-SYSTEM",
                "prompt.proof.placeholder_system": "PROOF-PLACEHOLDER",
            }.get(key, key)

    client = DeepSeekClient.__new__(DeepSeekClient)
    client.config = _Config()
    client.i18n = _I18n()
    client.last_error = ""
    client.model = "test"
    client.last_usage = {"prompt": 0, "completion": 0, "total": 0}
    client.last_latency_ms = 0
    seen: list[list[dict]] = []

    def _capture(messages):
        seen.append(messages)
        return "ok"

    client._call_api = _capture                 # type: ignore[assignment]

    import uuid

    client.proof_text(f"整段文本 {uuid.uuid4().hex}", prompt_key="correct_prompt",
                      cache_namespace="mode-test")

    assert seen and seen[-1][0]["content"] == "CORRECT-SYSTEM"
