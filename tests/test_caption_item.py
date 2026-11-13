# tests/test_caption_item.py
"""题录条目：跳转定位（域 → 字符区间 → 按文案）与"点哪条跳哪条"的确定性。

这里用一个小的 Word 模拟器（文本 + Find 收窄区间 + 段落），因为跳转的正确性恰恰取决于
Word 的几条行为：
- `Range(start, end).Find.Execute()` 命中后**收窄自身区间**；
- `Selection.Find` 是"从光标往后找"（老实现踩的坑，见 2026-11-13 现场）；
- 带 `\\h`（隐藏）的序列域 `Result.Select()` 会得到 **0 宽空选区**（无高亮）。
"""
from __future__ import annotations

from plugins.navigator.caption_item import CaptionItem


class _FakeRange:
    def __init__(self, doc, start: int, end: int):
        self._doc = doc
        self.Start = start
        self.End = end

    @property
    def Text(self) -> str:
        return self._doc.text[self.Start:self.End]

    @property
    def Find(self):
        return _FakeFind(self)

    @property
    def Duplicate(self):
        return _FakeRange(self._doc, self.Start, self.End)

    def Select(self):                       # noqa: N802
        self._doc.selection = (self.Start, self.End)

    def Paragraphs(self, _count):           # noqa: N802 - 模拟 Word：段落对象自己才有 .Range
        paragraph_range = _FakeRange(self._doc, self.Start, self.End)
        return type("P", (), {"Range": paragraph_range})()


class _FakeFind:
    def __init__(self, rng: _FakeRange):
        self._range = rng
        self.Text = ""
        self.Forward = True
        self.Wrap = 0
        self.searches: list[str] = rng._doc.searches

    def ClearFormatting(self):               # noqa: N802
        return None

    def Execute(self):                       # noqa: N802
        self.searches.append(self.Text)
        index = self._range._doc.text.find(self.Text, self._range.Start, self._range.End)
        if index < 0:
            return False
        self._range.End = index + len(self.Text)
        self._range.Start = index
        return True


class _FakeDoc:
    """一份纯文本文档：`Content` 覆盖全文，`Range(a,b)` 是子区间。"""

    def __init__(self, text: str):
        self.text = text
        self.searches: list[str] = []
        self.range_calls: list[tuple[int, int]] = []
        self.selection: tuple[int, int] | None = None
        self.Content = _FakeRange(self, 0, len(text))

    def Range(self, start, end):             # noqa: N802
        self.range_calls.append((start, end))
        return _FakeRange(self, int(start), int(end))

    def selected_text(self) -> str:
        if self.selection is None:
            return "<未选中>"
        return self.text[self.selection[0]:self.selection[1]]


DOC = "前言……图1.1 演示图片……图1.2 演示图片……表1.1 演示表格……图3.4 演示图片……"


def test_text_only_item_finds_its_own_caption():
    """离线条目只有文案：必须命中**自己那条**（不是文档里的第一个同名）。"""
    doc = _FakeDoc(DOC)
    item = CaptionItem(display="图1.2 演示图片")

    assert item.select_in_document(doc) is True
    assert doc.selected_text() == "图1.2 演示图片"


def test_text_search_starts_from_document_beginning():
    """搜索必须从文档开头开始（老实现从光标往后搜 ⇒ 点哪条看光标停在哪，"时好时坏"）。"""
    doc = _FakeDoc(DOC)
    item = CaptionItem(display="图3.4 演示图片")

    item.select_in_document(doc)

    assert doc.range_calls[0][0] == 0, "第一次搜索必须从 0 开始"
    assert doc.selected_text() == "图3.4 演示图片"


def test_duplicate_text_uses_ordinal():
    """文案完全相同的多张图：按序号取第 N 条，而不是永远第一条。"""
    text = "图1.1 演示图片……图1.2 演示图片"
    doc = _FakeDoc(text)
    item = CaptionItem(display="演示图片", ordinal=1)   # 第二条同名

    assert item.select_in_document(doc) is True
    assert doc.selection == (text.rindex("演示图片"), text.rindex("演示图片") + 4)


def test_text_search_failure_returns_false():
    item = CaptionItem(display="图 9.9 不存在")
    assert item.select_in_document(_FakeDoc(DOC)) is False


def test_field_item_selects_whole_paragraph_for_highlight():
    """有序列域时选**整段**：只选 `field.Result` 对 `\\h` 隐藏域是 0 宽空选区（没有高亮）。"""
    doc = _FakeDoc(DOC)
    para_start = DOC.index("图1.1")
    field = type("F", (), {"Result": doc.Range(para_start, para_start + 8)})()
    item = CaptionItem(display="图1.1 演示图片", field=field)

    assert item.select_in_document(doc) is True
    assert doc.selection == (para_start, para_start + 8 - 1), "选整段并去掉段尾标记"
    assert doc.searches == [], "有域时不该再去搜索文本"


def test_range_used_when_field_missing():
    doc = _FakeDoc(DOC)
    item = CaptionItem(display="表 1.1 y", start=10, end=20)

    assert item.select_in_document(doc) is True
    assert (10, 20) in doc.range_calls
    assert doc.searches == [], "有区间时不该再去搜索文本"


def test_display_with_page_suffix_is_trimmed():
    """列表里显示的是"文案  [第3页]"，搜索要剔掉页码尾巴。"""
    doc = _FakeDoc(DOC)
    item = CaptionItem(display="图1.2 演示图片  [第3页]")

    item.select_in_document(doc)

    assert doc.searches[0] == "图1.2 演示图片"
    assert doc.selected_text() == "图1.2 演示图片"


def test_caption_kind_recognizes_chinese_and_english_seq_codes():
    """序列域判定中英文都要认：只认中文会让 COM 刷新把英文模板的题录刷成 0 条。"""
    from plugins.navigator.caption_item import caption_kind

    assert caption_kind("SEQ 图 \\* ARABIC") == "figure"
    assert caption_kind("SEQ 表 \\* ARABIC") == "table"
    assert caption_kind("SEQ figure \\h \\s 1") == "figure"
    assert caption_kind("SEQ TABLE \\* ARABIC") == "table"


def test_caption_kind_ignores_non_caption_fields():
    from plugins.navigator.caption_item import caption_kind

    assert caption_kind("SEQ chapter \\c") is None, "章号域不是题注"
    assert caption_kind("SEQ 公式 \\* ARABIC") is None
    assert caption_kind("PAGEREF _Toc123 \\h") is None
    assert caption_kind("") is None
    assert caption_kind(None) is None
