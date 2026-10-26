# tests/test_proofread_table_replace.py
"""修"点击替换出错：无法删除范围"（Word 表格 / 域内）。

现场报错：`无法删除范围。 wdmain11.chm 25508`。
原因：表格里句子 `Range.Text` 末尾带单元格标记 `\\x07`，旧尾部拆分不认它，
替换范围把 `\\x07` 包进去 → Word 拒绝删除该范围。
"""
from __future__ import annotations

import pytest

from plugins.proofread.word_monitor import split_trailing_chars


def test_cell_marker_is_treated_as_tail():
    body, tail = split_trailing_chars("Nafion 薄膜\x07")
    assert body == "Nafion 薄膜", f"单元格标记不能进正文，实际 {body!r}"
    assert tail == "\x07"


def test_cell_marker_after_period_is_tail():
    body, tail = split_trailing_chars("这是一句话。\x07")
    assert body == "这是一句话"
    assert tail == "。\x07"


def test_paragraph_and_page_breaks_are_tail():
    for marker in ("\r\n", "\x0b", "\x0c"):
        body, tail = split_trailing_chars(f"句子内容{marker}")
        assert body == "句子内容", f"{marker!r} 应算尾部"
        assert tail == marker


def test_plain_text_without_period_is_unchanged():
    body, tail = split_trailing_chars("没有标点的句子")
    assert (body, tail) == ("没有标点的句子", "")


class _FailingRange:
    """模拟表格里 Range.Text 赋值抛 COM 错误。"""

    Start, End = 0, 8
    Text = "原文句子"

    def __setattr__(self, name, value):
        if name == "Text" and value == "改写后的句子":
            raise RuntimeError("(-2147352567, '发生意外。', (0, 'Microsoft Word', '无法删除范围。'))")
        object.__setattr__(self, name, value)

    def Duplicate(self):
        return self


def test_writeback_falls_back_to_manual_edit(monkeypatch):
    """写不进去时不能弹"替换失败"，而应清标记 + 提示手动修改。"""
    from plugins.proofread import writeback as wb
    from plugins.proofread.plugin import ProofreadPlugin

    class _Doc:
        FullName = "C:/docs/表格.docx"

        def Range(self, start, end):        # noqa: N802
            return _FailingRange()

    class _Word:
        ActiveDocument = _Doc()
        Selection = type("S", (), {"Start": 0, "End": 0})()
        ScreenUpdating = True

    plugin = ProofreadPlugin.__new__(ProofreadPlugin)
    statuses: list[str] = []
    manual: list[str] = []
    plugin._status = statuses.append
    plugin.tr = lambda key, **kw: key
    plugin._warn_stale_suggestion = lambda: None
    plugin._notify_manual_edit = lambda: manual.append("manual")

    class _Tokenized:
        def __init__(self, sentence_range):
            pass

        def get_blocks(self):
            return [{"type": "text"}]

    monkeypatch.setattr(wb, "get_word_app", lambda: _Word())
    monkeypatch.setattr(wb, "TokenizedSentence", _Tokenized)
    monkeypatch.setattr(wb, "clear_error_marks", lambda *a, **k: None)

    ok = plugin.apply_correction("改写后的句子", 0, 8, original_text="原文句子",
                                 doc_identity="C:/docs/表格.docx")

    assert ok is False
    assert manual, "应提示手动修改而不是抛替换失败"
    assert "status.manual_only" in statuses
