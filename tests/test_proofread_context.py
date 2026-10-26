# tests/test_proofread_context.py
"""U08：防抖间隔与上下文字数必须真正参与校对（此前只存配置不生效）。

审查证据（2026-10-25-strict-audit，静态确认）：设置页写入 debounce_ms /
context_before / context_after，但监控固定 `sleep(0.2)`，`proof_sentence` 也不读这些键。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def monitor_cls():
    from plugins.proofread.word_monitor import WordMonitor

    return WordMonitor


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeTokenized:
    """只提供 proof_sentence 需要的成员（避免依赖真实 Word Range）。"""

    pure_text = "原句内容。"

    def __init__(self, sentence_range):
        self.range = sentence_range

    def get_blocks(self):
        return [{"type": "text"}]

    def get_text_for_api(self):
        return "原句内容。这里是一段更长的句子用于校对"


@pytest.fixture(autouse=True)
def _stub_tokenized(monkeypatch):
    from plugins.proofread import word_monitor

    monkeypatch.setattr(word_monitor, "TokenizedSentence", _FakeTokenized)


class _Doc:
    def __init__(self, text: str):
        self.text = text

    def Range(self, start, end):           # noqa: N802 - 模拟 COM
        return type("R", (), {"Text": self.text[start:end]})()


class _SentenceRange:
    def __init__(self, text: str, start: int = 10):
        self.Text = text
        self.Start = start
        self.End = start + len(text)
        self.Document = _Doc("前文内容" + text + "后文内容")


def test_debounce_seconds_follows_config(monitor_cls):
    """U08：debounce_ms 决定轮询间隔（此前写死 0.2s）。"""
    watch = monitor_cls.__new__(monitor_cls)

    watch.config = _Cfg({"debounce_ms": 800})
    assert watch._debounce_seconds() == pytest.approx(0.8)

    watch.config = _Cfg({"debounce_ms": 0})
    assert watch._debounce_seconds() == pytest.approx(0.2), "非法值应回退 200ms"

    watch.config = _Cfg({})
    assert watch._debounce_seconds() == pytest.approx(0.2)


def test_context_is_sent_to_api_and_affects_cache_key():
    """U08：context_before/after 真正拼进提示词，并改变缓存键。"""
    from core.services.proof_cache import get_default_cache
    from plugins.proofread.word_monitor import proof_sentence

    get_default_cache().clear()

    class _Api:
        def __init__(self):
            self.prompts: list[str] = []
            self.keys: list[str] = []

        def proof_text(self, text, cache_key=None, **_kwargs):
            self.prompts.append(text)
            self.keys.append(cache_key)
            return "把句子改一下。"

    api = _Api()
    proof_sentence(_SentenceRange("原句内容。"), api,
                   _Cfg({"context_before": 4, "context_after": 4}))

    assert api.prompts, "没有调用 API"
    assert "上下文" in api.prompts[0], f"提示词里没有上下文：{api.prompts[0]}"
    assert "ctx" in (api.keys[0] or ""), f"缓存键应带上下文指纹：{api.keys[0]}"


def test_no_context_keeps_plain_cache_key():
    """context 为 0 时不改变缓存键（保持既有缓存复用）。"""
    from core.services.proof_cache import get_default_cache
    from plugins.proofread.word_monitor import proof_sentence

    get_default_cache().clear()

    class _Api:
        def __init__(self):
            self.keys: list[str] = []

        def proof_text(self, text, cache_key=None, **_kwargs):
            self.keys.append(cache_key)
            return "把句子改一下。"

    api = _Api()
    proof_sentence(_SentenceRange("原句内容。"), api, _Cfg({}))

    assert api.keys and "ctx" not in (api.keys[0] or "")
