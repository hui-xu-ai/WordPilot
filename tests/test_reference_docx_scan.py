# tests/test_reference_docx_scan.py
"""离线 docx 扫描：三类锚点（provider 域 / 脚注尾注 / REF 交叉引用）+ 编号拆分。

用一个**合成的最小 docx**（zip + document.xml）覆盖解析路径，避免测试依赖 Word；
再用 `local/fixtures/` 里的真实文档校验覆盖率（文件不存在则跳过）。
"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

from plugins.reference.docx_scan import (
    footnote_texts,
    parse_citation_labels,
    scan_docx,
)

FIXTURES = Path("local/fixtures")
ENDNOTE_DOCX = FIXTURES / "EndNote文献论文测试.docx"
ZOTERO_DOCX = FIXTURES / "Zotero文献论文测试.docx"

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

ZOTERO_INSTR = (
    ' ADDIN ZOTERO_ITEM CSL_CITATION {"citationItems":[{"id":934,"itemData":'
    '{"id":934,"type":"article-journal","title":"Global biodiversity","container-title":"Nature",'
    '"DOI":"10.1038/nature02593","issued":{"date-parts":[[2004]]},'
    '"author":[{"family":"Tittensor","given":"Derek P"}]}}]} '
)

DOC_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document {W_NS}><w:body>
  <w:p><w:r><w:t>浮游生物多样性研究[1]</w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"/></w:r>
    <w:r><w:instrText>{ZOTERO_INSTR.replace("&", "&amp;").replace("<", "&lt;")}</w:instrText></w:r>
    <w:r><w:fldChar w:fldCharType="separate"/></w:r>
    <w:r><w:t>1</w:t></w:r>
    <w:r><w:fldChar w:fldCharType="end"/></w:r>
  </w:p>
  <w:p><w:r><w:t>见脚注说明</w:t></w:r><w:r><w:footnoteReference w:id="7"/></w:r></w:p>
  <w:p><w:r><w:t>如图 1 所示，范围引用[1-3]与并列[5, 7]。</w:t></w:r></w:p>
  <w:p><w:fldSimple w:instr=" REF _Ref225454536 \\h "><w:r><w:t>图 1</w:t></w:r></w:fldSimple></w:p>
</w:body></w:document>
"""


@pytest.fixture(scope="module")
def synthetic_docx(tmp_path_factory):
    path = tmp_path_factory.mktemp("docx") / "合成论文.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", DOC_XML)
        zf.writestr(
            "word/footnotes.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><w:footnotes {W_NS}>'
            '<w:footnote w:id="7"><w:p><w:r><w:t>这是一条脚注</w:t></w:r></w:p></w:footnote>'
            "</w:footnotes>",
        )
    return path


# ---------------- 编号拆分 ----------------
@pytest.mark.parametrize(("text", "expected"), [
    ("[1]", ["1"]),
    ("[1,2]", ["1", "2"]),
    ("[1-3]", ["1", "2", "3"]),
    ("结果[1, 3-5]显示", ["1", "3", "4", "5"]),
    ("[12]与[12]重复", ["12"]),
    ("无引用的一段话", []),
    ("[图1]", []),
    ("[1-100]", []),            # 明显不是引用（区间过大）
])
def test_parse_citation_labels(text, expected):
    assert parse_citation_labels(text) == expected


# ---------------- 合成文档 ----------------
def test_scan_finds_provider_citation(synthetic_docx):
    result = scan_docx(synthetic_docx)

    assert result.doc_name == "合成论文.docx"
    provider = result.provider_citations
    assert len(provider) == 1
    citation = provider[0]
    assert citation.anchor_key == "doi:10.1038/nature02593"
    assert citation.reference is not None and citation.reference.title == "Global biodiversity"
    assert citation.label == "1", "编号应从所在段落里取（[1]）"


def test_scan_finds_footnote_anchor(synthetic_docx):
    """脚注身份用正文哈希（复制/重编号都不失效），不是 Word 的内部 id。"""
    from plugins.reference.docx_scan import footnote_anchor

    result = scan_docx(synthetic_docx)
    footnote = [c for c in result.citations if c.kind == "footnote"]
    assert result.footnote_count == 1
    expected = footnote_anchor("合成论文.docx", "这是一条脚注", "7")
    assert footnote and footnote[0].anchor_key == expected


def test_scan_finds_cross_reference_field(synthetic_docx):
    result = scan_docx(synthetic_docx)
    refs = [c for c in result.citations if c.kind == "ref"]
    assert result.ref_count == 1
    assert refs and refs[0].anchor_key == "doc:合成论文.docx:ref:_Ref225454536"


def test_scan_collects_labels_without_duplicates(synthetic_docx):
    result = scan_docx(synthetic_docx)
    assert {"1", "2", "3", "5", "7"} <= set(result.labels)


def test_scan_ignores_manual_plain_number_citation(synthetic_docx):
    """非目标：用户手敲编号不进引用列表（只作为 labels 候选）。"""
    result = scan_docx(synthetic_docx)
    anchors = [c.anchor_key for c in result.citations]
    assert all(not anchor.startswith("label:") for anchor in anchors)


def test_footnote_texts_extracted(synthetic_docx):
    assert footnote_texts(synthetic_docx) == {"7": "这是一条脚注"}


def test_broken_zip_is_reported(tmp_path):
    broken = tmp_path / "坏文件.docx"
    broken.write_bytes(b"not a zip")
    result = scan_docx(broken)
    assert result.warnings and "无法打开文档" in result.warnings[0]
    assert result.citations == []


def test_missing_document_xml_is_reported(tmp_path):
    path = tmp_path / "空.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/other.xml", "<x/>")
    result = scan_docx(path)
    assert any("document.xml" in warning for warning in result.warnings)


# ---------------- 真实 fixture ----------------
@pytest.mark.skipif(not ENDNOTE_DOCX.exists(), reason="缺少 EndNote fixture")
def test_real_endnote_fixture():
    started = time.perf_counter()
    result = scan_docx(ENDNOTE_DOCX)
    elapsed = time.perf_counter() - started

    assert result.ref_count >= 21, f"fixture 里应有 ≥21 个 REF 交叉引用，实际 {result.ref_count}"
    assert len(result.labels) >= 10, "正文里应能扫到编号"
    assert elapsed < 6.0, f"40 MB 文档扫描过慢：{elapsed:.2f}s"


@pytest.mark.skipif(not ZOTERO_DOCX.exists(), reason="缺少 Zotero fixture")
def test_real_zotero_fixture():
    started = time.perf_counter()
    result = scan_docx(ZOTERO_DOCX)
    elapsed = time.perf_counter() - started

    assert result.provider_citations, "Zotero 文档应解析出带元数据的引用"
    first = result.provider_citations[0]
    assert first.reference is not None
    assert first.reference.provider == "zotero"
    assert elapsed < 8.0, f"105 MB 文档扫描过慢：{elapsed:.2f}s"
