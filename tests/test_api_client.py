# tests/test_api_client.py
"""P10-B1：`DeepSeekClient` 的返回值契约。

失败必须返回 ``None``（原因进 ``last_error``），**绝不把错误文案当内容返回**——
历史实现返回「API 调用失败」，被 word_monitor 当成校对结果做 diff、被剪贴板直接写回。
"""
from __future__ import annotations

from core.services import api_client as api_mod


class _FakeSession:
    """替换 `api_mod._session()`：客户端改为复用 requests.Session（keep-alive）。"""

    def __init__(self, post):
        self.post = post


def test_proof_text_returns_none_when_api_fails(api_client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(api_mod, "_session", lambda: _FakeSession(boom))
    assert api_client.proof_text("原句") is None
    assert api_client.last_error


def test_proof_text_never_returns_error_message(api_client, monkeypatch):
    monkeypatch.setattr(api_mod, "_session", lambda: _FakeSession(
        lambda *a, **k: type("R", (), {"status_code": 500, "text": "boom"})()))
    result = api_client.proof_text("原句")
    assert result is None, f"错误文案不得当内容返回，实际={result!r}"
    assert "500" in api_client.last_error


def test_translate_uses_language_pack_default_when_config_blank(api_client, monkeypatch):
    """config 里 translate_prompt 恒存在（setdefault 空串）⇒ 必须 or 兜底到语言包默认文案。"""
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["prompt"] = json["messages"][-1]["content"]
        return type("R", (), {"status_code": 200,
                              "json": lambda self=None: {"choices": [{"message": {"content": "译文"}}]}})()

    monkeypatch.setattr(api_mod, "_session", lambda: _FakeSession(fake_post))
    assert api_client.translate("原文") == "译文"
    assert "原文" in seen["prompt"]


def test_translate_empty_input_returns_none(api_client):
    assert api_client.translate("   ") is None


def test_test_connection_false_on_failure(api_client, monkeypatch):
    monkeypatch.setattr(api_mod.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert api_client.test_connection() is False
