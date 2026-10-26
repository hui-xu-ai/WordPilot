# tests/test_reference_doc_index.py
"""扫描 → 缓存 → 点击查表：域表只在换文档/首次时重建，点击只做二分查找。"""
from __future__ import annotations

import pytest

from plugins.reference.doc_index import DocAnchor, DocIndex, classify_field

ZOTERO_CODE = (
    ' ADDIN ZOTERO_ITEM CSL_CITATION {"citationItems":[{"id":934,"itemData":{"id":934,'
    '"title":"Global biodiversity","DOI":"10.1038/nature02593"}}]} '
)


class _Text:
    def __init__(self, text: str):
        self.Text = text


class _Range:
    def __init__(self, start: int, end: int, text: str = ""):
        self.Start = start
        self.End = end
        self.Text = text


class _Field:
    def __init__(self, code: str, result: str, start: int, end: int):
        self.Code = _Text(code)
        self.Result = _Range(start, end, result)


class _Fields:
    def __init__(self, fields):
        self._fields = fields
        self.Count = len(fields)

    def __call__(self, index):
        return self._fields[index - 1]


class _Doc:
    def __init__(self, name: str, fields):
        self.Name = name
        self.FullName = f"C:/tmp/{name}"        # S10：身份用全路径
        self.Fields = _Fields(fields)


class _Word:
    def __init__(self, name: str, fields):
        self.ActiveDocument = _Doc(name, fields)


def _fields():
    return [
        _Field(" ADDIN EN.JS.CITE ", "[1]", 10, 14),
        _Field(" ADDIN EN.JS.CITE ", "[5,6]", 40, 46),
        _Field(" SEQ 图 \\* ARABIC ", "图 1", 60, 64),
        _Field(ZOTERO_CODE, "[2]", 80, 84),
    ]


def test_classify_field_rules():
    assert classify_field(" ADDIN EN.JS.CITE ", "[1]", "论文.docx") == \
        [("doc:论文.docx:cite:1", "1", "endnote_shell")]
    assert classify_field(" SEQ 图 \\* ARABIC ", "图 1", "论文.docx") == []
    assert classify_field(" REF _Ref1 \\h ", "图 1", "论文.docx") == []
    anchors = classify_field(ZOTERO_CODE, "[2]", "论文.docx")
    assert anchors[0][0] == "doi:10.1038/nature02593"


def test_multi_label_shell_expands_to_each_number():
    anchors = classify_field(" ADDIN EN.JS.CITE ", "[5,6]", "论文.docx")
    assert [item[1] for item in anchors] == ["5", "6"]


def test_rescan_and_lookup_by_position():
    index = DocIndex()
    assert index.needs_rescan("C:/tmp/论文.docx") is True
    assert index.rescan(_Word("论文.docx", _fields())) == 4, "1、5、6 三个编号 + Zotero 一条"

    assert index.lookup(12).anchor_key == "doc:论文.docx:cite:1"
    assert [item.label for item in index.lookup_all(42)] == ["5", "6"], "`[5,6]` 一格多篇要全部返回"
    assert index.lookup(45).label == "5"
    assert index.lookup(62) is None, "题注域不是文献引用"
    assert index.lookup(82).anchor_key == "doi:10.1038/nature02593"
    assert index.lookup(1000) is None

    assert index.needs_rescan("C:/tmp/论文.docx") is False, "同一文档不需要重扫"


def test_rescan_when_document_changes():
    index = DocIndex()
    index.rescan(_Word("论文A.docx", _fields()))
    assert index.needs_rescan("C:/tmp/论文B.docx") is True
    index.rescan(_Word("论文B.docx", _fields()))
    assert index.doc_name == "论文B.docx"


def test_lookup_boundaries():
    index = DocIndex()
    index.rescan(_Word("论文.docx", [_Field(" ADDIN EN.JS.CITE ", "[1]", 10, 14)]))
    assert index.lookup(10) is not None, "起始字符命中"
    assert index.lookup(14) is not None, "结束字符命中"
    assert index.lookup(9) is None
    assert index.lookup(15) is None


def test_rescan_survives_broken_fields():
    class _Broken:
        Count = 2

        def __call__(self, index):
            if index == 1:
                raise RuntimeError("损坏的域")
            return _Field(" ADDIN EN.JS.CITE ", "[7]", 20, 24)

    class _Doc2:
        Name = "论文.docx"
        Fields = _Broken()

    class _Word2:
        ActiveDocument = _Doc2()

    index = DocIndex()
    assert index.rescan(_Word2()) == 1, "单个域报错不能打断整次扫描"
    assert index.lookup(22).label == "7"


def test_empty_document_returns_zero():
    index = DocIndex()
    assert index.rescan(_Word("空.docx", [])) == 0
    assert index.lookup(0) is None


def test_doc_anchor_defaults():
    anchor = DocAnchor(start=1, end=2, anchor_key="doc:x:cite:1")
    assert anchor.label == "" and anchor.kind == ""


@pytest.mark.parametrize("code", ["", "   ", " STYLEREF 1 \\s "])
def test_ignored_fields(code):
    assert classify_field(code, "图 1", "论文.docx") == []

def test_load_from_cache_skips_rescan():
    """从缓存装载锚点后，不需要再遍历 COM 域。"""
    index = DocIndex()
    count = index.load("C:/tmp/论文.docx", [DocAnchor(5, 9, "doc:论文.docx:cite:3", "3", "endnote_shell")])
    assert count == 1
    assert index.needs_rescan("C:/tmp/论文.docx") is False, "装载后不该再要求重扫"
    assert index.lookup(7).label == "3"
