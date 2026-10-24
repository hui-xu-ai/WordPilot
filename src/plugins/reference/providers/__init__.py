# plugins/reference/providers/__init__.py
"""文献来源解析器：把 Word 域代码里的 Zotero / EndNote 数据解析成统一的 Reference。"""
from __future__ import annotations

from plugins.reference.providers.endnote import parse_endnote_cite
from plugins.reference.providers.zotero import parse_csl_citation

__all__ = ["parse_csl_citation", "parse_endnote_cite"]
