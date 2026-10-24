# tests/test_caption_item.py
"""题录条目：跳转定位的三级兜底（离线来源没有 COM 引用，只能按文案查找）。"""
from __future__ import annotations

from plugins.navigator.caption_item import CaptionItem


class _FakeResult:
    def Select(self):        # noqa: N802 - 模拟 Word COM 方法名
        return None


class _FakeField:
    def __init__(self):
        self.result = _FakeResult()
        self.used = False

    @property
    def Result(self):        # noqa: N802
        self.used = True
        return self.result


class _FakeRange:
    def __init__(self):
        self.selected = False

    def Select(self):        # noqa: N802
        self.selected = True


class _FakeFind:
    def __init__(self, hit: bool):
        self.hit = hit
        self.Text = ""
        self.Forward = True
        self.Wrap = 0
        self.searches: list[str] = []

    def ClearFormatting(self):      # noqa: N802
        return None

    def Execute(self):              # noqa: N802
        self.searches.append(self.Text)
        return self.hit


class _FakeDoc:
    def __init__(self, hit: bool):
        self.range_calls: list[tuple[int, int]] = []
        self.Content = type("Content", (), {"Find": _FakeFind(hit)})()

    def Range(self, start, end):    # noqa: N802
        self.range_calls.append((start, end))
        return _FakeRange()


def test_offline_item_falls_back_to_text_search():
    """离线条目只有文案：必须能靠 Find 定位，而不是直接放弃。"""
    doc = _FakeDoc(hit=True)
    item = CaptionItem(display="图 1.9 驱动器制备流程记录")

    assert item.select_in_document(doc) is True
    assert doc.Content.Find.searches[0] == "图 1.9 驱动器制备流程记录"


def test_text_search_failure_returns_false():
    item = CaptionItem(display="图 9.9 不存在")
    assert item.select_in_document(_FakeDoc(hit=False)) is False


def test_field_preferred_over_text_search():
    field, doc = _FakeField(), _FakeDoc(hit=True)
    item = CaptionItem(display="图 1.1 x", field=field)

    assert item.select_in_document(doc) is True
    assert field.used is True
    assert doc.Content.Find.searches == [], "有序列域时不该再去搜索文本"


def test_range_used_when_field_missing():
    doc = _FakeDoc(hit=True)
    item = CaptionItem(display="表 1.1 y", start=10, end=20)

    assert item.select_in_document(doc) is True
    assert doc.range_calls == [(10, 20)]
    assert doc.Content.Find.searches == []


def test_display_with_page_suffix_is_trimmed():
    """列表里显示的是"文案  [第3页]"，搜索要剔掉页码尾巴。"""
    doc = _FakeDoc(hit=True)
    item = CaptionItem(display="图 1.2 z  [第3页]")

    item.select_in_document(doc)
    assert doc.Content.Find.searches[0] == "图 1.2 z"
