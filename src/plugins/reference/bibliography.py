# plugins/reference/bibliography.py
"""从"参考文献列表"里反推文献元数据（EndNote/Zotero 把引用压成纯文本时的救命路径）。

实测场景：EndNote 插入的引用域常常只剩 `ADDIN EN.JS.CITE` 空壳（数据在 .Data 文件或库里），
但**文末的参考文献列表是完整可读的文本**：

    [1] Wang D P, Zhao S F, Li L L, et al. All-Flexible Artificial Reflex Arc Based on
        Threshold-Switching Memristor[J]. Advanced Functional Materials, 2022, 32(21).

于是：
- 身份键用 `bib:<标题哈希>`（**跨文档稳定**，另一篇论文引用同一篇文献时备注能复用）；
- 别名里挂 `doc:<文档>:cite:<编号>`（文档内编号 → 同一篇文献），
  这样 EndNote 空壳引用也能解析出真实信息。
"""
from __future__ import annotations

import hashlib
import logging
import re

from plugins.reference.reference_store import Reference

logger = logging.getLogger(__name__)

#: 条目起始：`[1]` / `[1]` 带前导空格 / `1.` / `1)`
_ENTRY_START = re.compile(r"^\s*\[?(\d{1,4})\]?[.、)]?\s+(?=\S)")
#: 文献类型标识：[J] [M] [D] [C] [EB/OL] …
_TYPE_MARK = re.compile(r"\[(J|M|D|C|N|R|S|P|G|Z|EB/OL|J/OL|M/OL)\]", re.IGNORECASE)
_YEAR = re.compile(r"(19|20)\d{2}")
_BIB_HEADING = re.compile(r"^\s*(参考文献|references?|bibliography)\s*$", re.IGNORECASE)


def find_heading_index(paragraphs: list[str]) -> int | None:
    """找"参考文献 / References"标题所在位置，返回其后第一条的索引。"""
    for index, paragraph in enumerate(paragraphs):
        if _BIB_HEADING.match(paragraph or ""):
            return index + 1
    return None


def _split_name_list(text: str) -> str:
    return "; ".join(part.strip() for part in text.split(",") if part.strip())


def parse_entry(raw: str, index: str, doc_name: str = "") -> Reference | None:
    """把一条著录文本解析成 Reference（作者/标题/期刊/年份尽量取到）。"""
    text = " ".join((raw or "").split())
    if len(text) < 8:
        return None
    body = _ENTRY_START.sub("", text).strip()

    type_mark = _TYPE_MARK.search(body)
    before_mark = body[:type_mark.start()].strip() if type_mark else body
    after_mark = body[type_mark.end():].lstrip(" .．:：").strip() if type_mark else ""

    # 作者与标题之间通常是 ". "（有时是 "．"）
    segments = re.split(r"[.．]\s+", before_mark, maxsplit=1)
    authors = _split_name_list(segments[0]) if len(segments) == 2 else ""
    title = (segments[1] if len(segments) == 2 else before_mark).strip(" .．")

    container = ""
    if after_mark:
        container = re.split(r"[,，.]", after_mark, maxsplit=1)[0].strip()
    year_match = _YEAR.search(after_mark or body)
    year = year_match.group(0) if year_match else ""

    if not title:
        return None
    digest = hashlib.sha1(title.lower().encode("utf-8")).hexdigest()[:10]
    aliases = [f"doc:{doc_name}:cite:{index}"] if doc_name else []
    return Reference(
        ref_key=f"bib:{digest}",
        provider="bibliography",
        provider_id=index,
        cite_label=index,
        title=title,
        authors=authors,
        year=year,
        container=container,
        aliases=aliases,
    )


def _looks_like_entry(text: str) -> bool:
    """条目的最小特征：有文献类型标识，或"年份 + 足够多标点"（避免把表格行当成文献）。"""
    if _TYPE_MARK.search(text):
        return True
    if not _YEAR.search(text):
        return False
    return text.count(",") + text.count("，") >= 2 and len(text) >= 30


def parse_bibliography(paragraphs: list[str], doc_name: str = "") -> list[Reference]:
    """把"参考文献"章节的段落合并成条目列表（续行自动拼接）。

    只解析**标题之后**的内容：实测 EndNote fixture 的文末表格里"1  xxx  2  xxx"
    这种编号行会被误判成文献（一次误收 20+ 条）。
    """
    start = find_heading_index(paragraphs)
    scoped = paragraphs[start:] if start is not None else list(paragraphs)
    entries: list[tuple[str, str]] = []
    current_index: str | None = None
    buffer: list[str] = []

    for paragraph in scoped:
        text = (paragraph or "").strip()
        if not text:
            continue
        match = _ENTRY_START.match(text)
        if match and (len(text) > 12 or match.group(1).isdigit()):
            if current_index is not None and buffer:
                entries.append((current_index, " ".join(buffer)))
            current_index = match.group(1)
            buffer = [text]
        elif current_index is not None:
            buffer.append(text)          # 续行
    if current_index is not None and buffer:
        entries.append((current_index, " ".join(buffer)))

    if start is None and len(entries) < 3:
        return []
    references = []
    for index, raw in entries:
        if not _looks_like_entry(raw):
            continue
        reference = parse_entry(raw, index, doc_name)
        if reference is not None:
            references.append(reference)
    logger.debug("参考文献列表解析出 %d 条（原始条目 %d）", len(references), len(entries))
    return references


def find_heading_index(paragraphs: list[str]) -> int | None:
    """找"参考文献 / References"标题所在位置，返回其后第一条的索引。"""
    for index, paragraph in enumerate(paragraphs):
        if _BIB_HEADING.match(paragraph or ""):
            return index + 1
    return None
