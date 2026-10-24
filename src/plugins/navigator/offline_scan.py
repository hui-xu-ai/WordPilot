# plugins/navigator/offline_scan.py
"""离线题录扫描：直接解析 .docx 的 OOXML，不经 COM、不占用 GUI 线程。

动机：刷新题录走 Word COM（`doc.Fields` / `doc.Paragraphs` 往返）会把 GUI 线程卡住约 2s。
.docx 本质是 zip + XML，题录与章节标题都在 `word/document.xml` 里，纯解析快一个数量级。

识别依据（实测样本 `local/fixtures/*.docx`，同一样本上比正则更准）：
- **章节标题**：段落样式名形如 `heading N` / `标题 N`（取 N 为层级）；"第几章"取**最小层级**，
  因为很多论文模板不用 Heading 1（实测 EndNote 样本从 heading 2 起）；
- **题注**：优先看段落样式名含"图表标题 / caption / 题注"；
  没有样式时退回文案正则（图/表 + 编号）与 `SEQ` 域指令。

限制（明确不做）：
- 拿不到**页码**（排版结果，OOXML 里没有）⇒ 沿用缓存旧值或留空；
- 拿不到 COM 引用 ⇒ 跳转改为按文本 `Find`（见 `navigator_jump`）。
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import Iterator
from xml.etree import ElementTree as ET

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{WORD_NS}}}"

#: 标题样式名（Word 中英文模板）：`heading 1` / `标题 1`
HEADING_RE = re.compile(r"(?:heading|标题)\s*([1-9])", re.IGNORECASE)

#: 题注样式名关键词（实测样本用 `图表标题` 样式）
CAPTION_STYLE_HINTS = ("图表标题", "题注", "caption")

#: 题注文案：图/表/Figure/Table + 编号（允许 1.9 / 1-9 / 1．9 等写法）
CAPTION_RE = re.compile(r"^\s*(?P<kind>图|表|Figure|Table)\s*[0-9０-９]+"
                        r"(?:[.\-－．_][0-9０-９]+)*", re.IGNORECASE)

_KIND_TABLE = {"表", "table"}


@dataclass
class OfflineItem:
    """离线扫描出的一条题录（无 COM 引用，也无页码）。"""

    text: str
    kind: str = "table"          # table / figure
    chapter: int = -1            # 所属章节下标（-1 = 第一个章节标题之前）


@dataclass
class OfflineResult:
    chapters: list[str] = field(default_factory=list)
    items: list[OfflineItem] = field(default_factory=list)
    paragraphs: int = 0
    chapter_level: int = 0       # 判定"章"所用的标题层级（0 = 没识别到标题）

    def table_items(self) -> list[OfflineItem]:
        return [i for i in self.items if i.kind == "table"]

    def figure_items(self) -> list[OfflineItem]:
        return [i for i in self.items if i.kind == "figure"]


def _style_names(archive: zipfile.ZipFile) -> dict[str, str]:
    """styleId → 样式名（小写），用于把 `pStyle val="ae"` 翻译成"图表标题"。"""
    try:
        raw = archive.read("word/styles.xml")
    except KeyError:
        return {}
    names: dict[str, str] = {}
    for style in ET.fromstring(raw).iter(f"{W}style"):
        sid = style.get(f"{W}styleId")
        name_node = style.find(f"{W}name")
        if sid and name_node is not None:
            names[sid] = (name_node.get(f"{W}val") or "").strip().lower()
    return names


def _p_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{W}t")).strip()


def _p_style(paragraph: ET.Element) -> str:
    node = paragraph.find(f"{W}pPr/{W}pStyle")
    return (node.get(f"{W}val") or "").strip() if node is not None else ""


def _heading_level(style_name: str) -> int:
    match = HEADING_RE.search(style_name or "")
    return int(match.group(1)) if match else 0


#: 章标题占位符（论文模板常见）：识别出来也显示"第N章"，否则下拉里会是三行一样的"章标题"
PLACEHOLDER_TITLES = {"章标题", "标题", "chapter title", "章", "chapter", ""}

#: 后置章节（不编"第N章"）
BACK_MATTER = ("参考文献", "致谢", "附录", "摘要", "abstract", "references",
               "acknowledg", "appendix", "结论与展望")

_CHAPTER_NUM_RE = re.compile(r"第\s*[0-9一二三四五六七八九十百零]+\s*章")
_LEADING_NUM_RE = re.compile(r"^\s*[0-9]+[.\s、]")


def chapter_label(index: int, text: str) -> str:
    """章节下拉里显示的名字：已有编号就原样用，否则补"第N章"。"""
    text = (text or "").strip()
    if _CHAPTER_NUM_RE.search(text) or _LEADING_NUM_RE.match(text):
        return text
    if any(word in text.lower() for word in BACK_MATTER):
        return text
    if text in PLACEHOLDER_TITLES or len(text) <= 2:
        return f"第{index + 1}章"
    return f"第{index + 1}章 {text}"


def _kind_of(text: str, style_name: str, paragraph: ET.Element) -> str | None:
    """判断段落是不是题注；返回 table / figure / None。

    以**文案编号**为准（`图 1.9` / `表 1.1` / `Figure 1.9`）：实测样本里"图表标题"样式
    也会落在图注的续行（如 `1.1)`）与多行说明上，单看样式会多收 15 条假条目录。
    样式只作为 `SEQ` 域的辅助线索（题注被人工改写过、文案里没有编号时）。
    """
    match = CAPTION_RE.match(text)
    if match:
        return "table" if match.group("kind").lower() in _KIND_TABLE else "figure"
    # 文案里没有编号（编号由 SEQ 域渲染，例如多行题注）时看域指令；
    # 但只认 图/表/Figure/Table 系列，避免把 `SEQ 公式` 也当成图题注。
    for instr in paragraph.iter(f"{W}instrText"):
        value = (instr.text or "").upper()
        if "SEQ" not in value:
            continue
        if "表" in value or "TABLE" in value:
            return "table"
        if "图" in value or "FIGURE" in value:
            return "figure"
    return None


def iter_document_paragraphs(docx_path: str | bytes) -> Iterator[ET.Element]:
    """流式读出 `word/document.xml` 里的段落元素（大文件也不吃满内存）。"""
    source = BytesIO(docx_path) if isinstance(docx_path, bytes) else docx_path
    with zipfile.ZipFile(source) as archive:
        with archive.open("word/document.xml") as handle:
            for _event, elem in ET.iterparse(handle, events=("end",)):
                if elem.tag == f"{W}p":
                    yield elem
                    elem.clear()


def scan_docx(docx_path: str) -> OfflineResult:
    """解析 docx，返回章节标题与题录清单（按文档顺序）。

    两步：先读 styles.xml 建 id→样式名映射，再流式扫段落。
    章节以**识别到的最小标题层级**为准（模板不一定用 Heading 1）。
    """
    titles: list[tuple[int, str]] = []          # (层级, 文案) 全量标题
    items: list[tuple[int, str, str]] = []      # (标题下标, kind, 文案)
    heading_positions: list[int] = []
    paragraphs = 0

    with zipfile.ZipFile(docx_path) as archive:
        names = _style_names(archive)
        with archive.open("word/document.xml") as handle:
            for _event, elem in ET.iterparse(handle, events=("end",)):
                if elem.tag != f"{W}p":
                    continue
                paragraphs += 1
                text = _p_text(elem)
                style = names.get(_p_style(elem), "")
                level = _heading_level(style)
                if level and text:
                    titles.append((level, text))
                    heading_positions.append(len(titles) - 1)
                elif text:
                    kind = _kind_of(text, style, elem)
                    if kind is not None:
                        items.append((len(titles) - 1, kind, text))
                elem.clear()

    result = OfflineResult(paragraphs=paragraphs)
    if not titles:
        result.items = [OfflineItem(text=text, kind=kind) for _ch, kind, text in items]
        return result

    chapter_level = min(level for level, _ in titles)
    # 只把"章"级别的标题收进章节列表，并重建 标题序号 → 章节序号 的映射
    mapping: dict[int, int] = {}
    for index, (level, text) in enumerate(titles):
        if level == chapter_level:
            mapping[index] = len(result.chapters)
            result.chapters.append(chapter_label(len(result.chapters), text))
    result.chapter_level = chapter_level

    def chapter_index(heading_index: int) -> int:
        current = -1
        for index, chapter in mapping.items():
            if index <= heading_index:
                current = chapter
            else:
                break
        return current

    result.items = [OfflineItem(text=text, kind=kind, chapter=chapter_index(pos))
                    for pos, kind, text in items]
    return result


def chapter_of(result: OfflineResult, index: int) -> str:
    """章节下标 → 标题（越界返回空串）。"""
    if 0 <= index < len(result.chapters):
        return result.chapters[index]
    return ""


def chapter_key(text: str) -> str:
    """题录文本归一化，用于把离线章节信息对到 COM 扫出来的条目上。"""
    return (text or "").split("  [", 1)[0].strip()
