# tests/test_navigator_jump.py
"""双击题录跳转只跳不选：选区必须收成光标。

用户现场（2026-11-13）：双击题录跳到题注时，整条题注被选中，而题注整段往往**正好等于一个句子**
—— 智能纠错监听线程里"上一轮是光标 + 这一轮选中正好等于整句 ⇒ 校对这次选中"的规则被命中，
于是"跳一下顺便触发一次纠错"。
"""
from __future__ import annotations

from plugins.navigator.navigator_jump import JumpMixin


class _Selection:
    def __init__(self, start: int, end: int):
        self.Start = start
        self.End = end
        self.Range = self

    @property
    def Duplicate(self):                    # noqa: N802 - Word 的 Range.Duplicate 是属性
        return _Selection(self.Start, self.End)

    def SetRange(self, start: int, end: int):   # noqa: N802
        self.Start, self.End = start, end

    def Select(self):                       # noqa: N802
        return None


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
    def __init__(self, selection: _Selection, span: tuple[int, int]):
        self._selection = selection
        self._span = span

    def Range(self, start, end):            # noqa: N802
        return _Selection(start, end)


class _Item:
    def __init__(self, display: str, span: tuple[int, int]):
        self.display = display
        self._span = span
        self.selected = False

    def select_in_document(self, doc, selection=None):
        self.selected = True
        target = selection if selection is not None else doc._selection
        target.Start, target.End = self._span      # 模拟 `Range.Select()`：整段被选中
        return True


class _ListWidget:
    def __init__(self, text: str):
        self._text = text

    def item(self):                         # 只给测试用
        return self

    def text(self) -> str:
        return self._text


class _ListItem:
    def __init__(self, text: str):
        self._text = text

    def text(self) -> str:
        return self._text


class _Panel(JumpMixin):
    def __init__(self, word, doc, captions, display):
        self.word = word
        self.doc = doc
        self._captions = captions
        self.table_captions = captions
        self.figure_captions = captions
        self.table_list = _ListWidget(display)
        self.figure_list = _ListWidget(display)
        self.history_stack = []
        self.max_history = 5
        self.statuses: list[str] = []

    def _get_word_document(self):
        return self.word, self.doc, "C:/docs/论文.docx"

    def _set_status(self, key, **params):
        self.statuses.append(key)


def _make(span=(100, 112)):
    selection = _Selection(50, 50)          # 跳转前：光标
    word = _Word(selection)
    doc = _Doc(selection, span)
    item = _Item("图1.1  演示图片", span)
    panel = _Panel(word, doc, [item], "图1.1  演示图片  [3]")
    return panel, word, item


def test_jump_collapses_selection_to_caret():
    panel, word, item = _make()

    panel._jump_to_caption(_ListItem("图1.1  演示图片  [3]"), [item],
                           _ListItem("图1.1  演示图片  [3]"))

    assert item.selected is True, "跳转本身必须发生"
    assert word.Selection.Start == word.Selection.End, "跳转后必须收成光标（否则触发智能纠错）"
    assert word.Selection.Start == 100, "光标落在题注起点"


def test_jump_still_scrolls_and_reports():
    panel, word, _item = _make()

    panel._jump_to_caption(_ListItem("图1.1  演示图片  [3]"), panel.table_captions,
                           _ListItem("图1.1  演示图片  [3]"))

    assert word.ActiveWindow.scrolled, "跳转后仍要滚动到该处"
    assert "status.jumped" in panel.statuses


def test_jump_records_history_of_previous_caret():
    panel, _word, item = _make()

    panel._jump_to_caption(_ListItem("图1.1  演示图片  [3]"), [item],
                           _ListItem("图1.1  演示图片  [3]"))

    assert len(panel.history_stack) == 1, "跳转前的位置要进历史（返回上一位置用）"
    assert panel.history_stack[0].Start == panel.history_stack[0].End == 50


def test_collapse_failure_does_not_break_jump():
    """Word 不给改选区时，跳转仍要完成（只是可能还保持选中）。"""
    panel, word, item = _make()

    def _boom(_start, _end):
        raise RuntimeError("COM 拒绝")

    word.Selection.SetRange = _boom

    panel._jump_to_caption(_ListItem("图1.1  演示图片  [3]"), [item],
                           _ListItem("图1.1  演示图片  [3]"))

    assert "status.jumped" in panel.statuses
