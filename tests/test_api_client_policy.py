# tests/test_api_client_policy.py
"""B05/B07：缓存键要含模型与提示词策略；api_endpoint 配置必须生效。

审查证据：
- B05 换模型/改提示词后仍命中旧缓存（总调用只有一次）；
- B07 配置里生成了 api_endpoint，但客户端固定官方 URL，配置形同摆设。
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

    def __init__(self, content: str):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class _Session:
    def __init__(self, reply: str = "结果"):
        self.reply = reply
        self.urls: list[str] = []
        self.calls = 0

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.calls += 1
        self.urls.append(url)
        return _Reply(self.reply)


def _client(api_mod, monkeypatch, clear=True, **values):
    from core.services.proof_cache import get_default_cache

    if clear:
        get_default_cache().clear()
    session = _Session()
    monkeypatch.setattr(api_mod, "_session", lambda: session)
    monkeypatch.setattr(api_mod, "log_api_call", lambda *a, **k: None)

    class _Cfg(dict):
        def get_api_key(self):
            return "sk-test"

        def get(self, key, default=None):
            return dict.get(self, key, default)

    return api_mod.DeepSeekClient(_Cfg(values)), session


def test_cache_key_changes_with_model(api_mod, monkeypatch):
    """B05：换模型必须重新请求，不能命中旧模型的缓存（两个客户端共用一份缓存）。"""
    from core.services.proof_cache import get_default_cache

    get_default_cache().clear()
    session = _Session()
    monkeypatch.setattr(api_mod, "_session", lambda: session)
    monkeypatch.setattr(api_mod, "log_api_call", lambda *a, **k: None)

    class _Cfg(dict):
        def get_api_key(self):
            return "sk-test"

        def get(self, key, default=None):
            return dict.get(self, key, default)

    first = api_mod.DeepSeekClient(_Cfg({"model": "model-A"}))
    assert first.proof_text("原句。") == "结果"

    second = api_mod.DeepSeekClient(_Cfg({"model": "model-B"}))
    assert second.proof_text("原句。") == "结果"

    assert session.calls == 2, "换模型后仍命中旧模型的缓存"


def test_cache_key_changes_with_prompt(api_mod, monkeypatch):
    """B05：改提示词必须重新请求。"""
    client, session = _client(api_mod, monkeypatch, proof_prompt="请只改错别字：{text}")
    client.proof_text("原句。")
    assert session.calls == 1

    # 同一进程里换提示词（模拟设置页改完保存）
    client.config["proof_prompt"] = "请按学术风格润色：{text}"
    client.proof_text("原句。")
    assert session.calls == 2, "改提示词后仍命中旧缓存"


def test_cache_still_hits_with_same_policy(api_mod, monkeypatch):
    """策略不变时仍应命中缓存（不能为了修 B05 把缓存废掉）。"""
    client, session = _client(api_mod, monkeypatch, model="model-A")
    client.proof_text("原句。")
    client.proof_text("原句。")
    assert session.calls == 1


def test_endpoint_from_config_is_used(api_mod, monkeypatch):
    """B07：api_endpoint 配置必须作用到实际请求 URL。"""
    client, session = _client(api_mod, monkeypatch,
                              api_endpoint="https://proxy.example.com")
    client.proof_text("原句。")

    assert session.urls, "没有发出请求"
    assert session.urls[0] == "https://proxy.example.com/v1/chat/completions"


def test_full_endpoint_path_kept_as_is(api_mod, monkeypatch):
    client, session = _client(api_mod, monkeypatch,
                              api_endpoint="https://proxy.example.com/chat/completions")
    client.proof_text("原句。")
    assert session.urls[0] == "https://proxy.example.com/chat/completions"


def test_bad_endpoint_falls_back_to_default(api_mod, monkeypatch):
    client, session = _client(api_mod, monkeypatch, api_endpoint="ftp://evil.example.com")
    client.proof_text("原句。")
    assert session.urls[0].startswith("https://api.deepseek.com"), \
        f"非法 scheme 应回退默认端点，实际 {session.urls[0]}"
