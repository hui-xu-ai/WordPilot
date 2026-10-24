# tests/test_reference_word_watch.py
"""选区识别规则：只认"文献类引用"，图注/编号域绝不触发。

用户实测反馈三件事，都已用测试钉住：
① 点导航跳到**图注**会误弹窗；
② EndNote 插入的引用（域里没有元数据）点了没反应；
③ **单击**引用（折叠光标，`Selection.Text` 为空）时也必须有反应 —— 编号要从
   `Field.Result.Text` 取，而不是从选区文本取。
"""
from __future__ import annotations

import pytest

from plugins.reference.docx_scan import footnote_anchor
from plugins.reference.word_watch import resolve_selection, screen_point


class _Code:
    def __init__(self, text: str):
        self.Text = text


class _Field:
    def __init__(self, code: str, result: str = ""):
        self.Code = _Code(code)
        self.Result = type("R", (), {"Text": result})()


class _Fields:
    def __init__(self, fields: list[tuple[str, str]]):
        self._fields = fields
        self.Count = len(fields)

    def __call__(self, index):
        code, result = self._fields[index - 1]
        return _Field(code, result)


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
        self.Document = self

    def Range(self, start, end):
        return type("R", (), {"Fields": self.Fields})()


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
    payload = resolve_selection(_Word(_Selection(fields=[(ZOTERO_CODE, "[1]")])))
    assert payload is not None
    assert payload["doc_name"] == "论文.docx"
    item = payload["items"][0]
    assert item["anchor_key"] == "doi:10.1038/nature02593"
    assert item["label"] == "1"


def test_cross_reference_field_never_triggers():
    """图注/编号项交叉引用（REF _Ref…）不再弹窗 —— 用户点导航跳图注时被误触发过。"""
    selection = _Selection(fields=[(" REF _Ref225454536 \\h ", "图 1")], text="图 1")
    assert resolve_selection(_Word(selection)) is None


@pytest.mark.parametrize("code", [" SEQ 图 \\* ARABIC ", " STYLEREF 1 \\s ", " REF _Ref1 \\h "])
def test_numbering_fields_never_trigger(code):
    assert resolve_selection(_Word(_Selection(fields=[(code, "图 1")], text="图 1"))) is None


@pytest.mark.parametrize("style", ["题注", "Caption", "caption 1"])
def test_caption_paragraph_never_triggers(style):
    selection = _Selection(fields=[(ZOTERO_CODE, "[1]")], style=style)
    assert resolve_selection(_Word(selection)) is None, "题注段落里的任何位置都不弹"


def test_endnote_shell_field_triggers_unbound_anchor():
    """EndNote 只写了域壳（无数据）时也要弹，锚点退回文档编号。"""
    payload = resolve_selection(_Word(_Selection(fields=[(" ADDIN EN.JS.CITE ", "[3]")])))
    assert payload is not None
    item = payload["items"][0]
    assert item["anchor_key"] == "doc:论文.docx:cite:3"
    assert item["reference"] is None, "没有元数据 → 由用户绑定/新建"


def test_collapsed_caret_click_uses_field_result_text():
    """**单击**引用（没有选中任何文字）时，编号要从 Field.Result.Text 取。

    这是"点了没反应"的真正原因：旧实现只看 `Selection.Text`，折叠光标下它是空串。
    """
    selection = _Selection(fields=[(" ADDIN EN.JS.CITE ", "[1]")], text="")
    payload = resolve_selection(_Word(selection))
    assert payload is not None
    assert payload["items"][0]["anchor_key"] == "doc:论文.docx:cite:1"
    assert payload["items"][0]["label"] == "1"


def test_endnote_data_field_also_triggers():
    payload = resolve_selection(_Word(_Selection(fields=[(" ADDIN EN.CITE.DATA ", "[7]")])))
    assert payload is not None
    assert payload["items"][0]["anchor_key"] == "doc:论文.docx:cite:7"


def test_endnote_shell_without_label_falls_back_to_result_hash():
    """连编号都取不到时也不能"点了没反应"：用域结果文本哈希兜底，先弹出来让用户绑。"""
    payload = resolve_selection(_Word(_Selection(fields=[(" ADDIN EN.JS.CITE ", "")], text="")))
    assert payload is not None
    assert payload["items"][0]["anchor_key"].startswith("doc:论文.docx:cite:")


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

def test_label_fallback_reads_number_near_caret():
    """域索引没命中时，光标旁的 `[12]` 也要能兜底（EndNote 常把引用显示成文本）。"""
    class _Doc:
        Name = "论文.docx"

        def Range(self, start, end):
            return type("R", (), {"Text": "测试[12]"})

    selection = _Selection(fields=[], text="[12]")
    selection.Start = 5
    selection.End = 5
    word = type("W", (), {})()
    word.Selection = selection
    word.ActiveDocument = _Doc()
    word.ActiveWindow = _Window()

    from plugins.reference.word_watch import WordWatch

    watch = WordWatch()
    payload = watch._label_fallback_payload(word, "论文.docx")
    assert payload is not None
    assert payload["items"][0]["anchor_key"] == "doc:论文.docx:cite:12"
    assert payload["items"][0]["kind"] == "label_fallback"


def test_label_fallback_ignores_text_without_number():
    selection = _Selection(fields=[], text="普通正文")
    word = type("W", (), {})()
    word.Selection = selection
    word.ActiveDocument = type("D", (), {"Name": "论文.docx", "Range": lambda *a: type("R", (), {"Text": "普通正文"})()})()
    word.ActiveWindow = _Window()

    from plugins.reference.word_watch import WordWatch

    assert WordWatch()._label_fallback_payload(word, "论文.docx") is None
