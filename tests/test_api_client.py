# tests/test_api_client.py
"""P10-B1：`DeepSeekClient` 的返回值契约。

失败必须返回 ``None``（原因进 ``last_error``），**绝不把错误文案当内容返回**——
历史实现返回「API 调用失败」，被 word_monitor 当成校对结果做 diff、被剪贴板直接写回。
"""
from __future__ import annotations

import pytest

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


# ── 自定义提示词是否真的生效（原 test_prompt_config.py，按 G5 归到本模块）───────────

class _RecorderSession:
    """记录请求体的假 Session（与上面的 _FakeSession 区分：那个只包一个 post 回调）。"""

    def __init__(self, reply: str = "结果"):
        self.payloads: list[dict] = []
        self.reply = reply

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - 模拟 requests
        self.payloads.append(json or {})
        return _Reply(self.reply)


class _Reply:
    """最小可用的 requests 响应替身（客户端只读 status_code / json() / text）。"""

    status_code = 200
    text = ""

    def __init__(self, content: str):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


@pytest.fixture
def session(monkeypatch):
    fake = _RecorderSession()
    monkeypatch.setattr(api_mod, "_session", lambda: fake)
    monkeypatch.setattr(api_mod, "log_api_call", lambda *a, **k: None)
    return fake


def _client(**values):
    from core.services.proof_cache import get_default_cache

    get_default_cache().clear()

    class _Cfg(dict):
        def get_api_key(self):
            return self.get("api_key", "sk-test")

        def get(self, key, default=None):
            return dict.get(self, key, default)

    return api_mod.DeepSeekClient(_Cfg(values))


def test_custom_proof_prompt_is_sent(session):
    client = _client(proof_prompt="请只改错别字：{text}")
    client.proof_text("原句")

    content = session.payloads[-1]["messages"][-1]["content"]
    assert "请只改错别字" in content, f"配置的校对提示词没进请求体：{content!r}"
    assert "原句" in content, "占位符 {text} 必须被替换成正文"


def test_custom_translate_prompt_is_sent(session):
    client = _client(translate_prompt="译成{target_lang}：{text}")
    client.translate("原文")

    content = session.payloads[-1]["messages"][-1]["content"]
    assert "译成" in content
    assert "原文" in content


def test_custom_rewrite_prompt_is_sent(session):
    client = _client(rewrite_prompt="改写为{style}：{text}")
    client.rewrite("原文")

    content = session.payloads[-1]["messages"][-1]["content"]
    assert "改写为" in content
    assert "原文" in content


def test_blank_prompt_falls_back_to_language_pack(session):
    """配置里是空串（设置页 setdefault 会写空串）时必须回退默认文案，不能发空提示。"""
    client = _client(proof_prompt="   ", translate_prompt="")
    client.proof_text("原句")

    content = session.payloads[-1]["messages"][-1]["content"]
    assert "原句" in content
    assert content.strip() != "原句", "回退失败：只发了正文，没有提示词"


def test_clipboard_proof_uses_its_own_prompt_and_cache(session):
    """翻译改写页的「纠错」必须用 correct_prompt，且缓存键带命名空间（不与逐句校对串味）。"""
    client = _client(proof_prompt="逐句校对：{text}", correct_prompt="整段纠错：{text}")

    assert client.clipboard_proof("同一段文本") == "结果"
    content = session.payloads[-1]["messages"][-1]["content"]
    assert "整段纠错" in content, f"应使用整段纠错提示词，实际：{content!r}"

    # 同一段文本走逐句校对：不得命中纠错那份缓存
    client.proof_text("同一段文本")
    content = session.payloads[-1]["messages"][-1]["content"]
    assert "逐句校对" in content, "逐句校对命中了纠错缓存（缓存键没隔离）"
