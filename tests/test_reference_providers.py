# tests/test_reference_providers.py
"""Zotero / EndNote 域代码解析：身份键优先级、别名、以及解析失败要安静返回 None。"""
from __future__ import annotations

import json

import pytest

from plugins.reference.providers.endnote import parse_endnote_cite
from plugins.reference.providers.zotero import parse_csl_citation

# 与 fixtures 里实测到的结构一致（关键字段齐备）
ZOTERO_FIELD = (
    ' ADDIN ZOTERO_ITEM CSL_CITATION {"citationID":"CqGvbdtv","properties":'
    '{"formattedCitation":"\\\\super [1]\\\\nosupersub{}","plainCitation":"[1]","noteIndex":0},'
    '"citationItems":[{"id":934,"itemData":{"id":934,"type":"article-journal",'
    '"title":"Global biodiversity patterns of marine phytoplankton and zooplankton",'
    '"container-title":"Nature","DOI":"10.1038/nature02593","issued":{"date-parts":[[2004]]},'
    '"author":[{"family":"Tittensor","given":"Derek P"},{"family":"Mora","given":"C"}],'
    '"URL":"https://doi.org/10.1038/nature02593"}}],"schema":"https://github.com/citation-style-language/schema/raw/master/csl-citation.json"} '
)

ENDNOTE_FIELD = (
    " ADDIN EN.CITE <EndNote><Cite><Author>Smith</Author><Year>2004</Year><RecNum>12345</RecNum>"
    "<record><rec-number>12345</rec-number><ref-type name=\"Journal Article\">17</ref-type>"
    "<contributors><authors><author>Smith J</author><author>Wang L</author></authors></contributors>"
    "<titles><title>某篇论文</title><secondary-title>中国科学</secondary-title></titles>"
    "<dates><year>2004</year></dates><electronic-resource-num>10.1000/xyz</electronic-resource-num>"
    "</record></Cite></EndNote> "
)


def test_zotero_prefers_doi_as_identity():
    ref = parse_csl_citation(ZOTERO_FIELD)
    assert ref is not None
    assert ref.ref_key == "doi:10.1038/nature02593", "有 DOI 时用 DOI 作身份（跨库迁移最稳）"
    assert "zotero:934" in ref.aliases, "Zotero item id 必须作为别名保留"
    assert ref.provider == "zotero" and ref.provider_id == "934"
    assert ref.title.startswith("Global biodiversity")
    assert ref.container == "Nature"
    assert ref.year == "2004"
    assert "Tittensor" in ref.authors and "Mora" in ref.authors


def test_zotero_without_doi_uses_item_id():
    data = {"citationItems": [{"id": 42, "itemData": {"id": 42, "title": "无 DOI 条目"}}]}
    ref = parse_csl_citation(' ADDIN ZOTERO_ITEM CSL_CITATION ' + json.dumps(data))
    assert ref is not None
    assert ref.ref_key == "zotero:42"
    assert ref.aliases == [] or "doi:" not in "".join(ref.aliases)


def test_zotero_citation_id_is_not_identity():
    """citationID 每次插入都变，绝不能当身份。"""
    ref = parse_csl_citation(ZOTERO_FIELD)
    assert ref is not None
    assert "CqGvbdtv" not in ref.ref_key and "CqGvbdtv" not in "".join(ref.aliases)


@pytest.mark.parametrize("bad", [
    "",
    " ADDIN ZOTERO_ITEM CSL_CITATION {broken json",
    " SEQ 图 \\* ARABIC ",
    " ADDIN ZOTERO_ITEM CSL_CITATION {}",
])
def test_zotero_garbage_returns_none(bad):
    assert parse_csl_citation(bad) is None


def test_endnote_parses_record_fields():
    ref = parse_endnote_cite(ENDNOTE_FIELD)
    assert ref is not None
    assert ref.ref_key == "doi:10.1000/xyz"
    assert "endnote:12345" in ref.aliases
    assert ref.provider_id == "12345"
    assert ref.title == "某篇论文"
    assert ref.container == "中国科学"
    assert ref.year == "2004"
    assert ref.authors == "Smith J; Wang L"
    assert ref.type == "Journal Article"


def test_endnote_without_doi_falls_back_to_rec_number():
    field = (' ADDIN EN.CITE <EndNote><Cite><record><rec-number>777</rec-number>'
             '<titles><title>无 DOI</title></titles></record></Cite></EndNote> ')
    ref = parse_endnote_cite(field)
    assert ref is not None
    assert ref.ref_key == "endnote:777"


@pytest.mark.parametrize("bad", [
    "",
    " ADDIN EN.CITE <EndNote><Cite><record><titles>",
    " ADDIN ZOTERO_ITEM CSL_CITATION {}",
    " ADDIN EN.CITE <EndNote><Cite><record><rec-number></rec-number></record></Cite></EndNote>",
])
def test_endnote_garbage_returns_none(bad):
    assert parse_endnote_cite(bad) is None


def test_doi_normalisation_strips_prefixes():
    data = {"citationItems": [{"id": 1, "itemData": {"id": 1, "DOI": "https://doi.org/10.1/ABC"}}]}
    ref = parse_csl_citation(' ADDIN ZOTERO_ITEM CSL_CITATION ' + json.dumps(data))
    assert ref is not None and ref.ref_key == "doi:10.1/abc"
