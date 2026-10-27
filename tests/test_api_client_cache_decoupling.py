# tests/test_api_cache_decoupling.py
"""缓存与日志的场景隔离：换提示词不再命中旧缓存；整段纠错不混进逐句校对日志。

分析发现（2026-10-27）：
1. `_cache_policy_tag()` 只用 `config[prompt_key]`，config 为空（用语言包默认）时指纹固定，
   改了默认提示词或切换提示词语言仍会命中老缓存；
2. `proof_text()` 无论 prompt_key 是 proof 还是 correct，日志 action 都写 "proof"
   —— 智能纠错页的监控因此混入了翻译改写页的数据。
"""
from __future__ import annotations

import uuid

import pytest


def _client(config_data=None, prompt_tr=None):
    from core.services.api_client import DeepSeekClient

    class _Config:
        def __init__(self):
            self.data = {"proof_prompt": "", "correct_prompt": "", "proof_language": "auto",
                         "correct_language": "auto"}
            self.data.update(config_data or {})

        def get(self, key, default=None):
            return self.data.get(key, default)

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return {
                "prompt.proof.default": "校对口令A：{text}",
                "prompt.correct.default": "纠错口令B：{text}",
                "prompt.proof.system": "SYS",
                "prompt.correct.system": "SYS",
                "prompt.proof.placeholder_system": "SYS",
            }.get(key, key)

    client = DeepSeekClient.__new__(DeepSeekClient)
    client.config = _Config()
    client.i18n = _I18n()
    client.last_error = ""
    client.model = "test-model"
    client.last_usage = {"prompt_tokens": 10, "completion_tokens": 2}
    client.last_latency_ms = 5
    client.last_api_success = True
    client._call_api = lambda messages: "结果"          # type: ignore[assignment]
    if prompt_tr:
        client.i18n = type("I", (), {"prompt_tr": staticmethod(prompt_tr)})()
    return client


def test_cache_tag_differs_per_prompt_scene():
    client = _client()

    proof_tag = client._cache_policy_tag("proof_prompt")
    correct_tag = client._cache_policy_tag("correct_prompt")

    assert proof_tag != correct_tag, "不同场景的提示词不能共用缓存指纹"


def test_cache_tag_changes_when_default_prompt_changes():
    first = _client()._cache_policy_tag("proof_prompt")
    second = _client(prompt_tr=lambda key, **kw: "改过的默认提示词 {text}"
                     if key == "prompt.proof.default" else key)._cache_policy_tag("proof_prompt")

    assert first != second, "改了默认提示词必须换缓存指纹，否则命中旧结论"


def test_cache_tag_changes_when_config_prompt_changes():
    first = _client({"proof_prompt": "自定义A {text}"})._cache_policy_tag("proof_prompt")
    second = _client({"proof_prompt": "自定义B {text}"})._cache_policy_tag("proof_prompt")

    assert first != second


def test_correct_calls_are_logged_as_correct(tmp_path, monkeypatch):
    from core.services import api_logger

    path = tmp_path / "api_calls.log"
    monkeypatch.setattr(api_logger, "LOG_FILE", path)
    client = _client()

    client.proof_text(f"整段文本{uuid.uuid4().hex}", prompt_key="correct_prompt",
                      cache_namespace="log-scene-test")

    assert '"action": "correct"' in path.read_text(encoding="utf-8")


def test_proof_calls_stay_proof(tmp_path, monkeypatch):
    from core.services import api_logger

    path = tmp_path / "api_calls.log"
    monkeypatch.setattr(api_logger, "LOG_FILE", path)
    client = _client()

    client.proof_text(f"单句{uuid.uuid4().hex}", prompt_key="proof_prompt",
                      cache_namespace="log-scene-test")

    assert '"action": "proof"' in path.read_text(encoding="utf-8")


def test_translate_and_rewrite_do_not_use_result_cache():
    """翻译/改写不查结果缓存 —— 提示词不同也不会互相串结果。"""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("src/core/services/api_client.py") \
        .read_text(encoding="utf-8")
    for method in ("def translate", "def rewrite"):
        start = source.index(method)
        end = source.find("\n    def ", start + 10)
        body = source[start:end if end > 0 else len(source)]
        assert "cache.get(" not in body, f"{method} 不该使用结果缓存"
