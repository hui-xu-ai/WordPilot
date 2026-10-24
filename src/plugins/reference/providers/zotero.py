# plugins/reference/providers/zotero.py
"""Zotero 域代码解析。

Word 里的形态（fixture 实测）：
    ADDIN ZOTERO_ITEM CSL_CITATION {"citationID":"CqGvbdtv","properties":{...},
        "citationItems":[{"id":934,"itemData":{... "DOI":"10.1038/nature02593" ...}}]}

身份选择（对应 ADR-004）：
- 有 DOI 用 `doi:<doi>`（跨库迁移最稳）；
- 否则用 `zotero:<item id>`；
- `citationID` 是**每次插入都换**的实例 id，绝不能当身份；
- 两种键互为别名，任一命中都算同一篇文献。
"""
from __future__ import annotations

import json
import logging
import re

from plugins.reference.reference_store import Reference

logger = logging.getLogger(__name__)

_MARKER = re.compile(r"CSL_CITATION\s*", re.IGNORECASE)


def _norm_doi(doi: str | None) -> str:
    return (doi or "").strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")


def _year_from_issued(issued) -> str:
    if not isinstance(issued, dict):
        return ""
    parts = issued.get("date-parts") or []
    if parts and isinstance(parts[0], list) and parts[0]:
        return str(parts[0][0])
    if isinstance(issued.get("raw"), str):
        match = re.search(r"\d{4}", issued["raw"])
        return match.group(0) if match else ""
    return ""


def _authors(item: dict) -> str:
    names = []
    for author in item.get("author") or []:
        family = (author.get("family") or "").strip()
        given = (author.get("given") or "").strip()
        if family and given:
            names.append(f"{family} {given}")
        elif family or (author.get("literal") or "").strip():
            names.append(family or author.get("literal").strip())
    return "; ".join(names)


def _first_item(data: dict) -> dict:
    """`citationItems[i].itemData` 优先；兼容部分版本直接给平铺字段。"""
    items = data.get("citationItems") or []
    if items:
        item = items[0]
        if isinstance(item, dict):
            item_data = item.get("itemData")
            if isinstance(item_data, dict):
                return item_data
            return item
    return data


def parse_csl_citation(instr_text: str) -> Reference | None:
    """把一条 `ADDIN ZOTERO_ITEM CSL_CITATION {...}` 解析成 Reference。"""
    if not instr_text or not _MARKER.search(instr_text):
        return None
    start = instr_text.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(instr_text[start:])
    except (json.JSONDecodeError, ValueError):
        logger.debug("Zotero CSL JSON 解析失败")
        return None
    if not isinstance(data, dict):
        return None

    item = _first_item(data)
    item_id = str(item.get("id") or (data.get("citationItems") or [{}])[0].get("id") or "").strip()
    doi = _norm_doi(item.get("DOI"))
    if not item_id and not doi:
        return None

    ref_key = f"doi:{doi}" if doi else f"zotero:{item_id}"
    aliases = [alias for alias in (f"zotero:{item_id}" if item_id else "",
                                   f"doi:{doi}" if doi else "") if alias and alias != ref_key]
    return Reference(
        ref_key=ref_key,
        provider="zotero",
        provider_id=item_id,
        title=(item.get("title") or "").strip(),
        authors=_authors(item),
        year=_year_from_issued(item.get("issued")),
        container=(item.get("container-title") or "").strip(),
        doi=doi,
        url=(item.get("URL") or "").strip(),
        type=(item.get("type") or "").strip(),
        aliases=aliases,
    )
