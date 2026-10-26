"""回归：监听线程单次循环不能因为签名不一致/异常而整条死掉（这次"完全不弹窗"的真因）。"""
from __future__ import annotations

import pytest

from plugins.reference.doc_index import DocIndex
from plugins.reference.word_watch import WordWatch


class _Text:
    def __init__(self, text: str):
        self.Text = text


class _Range:
    def __init__(self, start: int, end: int, text: str = ""):
        self.Start, self.End, self.Text = start, end, text


class _Field:
    def __init__(self, code: str, result: str, start: int, end: int):
        self.Code = _Text(code)
        self.Result = _Range(start, end, result)


class _Fields:
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def __call__(self, index):
        return self._items[index - 1]


class _Doc:
    Name = "论文.docx"
    FullName = "C:/tmp/论文.docx"

    def __init__(self, fields=None, fail_fields: bool = False):
        self._fail = fail_fields
        self.Fields = _Fields(fields or [])

    @property
    def Fields(self):                       # noqa: N802 - 模拟 COM 属性
        if self._fail:
            raise RuntimeError("读取域集合失败")
        return self._fields

    @Fields.setter
    def Fields(self, value):                # noqa: N802
        self._fields = value

    def Range(self, start, end):
        return _Range(start, end, "测试[5,6]")


class _Selection:
    Text = "[5,6]"
    Start = 4
    End = 4

    def Information(self, kind):
        return 100

    def Range(self, start, end):
        return _Range(start, end, "测试[5,6]")


class _Word:
    def __init__(self, doc):
        self.ActiveDocument = doc
        self.Selection = _Selection()

    def Range(self, start, end):
        return _Range(start, end, "测试[5,6]")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_tick_emits_all_labels_for_multi_citation(qapp):
    """`[5,6]` 必须一次给出两条 —— 顺带锁住 `_tick` 与 `_anchor_payload` 的签名一致。"""
    doc = _Doc([_Field(" ADDIN EN.JS.CITE ", "[5,6]", 2, 8)])
    word = _Word(doc)
    watch = WordWatch()
    seen: list[dict] = []
    watch.citationFound.connect(seen.append)

    watch._tick(lambda: word)      # 第一次：建索引
    watch._tick(lambda: word)      # 第二次：命中并上报

    assert seen, "命中引用必须发出信号（否则界面上就是'点了没弹窗'）"
    labels = [item["label"] for item in seen[-1]["items"]]
    assert labels == ["5", "6"]


def test_tick_does_not_raise_when_fields_unavailable(qapp):
    """域集合读不到时也不能抛异常把线程打死，且要记住文档名（否则每轮都重扫）。"""
    word = _Word(_Doc(fail_fields=True))
    watch = WordWatch()
    watch._tick(lambda: word)
    assert watch._index.doc_name == "论文.docx", "拿不到域也要记住文档名，避免无限重扫"
    watch._tick(lambda: word)      # 第二轮不应再重扫、也不应抛异常


def test_tick_keeps_working_when_label_parsing_fails(qapp, monkeypatch):
    """即便角标解析抛异常，单次循环也要被兜住（有 try/except），线程不能死。"""
    import plugins.reference.word_watch as wm

    def boom(*_args, **_kwargs):
        raise RuntimeError("解析炸了")

    monkeypatch.setattr(wm, "labels_at_position", boom)
    word = _Word(_Doc([_Field(" ADDIN EN.JS.CITE ", "[5]", 2, 8)]))
    watch = WordWatch()
    watch._tick(lambda: word)
    watch._tick(lambda: word)


def test_doc_index_retries_after_field_failure():
    """读域失败时**不能**标记成已扫描：否则这份文档永远不再重扫（点引用不弹窗）。"""
    index = DocIndex()
    assert index.rescan(_Word(_Doc(fail_fields=True))) == 0
    assert index.doc_name == "论文.docx", "拿不到域也要记住文档名"
    assert index.needs_rescan("C:/tmp/论文.docx") is True, "失败后下一轮必须允许重扫"
