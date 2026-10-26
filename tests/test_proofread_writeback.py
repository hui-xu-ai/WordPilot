# tests/test_proofread_writeback.py
"""B01 写回保护：建议生成后原文变化（切文档/继续编辑）时不得写回。

审查报告（2026-10-25-strict-audit）用真实 Word 复现：
A 文档生成建议后切到 B，点应用把 B 的文本改成 A 的建议，无任何警告。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def mod():
    from plugins.proofread import plugin as module

    return module


class _FakeRange:
    def __init__(self, text: str, start: int = 0):
        self.Text = text
        self.Start = start
        self.End = start + len(text)

    def Duplicate(self):
        return _FakeRange(self.Text, self.Start)


class _FakeDoc:
    def __init__(self, text: str):
        self.text = text
        self.written = []

    def Range(self, start, end):
        return _FakeRange(self.text, start)


class _FakeWord:
    def __init__(self, doc):
        self.ActiveDocument = doc
        self.Selection = type("S", (), {"Start": 0, "End": 0})()
        self.ScreenUpdating = True


class _FakeTokenized:
    def __init__(self, sent_range):
        self._range = sent_range

    def get_blocks(self):
        return [{"type": "text"}]


def _make_plugin(mod, monkeypatch, current_text: str):
    """构造只够跑 apply_correction 的插件替身。"""
    plugin = mod.ProofreadPlugin.__new__(mod.ProofreadPlugin)
    doc = _FakeDoc(current_text)
    statuses: list[str] = []
    warnings: list[str] = []

    plugin._status = lambda text: statuses.append(text)
    plugin.tr = lambda key, **kw: key
    plugin._warn_stale_suggestion = lambda: warnings.append("stale")
    plugin.normalize_span = mod.ProofreadPlugin.normalize_span

    from plugins.proofread import writeback as wb

    monkeypatch.setattr(wb, "get_word_app", lambda: _FakeWord(doc))
    monkeypatch.setattr(wb, "TokenizedSentence", _FakeTokenized)
    monkeypatch.setattr(wb, "clear_error_marks", lambda *a, **k: None)
    monkeypatch.setattr(wb, "QMessageBox", type("Q", (), {
        "warning": staticmethod(lambda *a, **k: warnings.append("warning")),
        "information": staticmethod(lambda *a, **k: None),
    }))
    return plugin, doc, statuses, warnings


def test_stale_suggestion_is_not_written(mod, monkeypatch):
    """原文已变（当前句子 ≠ 生成建议时的原文）⇒ 拒绝写回。"""
    plugin, doc, statuses, warnings = _make_plugin(
        mod, monkeypatch, current_text="这是被改过的另一句话。")

    plugin.apply_correction("这是原来的句子。", 0, 12,
                            original_text="这是原来的句子。")

    assert doc.written == [], "原文不匹配时绝不能写回"
    assert doc.text == "这是被改过的另一句话。", "正文不能被改动"
    assert any("stale" in s for s in statuses), f"应给出过期提示，实际 {statuses}"
    assert warnings, "应提示用户建议已过期"


def test_matching_original_still_applies(mod, monkeypatch):
    """原文一致时照常写回（不能把正常路径挡掉）。"""
    plugin, doc, statuses, warnings = _make_plugin(
        mod, monkeypatch, current_text="这是原来的句子。")

    written = {}

    class _Recorder(_FakeRange):
        @property
        def Text(self):                       # noqa: N802 - 模拟 COM 属性
            return written.get("text", "这是原来的句子。")

        @Text.setter
        def Text(self, value):
            written["text"] = value

    class _Doc(_FakeDoc):
        def Range(self, start, end):
            return _Recorder(self.text, start)

    doc = _Doc("这是原来的句子。")
    from plugins.proofread import writeback as wb
    monkeypatch.setattr(wb, "get_word_app", lambda: _FakeWord(doc))

    plugin.apply_correction("这是修改后的句子。", 0, 8,
                            original_text="这是原来的句子。")

    # 句尾标点属于"尾部"，替换只覆盖主体（既有行为），所以这里不带「。」\n    assert written.get("text") == "这是修改后的句子", "匹配时应写入新文本主体"
    assert not warnings, "正常写回不应报警"


def test_no_original_text_skips_guard(mod, monkeypatch):
    """没给 original_text（旧调用方）时保持原行为，不因缺少对比而拒绝。"""
    plugin, doc, statuses, warnings = _make_plugin(
        mod, monkeypatch, current_text="任意当前内容。")

    plugin.apply_correction("任意新内容。", 0, 5)

    assert any("stale" in s for s in statuses) is False, "没有原文可比时不应报过期"


def test_whitespace_difference_still_applies(mod, monkeypatch):
    """B01 收尾：`Range.Text` 常带句首空白/制表符，空白差异不能判成"已过期"。

    现场表现就是"点了应用毫无反应、校对像失效了"。
    """
    plugin, doc, statuses, warnings = _make_plugin(
        mod, monkeypatch, current_text="\t  这是原来的句子。")

    plugin.apply_correction("这是修改后的句子。", 0, 12,
                            original_text="这是原来的句子。")

    assert not warnings, f"空白差异不该报过期：{statuses}"
    assert not any("stale" in s for s in statuses)


def test_real_change_still_blocked(mod, monkeypatch):
    """真正换了一句（不是空白差异）时仍然必须拒绝写回。"""
    plugin, doc, statuses, warnings = _make_plugin(
        mod, monkeypatch, current_text="完全不同的另一句话。")

    plugin.apply_correction("这是修改后的句子。", 0, 12,
                            original_text="这是原来的句子。")

    assert warnings, "内容真的变了必须拦下"
    assert doc.written == []
