# tests/test_reference_word_watch.py
"""选区识别规则：只认"文献类引用"，图注/编号域绝不触发。

用户实测反馈两件事：① 点导航跳到**图注**会误弹窗；② EndNote 插入的引用（域里没有数据）
点了没反应。这两个都已用测试钉住。
"""
from __future__ import annotations

import pytest

from plugins.reference.docx_scan import footnote_anchor
from plugins.reference.word_watch import resolve_selection, screen_point


class _Field:
    def __init__(self, code: str):
        self.Code = type("Code", (), {"Text": code})()


class _Fields:
    def __init__(self, codes: list[str]):
        self._codes = codes
        self.Count = len(codes)

    def __call__(self, index):
        return _Field(self._codes[index - 1])


class _Notes:
    def __init__(self, texts: list[str]):
        self._texts = texts
        self.Count = len(texts)

    def __call__(self, index):
        return type("Note", (), {"Range": type("R", (), {"Text": self._texts[index - 1]})()})()


class _Information:
    def __call__(self, kind):
        return 100 if kind == 5 else 200


class _Paragraphs:
    def __init__(self, style: str):
        self._style = style

    def __call__(self, index):
        return type("P", (), {"Style": type("S", (), {"NameLocal": self._style})()})()


class _Selection:
    def __init__(self, *, fields=None, footnotes=None, endnotes=None, text="[1]",
                 style="正文"):
        self.Fields = _Fields(fields or [])
        self.Footnotes = _Notes(footnotes or [])
        self.Endnotes = _Notes(endnotes or [])
        self.Paragraphs = _Paragraphs(style)
        self.Text = text
        self.Information = _Information()
        self.Start = 10
        self.End = 14


class _Window:
    def PointsToScreenPixelsX(self, value):
        return value + 1

    def PointsToScreenPixelsY(self, value):
        return value + 2


class _Doc:
    def __init__(self, name: str):
        self.Name = name


class _Word:
    def __init__(self, selection: _Selection, doc_name: str = "论文.docx"):
        self.Selection = selection
        self.ActiveDocument = _Doc(doc_name)
        self.ActiveWindow = _Window()


ZOTERO_CODE = (
    ' ADDIN ZOTERO_ITEM CSL_CITATION {"citationItems":[{"id":934,"itemData":{"id":934,'
    '"title":"Global biodiversity","DOI":"10.1038/nature02593","issued":{"date-parts":[[2004]]}}}]} '
)


def test_resolves_zotero_field():
    payload = resolve_selection(_Word(_Selection(fields=[ZOTERO_CODE])))
    assert payload is not None
    assert payload["doc_name"] == "论文.docx"
    item = payload["items"][0]
    assert item["anchor_key"] == "doi:10.1038/nature02593"
    assert item["label"] == "1"


def test_cross_reference_field_never_triggers():
    """图注/编号项交叉引用（REF _Ref…）不再弹窗 —— 用户点导航跳图注时被误触发过。"""
    assert resolve_selection(_Word(_Selection(fields=[" REF _Ref225454536 \\h "], text="图 1"))) is None


@pytest.mark.parametrize("code", [" SEQ 图 \\* ARABIC ", " STYLEREF 1 \\s ", " REF _Ref1 \\h "])
def test_numbering_fields_never_trigger(code):
    assert resolve_selection(_Word(_Selection(fields=[code], text="图 1"))) is None


@pytest.mark.parametrize("style", ["题注", "Caption", "caption 1"])
def test_caption_paragraph_never_triggers(style):
    selection = _Selection(fields=[ZOTERO_CODE], style=style)
    assert resolve_selection(_Word(selection)) is None, "题注段落里的任何位置都不弹"


def test_endnote_shell_field_triggers_unbound_anchor():
    """EndNote 只写了域壳（无数据）时也要弹，锚点退回文档编号。"""
    payload = resolve_selection(_Word(_Selection(fields=[" ADDIN EN.JS.CITE "], text="[3]")))
    assert payload is not None
    item = payload["items"][0]
    assert item["anchor_key"] == "doc:论文.docx:cite:3"
    assert item["reference"] is None, "没有元数据 → 由用户绑定/新建"


def test_endnote_data_field_also_triggers():
    payload = resolve_selection(_Word(_Selection(fields=[" ADDIN EN.CITE.DATA "], text="[7]")))
    assert payload is not None
    assert payload["items"][0]["anchor_key"] == "doc:论文.docx:cite:7"


def test_endnote_shell_without_label_is_ignored():
    """连编号都取不到时无法确定身份，宁可不弹也不要弹一个空壳。"""
    assert resolve_selection(_Word(_Selection(fields=[" ADDIN EN.JS.CITE "], text=""))) is None


def test_resolves_footnote_by_text_hash():
    payload = resolve_selection(_Word(_Selection(footnotes=["这是一条脚注"])))
    assert payload is not None
    expected = footnote_anchor("论文.docx", "这是一条脚注", "deadbeef")
    assert payload["items"][0]["anchor_key"] == expected


def test_resolves_endnote_by_text_hash():
    payload = resolve_selection(_Word(_Selection(endnotes=["尾注内容"])))
    assert payload is not None
    assert payload["items"][0]["anchor_key"].startswith("doc:论文.docx:en:")


def test_returns_none_for_plain_text_selection():
    assert resolve_selection(_Word(_Selection(text="普通正文，没有引用"))) is None


def test_returns_none_when_word_has_no_document():
    class Broken:
        @property
        def Selection(self):
            raise RuntimeError("Word 已关闭")

    assert resolve_selection(Broken()) is None


def test_screen_point_uses_page_position():
    x, y = screen_point(_Word(_Selection()), _Selection())
    assert isinstance(x, int) and isinstance(y, int)


def test_screen_point_falls_back_when_api_missing():
    class NoWindow:
        Selection = _Selection()

    x, y = screen_point(NoWindow(), _Selection())
    assert isinstance(x, int) and isinstance(y, int)