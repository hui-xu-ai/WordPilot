# plugins/reference/docx_scan.py
"""离线解析 .docx，抽出"可挂备注的引用锚点"。

为什么离线解析：走 Word COM 取域代码要遍历 `doc.Fields`（2000 个域 ≈ 数秒，且要求 Word 正在运行）。
`zipfile + ET.iterparse` 流式读 `word/document.xml`，40 MB 文档实测 67–75 ms（同 navigator 的做法）。

支持的三类锚点（对应 ADR-004）：
1. `ADDIN ZOTERO_ITEM CSL_CITATION {...}` / `ADDIN EN.CITE …` → provider 身份（`doi:` 或 `zotero:`/`endnote:`）；
2. 脚注 / 尾注引用 → `doc:<文档名>:fn:<id>` / `doc:<文档名>:en:<id>`；
3. 交叉引用域 `REF _Ref…`（题注/编号项的引用）→ `doc:<文档名>:ref:<书签名>`。

**不处理**用户手敲的编号文本（无域、无法追溯来源）——这是明确的非目标。
"""
from __future__ import annotations

import hashlib
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Iterator

from plugins.reference.bibliography import parse_bibliography
from plugins.reference.providers.endnote import parse_endnote_cite
from plugins.reference.providers.zotero import parse_csl_citation
from plugins.reference.reference_store import Reference

logger = logging.getLogger(__name__)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_LABEL_GROUP = re.compile(r"[\[（(]([0-9]{1,3}(?:\s*[-–—~,，、]\s*[0-9]{1,3})*)[\]）)]")
_REF_BOOKMARK = re.compile(r'^\s*REF\s+(\S+)', re.IGNORECASE)
#: EndNote 只有域壳、没有内嵌数据的旧/新格式
_ENDNOTE_SHELL = re.compile(r'^\s*ADDIN\s+EN\.(?:JS\.)?CITE(?:\.DATA)?\b', re.IGNORECASE)


def footnote_anchor(doc_name: str, text: str, fallback: str, prefix: str = "fn") -> str:
    """脚注/尾注的身份键：优先用正文哈希（复制/重编号后仍稳定），正文为空才退回 id。"""
    payload = " ".join((text or "").split())
    if not payload:
        return f"doc:{doc_name}:{prefix}:{fallback}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    return f"doc:{doc_name}:{prefix}:{digest}"


def parse_citation_labels(text: str) -> list[str]:
    """从一段文字里取出引用编号：`[1]` `[1,2]` `[1-3]` `[1, 3-5]` → ['1','2','3'…]。"""
    labels: list[str] = []
    for group in _LABEL_GROUP.findall(text or ""):
        for chunk in re.split(r"[,，、]", group):
            chunk = chunk.strip()
            if not chunk:
                continue
            match = re.fullmatch(r"(\d{1,3})\s*[-–—~]\s*(\d{1,3})", chunk)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                if start <= end and end - start <= 60:
                    labels.extend(str(value) for value in range(start, end + 1))
                continue
            if chunk.isdigit():
                labels.append(chunk)
    # 去重但保持出现顺序
    return list(dict.fromkeys(labels))


@dataclass
class ScanCitation:
    """文档里的一处引用锚点。"""

    anchor_key: str
    kind: str                       # zotero | endnote | footnote | endnote_ref | ref
    label: str = ""
    first_para: str = ""
    reference: Reference | None = None

    @property
    def has_metadata(self) -> bool:
        return self.reference is not None


@dataclass
class ScanResult:
    doc_name: str = ""
    citations: list[ScanCitation] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    footnote_count: int = 0
    endnote_count: int = 0
    ref_count: int = 0
    #: 文末"参考文献"列表解析出的文献（可为空：文档没有列表或格式不认）
    references: list[Reference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def provider_citations(self) -> list[ScanCitation]:
        return [c for c in self.citations if c.reference is not None]


#: 入库时"按同一篇文献"比较标题用：去掉标点/空白、统一小写
_TITLE_NOISE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def work_identities(ref: Reference) -> list[str]:
    """同一篇文献的**所有**身份线索（DOI + 规范化标题）。

    两边的键不一样、字段也未必齐全：文末列表那条可能没 DOI，域里那条有 DOI 却没编号——
    所以用"任一身份命中"来判定同一篇（只用 DOI 会漏、只用标题会误）。
    """
    out: list[str] = []
    doi = (getattr(ref, "doi", "") or "").strip().lower()
    key = (getattr(ref, "ref_key", "") or "").strip().lower()
    if not doi and key.startswith("doi:"):
        doi = key[4:]                      # 旧条目常把 DOI 写在键里（`doi:10.xxxx/...`）
    if doi:
        out.append(f"doi:{doi}")
    title = _TITLE_NOISE.sub("", (getattr(ref, "title", "") or "").lower())
    if title:
        out.append(f"title:{title}")
    return out or [f"key:{getattr(ref, 'ref_key', '')}"]


def work_identity(ref: Reference) -> str:
    """同一篇文献的首选身份（DOI 优先，其次规范化标题）。"""
    return work_identities(ref)[0]


def merge_provider_citations(result: ScanResult) -> tuple[list[Reference], int, int]:
    """算出入库候选：**以文末参考文献列表为准**，域元数据只补全同一篇，不新增。

    为什么：Zotero 文档里每个域都带一份元数据，但键是 `doi:...`、**没有编号**，而文末列表
    已经按 1..N 编号列出同一批文献。两边都入库 → 子库里出现大量"没编号、且与有编号的重复"
    的条目（用户现场）。这里的规则：
    - 有文末列表：列表全量入库；域引用按身份（DOI 或标题任一命中）匹配，只补空字段，
      匹配不上的**丢弃**；
    - 没有文末列表（如 EndNote 纯域文档）：退化成都入库，否则什么也拿不到。

    返回 (候选, 补全条数, 丢弃条数)。
    """
    references = list(result.references)
    if not references:
        return [c.reference for c in result.provider_citations if c.reference is not None], 0, 0

    by_identity: dict[str, Reference] = {}
    for ref in references:
        for identity in work_identities(ref):
            by_identity.setdefault(identity, ref)

    filled = ignored = 0
    for citation in result.provider_citations:
        incoming = citation.reference
        if incoming is None:
            continue
        target = next((by_identity[key] for key in work_identities(incoming)
                       if key in by_identity), None)
        if target is None:
            ignored += 1
            continue
        changed = False
        for attr in ("title", "authors", "year", "container", "doi"):
            if not getattr(target, attr, "") and getattr(incoming, attr, ""):
                setattr(target, attr, getattr(incoming, attr))
                changed = True
        if changed:
            filled += 1
    return references, filled, ignored


def _open_document(source: str | Path | bytes) -> BytesIO | zipfile.ZipFile:
    if isinstance(source, (bytes, bytearray)):
        buffer = BytesIO(source)
        return zipfile.ZipFile(buffer)
    return zipfile.ZipFile(str(source))


def footnote_texts(source: str | Path | bytes, member: str = "word/footnotes.xml") -> dict[str, str]:
    """`{footnote id: 文本}`（脚注/尾注正文，用于"按文本重绑"）。"""
    texts: dict[str, str] = {}
    try:
        archive = _open_document(source)
    except (OSError, zipfile.BadZipFile):
        return texts
    with archive as zf:
        if member not in zf.namelist():
            return texts
        try:
            with zf.open(member) as handle:
                buffer: list[str] = []
                for _event, el in ET.iterparse(handle, events=("end",)):
                    if el.tag in (f"{W}footnote", f"{W}endnote"):
                        ref_id = el.get(f"{W}id", "")
                        text = "".join(buffer).strip()
                        if ref_id and text:
                            texts[ref_id] = text
                        buffer.clear()
                        el.clear()
                    elif el.tag in (f"{W}t", f"{W}delText"):
                        buffer.append(el.text or "")
                        el.clear()
        except (OSError, ET.ParseError) as exc:
            logger.debug("脚注解析失败：%s", exc)
    return texts


def _iter_elements(archive: zipfile.ZipFile, member: str) -> Iterator[ET.Element]:
    with archive.open(member) as handle:
        for _event, el in ET.iterparse(handle, events=("end",)):
            yield el


def scan_docx(source: str | Path | bytes, doc_name: str | None = None) -> ScanResult:
    """扫描一个 .docx，返回引用锚点清单（不触碰 Word）。"""
    if doc_name is None:
        doc_name = Path(source).name if not isinstance(source, (bytes, bytearray)) else "document.docx"
    result = ScanResult(doc_name=doc_name)

    try:
        archive = _open_document(source)
    except (OSError, zipfile.BadZipFile) as exc:
        result.warnings.append(f"无法打开文档：{exc}")
        return result

    with archive as zf:
        if "word/document.xml" not in zf.namelist():
            result.warnings.append("文档缺少 word/document.xml")
            return result

        para_buffer: list[str] = []
        paragraph_texts: list[str] = []
        note_anchors: list[tuple[ScanCitation, str, str]] = []
        instr_buffer: list[str] = []
        in_field = False
        instr_ready = False
        superscript_pending = False
        previous_paragraph = ""

        def flush_field() -> None:
            nonlocal instr_buffer, in_field, instr_ready
            instruction = "".join(instr_buffer).strip()
            context = "".join(para_buffer).strip() or previous_paragraph
            instr_buffer, in_field, instr_ready = [], False, False
            if not instruction:
                return
            labels = parse_citation_labels(context)
            label = labels[0] if labels else ""

            reference = parse_csl_citation(instruction) or parse_endnote_cite(instruction)
            if reference is not None:
                kind = reference.provider or "zotero"
                result.citations.append(ScanCitation(
                    anchor_key=reference.ref_key, kind=kind, label=label,
                    first_para=context[:120], reference=reference))
                return

            match = _REF_BOOKMARK.match(instruction)
            if match and match.group(1).startswith("_Ref"):
                # 题注/编号项交叉引用：只计数，**不再产出可弹窗的锚点**
                #（用户点导航跳到图注时曾被误触发）
                result.ref_count += 1
                return
            if _ENDNOTE_SHELL.match(instruction) and label:
                # EndNote 只写了域壳（数据不在文档里）：按文档编号建锚点，等用户绑定
                result.citations.append(ScanCitation(
                    anchor_key=f"doc:{doc_name}:cite:{label}", kind="endnote_shell",
                    label=label, first_para=context[:120]))

        try:
            for el in _iter_elements(zf, "word/document.xml"):
                tag = el.tag
                if tag == f"{W}t":
                    text = el.text or ""
                    para_buffer.append(text)
                    if superscript_pending and text.strip().isdigit():
                        result.labels.append(text.strip())
                    el.clear()
                elif tag == f"{W}instrText":
                    if in_field:
                        instr_buffer.append(el.text or "")
                    el.clear()
                elif tag == f"{W}fldChar":
                    kind = el.get(f"{W}fldCharType", "")
                    if kind == "begin":
                        in_field, instr_ready, instr_buffer = True, False, []
                    elif kind == "separate":
                        instr_ready = True
                    elif kind == "end":
                        flush_field()
                    el.clear()
                elif tag == f"{W}fldSimple":
                    instruction = el.get(f"{W}instr", "") or ""
                    context = "".join(para_buffer).strip()
                    reference = parse_csl_citation(instruction) or parse_endnote_cite(instruction)
                    if reference is not None:
                        result.citations.append(ScanCitation(
                            anchor_key=reference.ref_key, kind=reference.provider or "zotero",
                            label=(parse_citation_labels(context) or [""])[0],
                            first_para=context[:120], reference=reference))
                    else:
                        match = _REF_BOOKMARK.match(instruction)
                        if match and match.group(1).startswith("_Ref"):
                            result.ref_count += 1        # 只计数，不产出锚点
                        elif _ENDNOTE_SHELL.match(instruction):
                            shell_label = (parse_citation_labels(context) or [""])[0]
                            if shell_label:
                                result.citations.append(ScanCitation(
                                    anchor_key=f"doc:{doc_name}:cite:{shell_label}",
                                    kind="endnote_shell", label=shell_label,
                                    first_para=context[:120]))
                    el.clear()
                elif tag == f"{W}vertAlign":
                    superscript_pending = (el.get(f"{W}val", "") or "").lower() == "superscript"
                    el.clear()
                elif tag == f"{W}r":
                    superscript_pending = False
                    el.clear()
                elif tag in (f"{W}footnoteReference", f"{W}endnoteReference"):
                    is_foot = tag == f"{W}footnoteReference"
                    ref_id = el.get(f"{W}id", "")
                    if is_foot:
                        result.footnote_count += 1
                        prefix = "fn"
                    else:
                        result.endnote_count += 1
                        prefix = "en"
                    if ref_id:
                        context = "".join(para_buffer).strip()
                        citation = ScanCitation(
                            anchor_key=f"doc:{doc_name}:{prefix}:{ref_id}",
                            kind="footnote" if is_foot else "endnote_ref",
                            label=(parse_citation_labels(context) or [""])[0],
                            first_para=context[:120])
                        result.citations.append(citation)
                        note_anchors.append((citation, ref_id, prefix))
                    el.clear()
                elif tag == f"{W}p":
                    text = "".join(para_buffer).strip()
                    if text:
                        previous_paragraph = text[:120]
                        if len(paragraph_texts) < 40000:
                            paragraph_texts.append(text)      # 供"参考文献列表"解析
                        found = parse_citation_labels(text)
                        if found:
                            result.labels.extend(found)
                    para_buffer.clear()
                    el.clear()
        except ET.ParseError as exc:
            result.warnings.append(f"document.xml 解析中断：{exc}")

    if in_field:                       # 文档被截断在域中间
        flush_field()

    if note_anchors:
        texts = footnote_texts(source)
        endnote_texts = footnote_texts(source, member="word/endnotes.xml")
        for citation, ref_id, prefix in note_anchors:
            source_text = (texts if prefix == "fn" else endnote_texts).get(ref_id, "")
            citation.anchor_key = footnote_anchor(doc_name, source_text, ref_id, prefix)

    if paragraph_texts:
        result.references = parse_bibliography(paragraph_texts, doc_name)

    seen: dict[str, ScanCitation] = {}
    for citation in result.citations:
        seen.setdefault(citation.anchor_key, citation)
    result.citations = list(seen.values())
    result.labels = list(dict.fromkeys(result.labels))
    return result
