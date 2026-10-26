# tests/test_api_truncation.py
"""B04：响应被 max_tokens 截断（finish_reason=length）时不得当作成功结果，也不得写缓存。

审查报告用 HTTP 替身复现：200 + finish_reason=length 的半句输出被判成功，
应用时可能把原文后半段删掉。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def api_mod():
    from core.services import api_client

    return api_client


class _Reply:
    status_code = 200
    text = ""

    def __init__(self, content: str, finish_reason=None):
        self._content = content
        self._finish_reason = finish_reason

    def json(self):
        return {
            "choices": [{"message": {"content": self._content},
                         "finish_reason": self._finish_reason}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class _Session:
    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.calls += 1
        return self._reply


def _client(api_mod, monkeypatch, reply):
    from core.services.proof_cache import get_default_cache

    get_default_cache().clear()
    session = _Session(reply)
    monkeypatch.setattr(api_mod, "_session", lambda: session)
    monkeypatch.setattr(api_mod, "log_api_call", lambda *a, **k: None)

    class _Cfg(dict):
        def get_api_key(self):
            return "sk-test"

        def get(self, key, default=None):
            return dict.get(self, key, default)

    return api_mod.DeepSeekClient(_Cfg({"api_key": "sk-test"})), session


def test_truncated_response_is_failure(api_mod, monkeypatch):
    """finish_reason=length ⇒ 返回 None，并且 last_error 说明截断。"""
    client, _session_ = _client(api_mod, monkeypatch, _Reply("这是被截断的半", "length"))

    assert client.proof_text("这是一整句话。") is None, "截断结果不能当成功返回"
    assert client.last_api_success is False
    assert "截断" in client.last_error
    assert client.last_finish_reason == "length"


def test_truncated_response_is_not_cached(api_mod, monkeypatch):
    """截断结果不得进入缓存：再问一次必须重新请求。"""
    client, session = _client(api_mod, monkeypatch, _Reply("半句", "length"))

    client.proof_text("原句。")
    client.proof_text("原句。")
    assert session.calls == 2, "截断结果被缓存了（第二次没有重新请求）"


def test_normal_response_still_cached(api_mod, monkeypatch):
    """正常结果仍然缓存（不能因为修 B04 把缓存关掉）。"""
    client, session = _client(api_mod, monkeypatch, _Reply("正常结果", "stop"))

    assert client.proof_text("原句。") == "正常结果"
    assert client.proof_text("原句。") == "正常结果"
    assert session.calls == 1, "正常结果应命中缓存"
