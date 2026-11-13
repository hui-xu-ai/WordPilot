# tests/test_navigator_jump.py
"""双击题录跳转：保留高亮（选中目标区间），但把这次选中登记为"程序选区"。

用户现场（2026-11-13）：跳转把整条题注选中 → 题注整段往往正好是一个句子 → 智能纠错
"上一轮光标 + 这一轮选中正好等于整句 ⇒ 校对这次选中"的规则被命中，跳一下就顺手校对一次。
把选区收成光标能避免触发，但高亮也没了（用户明确要保留高亮）⇒ 改为登记程序选区。
"""
from __future__ import annotations

import pytest

from core.services import word_bridge
from plugins.navigator.navigator_jump import JumpMixin


class _Selection:
    def __init__(self, start: int, end: int):
        self.Start = start
        self.End = end
        self.Range = self

    @property
    def Duplicate(self):                    # noqa: N802 - Word 的 Range.Duplicate 是属性
        return _Selection(self.Start, self.End)


class _Window:
    def __init__(self, selection: _Selection):
        self.Selection = selection
        self.scrolled: list[tuple[int, int]] = []

    def ScrollIntoView(self, rng, _arg):    # noqa: N802
        self.scrolled.append((rng.Start, rng.End))


class _Word:
    def __init__(self, selection: _Selection):
        self.Selection = selection
        self.ActiveWindow = _Window(selection)
        self.ScreenUpdating = True


class _Doc:
    def __init__(self, selection: _Selection):
        self._selection = selection


class _Item:
    def __init__(self, display: str, span: tuple[int, int]):
        self.display = display
        self._span = span
        self.selected = False

    def select_in_document(self, doc):
        self.selected = True
        doc._selection.Start, doc._selection.End = self._span   # 模拟 `Range.Select()`
        return True


class _ListItem:
    def __init__(self, text: str):
        self._text = text

    def text(self) -> str:
        return self._text


class _Panel(JumpMixin):
    def __init__(self, word, doc, captions):
        self.word = word
        self.doc = doc
        self.table_captions = captions
        self.figure_captions = captions
        self.history_stack = []
        self.max_history = 5
        self.statuses: list[str] = []

    def _get_word_document(self):
        return self.word, self.doc, "C:/docs/论文.docx"

    def _set_status(self, key, **params):
        self.statuses.append(key)


SPAN = (100, 112)


@pytest.fixture
def panel_and_word(monkeypatch):
    monkeypatch.setattr(word_bridge, "_programmatic_range", None)
    selection = _Selection(50, 50)          # 跳转前：光标
    word = _Word(selection)
    item = _Item("图1.1  演示图片", SPAN)
    panel = _Panel(word, _Doc(selection), [item])
    return panel, word, item


def _jump(panel):
    display = "图1.1  演示图片  [3]"
    panel._jump_to_caption(_ListItem(display), panel.table_captions, _ListItem(display))


def test_jump_keeps_highlight(panel_and_word):
    """跳转后目标区间仍然被选中 —— 这就是用户要看的"高亮"。"""
    panel, word, item = panel_and_word

    _jump(panel)

    assert item.selected is True, "跳转本身必须发生"
    assert (word.Selection.Start, word.Selection.End) == SPAN, "保留选中才有高亮"


def test_jump_registers_programmatic_selection(panel_and_word):
    """这次选中必须登记为程序选区，智能纠错才不会把它当成"用户在选整句"。"""
    panel, word, _item = panel_and_word

    _jump(panel)

    assert word_bridge.selection_is_programmatic(*SPAN) is True
    assert word_bridge.selection_is_programmatic(7, 9) is False, "别的区间不算程序选区"


def test_jump_still_scrolls_and_reports(panel_and_word):
    panel, word, _item = panel_and_word

    _jump(panel)

    assert word.ActiveWindow.scrolled, "跳转后仍要滚动到该处"
    assert "status.jumped" in panel.statuses


def test_jump_records_history_of_previous_caret(panel_and_word):
    panel, _word, _item = panel_and_word

    _jump(panel)

    assert len(panel.history_stack) == 1, "跳转前的位置要进历史（返回上一位置用）"
    assert panel.history_stack[0].Start == panel.history_stack[0].End == 50


def test_registration_failure_does_not_break_jump(panel_and_word, monkeypatch):
    """登记失败只可能多校对一次，不能连跳转都失败。"""
    panel, _word, _item = panel_and_word

    def _boom(_selection):
        raise RuntimeError("COM 拒绝")

    monkeypatch.setattr(word_bridge, "mark_programmatic_selection", _boom)

    _jump(panel)

    assert "status.jumped" in panel.statuses
