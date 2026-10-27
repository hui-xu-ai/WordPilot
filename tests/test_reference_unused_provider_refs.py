# tests/test_reference_unused_provider_refs.py
"""域引用只在"同一篇已在文末列表里"时用于补全，多出来的不入库。

现场（Zotero 测试文档）：文末列表 25 条（编号 1..25，键 `bib:<hash>`），域内 18 条元数据
（键 `doi:...`、**全无编号**），两者重叠 9 条 —— 旧实现两边都入库，子库里就出现大量
"没编号、且与有编号的重复"的条目（用户现场）。
"""
from __future__ import annotations

from plugins.reference.docx_scan import (ScanCitation, ScanResult, merge_provider_citations,
                                         work_identity)
from plugins.reference.reference_store import Reference


def _ref(key, title, doi="", label="", **kw) -> Reference:
    return Reference(ref_key=key, title=title, doi=doi, cite_label=label, **kw)


def _result(references, providers) -> ScanResult:
    citations = [ScanCitation(anchor_key=f"c{i}", kind="zotero", reference=ref)
                 for i, ref in enumerate(providers)]
    return ScanResult(doc_name="论文.docx", citations=citations, references=references)


def test_work_identity_prefers_doi_then_title():
    assert work_identity(_ref("a", "T", doi="10.1/ABC")) == "doi:10.1/abc"
    assert (work_identity(_ref("a", "An Overview of AUVs"))
            == work_identity(_ref("b", "an overview of auvs.")))


def test_extra_provider_refs_are_dropped():
    refs = [_ref("bib:1", "Smart Film Actuators", label="1"),
            _ref("bib:2", "Recent Progress", label="2")]
    providers = [_ref("doi:smart", "Smart Film Actuators"),        # 与列表同一篇
                 _ref("doi:extra", "未被引用的另一篇")]             # 列表里没有

    candidates, filled, ignored = merge_provider_citations(_result(refs, providers))

    assert [ref.ref_key for ref in candidates] == ["bib:1", "bib:2"], "只入文末列表"
    assert ignored == 1, "列表里没有的域引用要丢弃"
    assert filled >= 0


def test_provider_metadata_fills_missing_fields():
    refs = [_ref("bib:1", "Smart Film Actuators", label="1")]      # 缺 doi/authors
    providers = [_ref("doi:10.1002/1", "Smart Film Actuators",
                      doi="10.1002/1", authors="Zhang Z H", year="2022")]

    candidates, filled, ignored = merge_provider_citations(_result(refs, providers))

    assert ignored == 0
    assert filled == 1
    target = candidates[0]
    assert target.doi == "10.1002/1" and target.authors == "Zhang Z H", "缺的字段要补齐"
    assert target.cite_label == "1", "编号来自文末列表，不能被域元数据覆盖"


def test_document_without_bibliography_falls_back_to_provider_refs():
    """没有文末列表的文档（EndNote 纯域）仍要能入库，否则什么也拿不到。"""
    providers = [_ref("doc:cite:1", "只有域元数据的文献")]

    candidates, _filled, ignored = merge_provider_citations(_result([], providers))

    assert [ref.ref_key for ref in candidates] == ["doc:cite:1"]
    assert ignored == 0
