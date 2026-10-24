# plugins/reference/providers/endnote.py
"""EndNote 域代码解析。

Word 里的形态（EndNote 未转纯文本时）：
    ADDIN EN.CITE <EndNote><Cite><Author>…</Author><Year>2004</Year>
        <RecNum>12345</RecNum><record><rec-number>12345</rec-number>
        <ref-type name="Journal Article">17</ref-type>
        <contributors><authors><author>Smith J</author></authors></contributors>
        <titles><title>标题</title><secondary-title>期刊名</secondary-title></titles>
        <dates><year>2004</year></dates>
        <electronic-resource-num>10.1/x</electronic-resource-num></record></Cite></EndNote>

身份：`endnote:<rec-number>`（库内稳定）；有 DOI 时优先 `doi:<doi>` 并互为别名。
注意 fixture 实测：EndNote 文档里绝大多数引用已被"转成纯文本"，只有 7 个域，
所以纯文本引用不在本插件处理范围内（用户明确不提倡这种编号方式）。
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from plugins.reference.reference_store import Reference

logger = logging.getLogger(__name__)

_FIELD_PREFIX = re.compile(r"^\s*ADDIN\s+EN\.(?:JS\.)?CITE\b", re.IGNORECASE)
_XML_START = re.compile(r"<EndNote\b", re.IGNORECASE)


def _norm_doi(doi: str | None) -> str:
    return (doi or "").strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def parse_endnote_cite(instr_text: str) -> Reference | None:
    """把一条 `ADDIN EN.CITE <EndNote>…</EndNote>` 解析成 Reference。"""
    if not instr_text or not _FIELD_PREFIX.search(instr_text):
        return None
    match = _XML_START.search(instr_text)
    if not match:
        return None
    xml_fragment = instr_text[match.start():]
    try:
        root = ET.fromstring(xml_fragment)
    except ET.ParseError:
        logger.debug("EndNote XML 片段解析失败")
        return None

    record = root.find(".//record")
    scope = record if record is not None else root
    rec_number = _text(scope.find("rec-number")) or _text(root.find(".//RecNum"))
    doi = _norm_doi(_text(scope.find("electronic-resource-num")))
    if not rec_number and not doi:
        return None

    authors = [_text(author) for author in scope.findall(".//contributors/authors/author")]
    ref_type = ""
    type_node = scope.find("ref-type")
    if type_node is not None:
        ref_type = (type_node.get("name") or "").strip()

    ref_key = f"doi:{doi}" if doi else f"endnote:{rec_number}"
    aliases = [alias for alias in (f"endnote:{rec_number}" if rec_number else "",
                                   f"doi:{doi}" if doi else "") if alias and alias != ref_key]
    return Reference(
        ref_key=ref_key,
        provider="endnote",
        provider_id=rec_number,
        title=_text(scope.find("titles/title")) or _text(scope.find(".//title")),
        authors="; ".join(name for name in authors if name),
        year=_text(scope.find("dates/year")) or _text(root.find(".//Year")),
        container=_text(scope.find("titles/secondary-title")),
        doi=doi,
        url=_text(scope.find("urls/url")) or _text(scope.find("url")),
        type=ref_type,
        aliases=aliases,
    )
