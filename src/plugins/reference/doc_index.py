# plugins/reference/doc_index.py
"""**扫描 → 缓存 → 点击查表**（对齐题录导航助手的思路）。

用户明确要求：参考文献和题注本质相同 —— 都是"在线/离线扫描一次，写进本地库，
之后点击只是查库"。所以这里把"域 → 锚点"的解析放进 `DocIndex`：

- `rescan(word)`：遍历 `doc.Fields` 一次，把每个文献类域的 `Result.Start/End`
  与解析出的锚点记进内存（2000 个域约 1~3 s，只在首次/换文档/手动刷新时做）；
- `lookup(pos)`：二分查当前位置落在哪个域里 —— **毫秒级**，每次点击只读一个 `Selection.Start`；
- 图/表/章节号（`SEQ`/`STYLEREF`/`REF`）与题注样式段落一律忽略。
"""
from __future__ import annotations

import bisect
import hashlib
import logging
import re
from dataclasses import dataclass, field

from plugins.reference.docx_scan import footnote_anchor, parse_citation_labels
from plugins.reference.providers.endnote import parse_endnote_cite
from plugins.reference.providers.zotero import parse_csl_citation

logger = logging.getLogger(__name__)

_ENDNOTE_SHELL = re.compile(r"^\s*ADDIN\s+EN\.(?:JS\.)?CITE(?:\.DATA)?\b", re.IGNORECASE)
_NUMBER_FIELD = re.compile(r"^\s*(SEQ|STYLEREF|REF)\b", re.IGNORECASE)


@dataclass
class DocAnchor:
    """文档中的一个引用锚点（含字符区间，供位置查表）。"""

    start: int
    end: int
    anchor_key: str
    label: str = ""
    kind: str = ""


def classify_field(code: str, result_text: str, doc_name: str) -> list[tuple[str, str, str]]:
    """把一个域解析成 [(anchor_key, label, kind)]；不是文献引用则返回空列表。"""
    code = (code or "").strip()
    if not code or _NUMBER_FIELD.match(code):
        return []

    reference = parse_csl_citation(code) or parse_endnote_cite(code)
    labels = parse_citation_labels(result_text or "") or parse_citation_labels(code)
    label = labels[0] if labels else ""
    if reference is not None:
        return [(reference.ref_key, label, reference.provider or "provider")]

    if _ENDNOTE_SHELL.match(code):
        # EndNote 只写了域壳：按文档编号逐条展开（`[5,6]` → 两条）
        if labels:
            return [(f"doc:{doc_name}:cite:{one}", one, "endnote_shell") for one in labels]
        # 连编号都没有：用域结果文本哈希兜底，先能弹出来让用户绑
        payload = " ".join((result_text or code).split())
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
        return [(f"doc:{doc_name}:cite:{digest}", "", "endnote_shell")]
    return []


def document_signature(document) -> tuple[int, int]:
    """文档内容指纹：域个数 + 正文末尾位置。插入/删除引用或正文都会变化。"""
    try:
        fields = int(document.Fields.Count)
    except Exception:                          # noqa: BLE001
        fields = 0
    try:
        end = int(document.Content.End)
    except Exception:                          # noqa: BLE001
        end = 0
    return (fields, end)


@dataclass
class DocIndex:
    """某一篇文档的引用锚点缓存。"""

    doc_name: str = ""
    anchors: list[DocAnchor] = field(default_factory=list)
    _starts: list[int] = field(default_factory=list)
    #: 是否已经为当前文档扫过一次（空文档也算扫过，避免无限重扫）
    scanned: bool = False
    doc_identity: str = ""          # 全路径身份（判重扫用）；doc_name 只用于显示
    #: 文档内容指纹（域个数 + 正文长度）：**同一文档编辑后**也要重扫（R12）
    signature: tuple[int, int] = (0, 0)

    def needs_rescan(self, doc_identity: str, signature: tuple[int, int] | None = None) -> bool:
        """按**全路径身份**判断（S10）：同名不同目录的文档不能共用锚点。

        R12：还要比较内容指纹 —— 同一篇文档里插入/删除引用后，旧的锚点位置会整体偏移。
        """
        if doc_identity != self.doc_identity or not self.scanned:
            return True
        if signature is not None and self.signature and signature != self.signature:
            return True
        return False

    def clear(self) -> None:
        self.doc_name = ""
        self.doc_identity = ""
        self.signature = (0, 0)
        self.anchors = []
        self._starts = []
        self.scanned = False

    def rescan(self, word) -> int:
        """遍历当前文档的所有域，重建锚点表；返回锚点数量。"""
        try:
            document = word.ActiveDocument
            doc_name = document.Name
            # S10：搞一份**全路径身份**用于判断是否重扫（同名不同目录的文档不能混）
            doc_identity = str(getattr(document, "FullName", "") or doc_name)
        except Exception as exc:                 # noqa: BLE001 - Word 关闭/无文档
            logger.debug("扫描域失败：%s", exc)
            return 0

        # 记录文档名/身份，但**先不标记"已扫描"**：
        # 刚切换文档时 Word 常短暂忙碌，读域会抛异常；若此时就标记 scanned，
        # 这份文档会被判定"扫过了但 0 个锚点"，之后再也不重扫 → 点引用永远不弹窗
        # （用户现场：切换文档后必须点「同步本文」才恢复）。
        self.doc_name = doc_name
        self.doc_identity = doc_identity
        self.anchors = []
        self._starts = []

        try:
            fields = document.Fields
            count = int(fields.Count)
        except Exception as exc:                 # noqa: BLE001
            logger.debug("读取域集合失败（下次继续重试）：%s", exc)
            self.signature = (0, 0)              # 指纹归零 → 下一轮 needs_rescan 为真
            self.scanned = False
            return 0

        self.signature = document_signature(document)
        self.scanned = True

        anchors: list[DocAnchor] = []
        for index in range(1, count + 1):
            try:
                field = fields(index)
                code = (field.Code.Text or "").strip()
                if not code or _NUMBER_FIELD.match(code):
                    continue
                try:
                    result_range = field.Result
                    result_text = (result_range.Text or "").strip()
                    start, end = int(result_range.Start), int(result_range.End)
                except Exception:                # noqa: BLE001
                    result_text, start, end = "", int(field.Code.Start), int(field.Code.End)
                for anchor_key, label, kind in classify_field(code, result_text, doc_name):
                    anchors.append(DocAnchor(start=start, end=max(end, start + 1),
                                             anchor_key=anchor_key, label=label, kind=kind))
            except Exception:                    # noqa: BLE001 - 单个域失败不影响整体
                continue

        anchors.sort(key=lambda item: (item.start, item.end))
        self.anchors = anchors
        self._starts = [item.start for item in anchors]
        self.scanned = True
        logger.info("参考文献锚点索引已重建：%d 个（%s，域总数 %d）", len(anchors), doc_name, count)
        return len(anchors)

    def load(self, doc_name: str, anchors: list[DocAnchor]) -> int:
        """直接装载（来自扫描缓存）：免去一次 COM 遍历。"""
        anchors = sorted(anchors, key=lambda item: (item.start, item.end))
        self.doc_name = doc_name
        self.doc_identity = doc_name          # S10：装载也记身份
        self.anchors = anchors
        self._starts = [item.start for item in anchors]
        self.scanned = True
        return len(anchors)

    def lookup_all(self, position: int) -> list[DocAnchor]:
        """查位置命中的**所有**锚点（`[5,6]` 这种一格多篇会返回两条）。

        二分定位后把区间完全相同的锚点一起取出来 —— 同一域的多个编号没有独立位置，
        只有全部返回才能"连用的文献全部读取"。
        """
        if not self._starts:
            return []
        index = bisect.bisect_right(self._starts, position) - 1
        for candidate in (index, index - 1):
            if 0 <= candidate < len(self.anchors):
                anchor = self.anchors[candidate]
                if anchor.start <= position <= anchor.end:
                    return [item for item in self.anchors
                            if (item.start, item.end) == (anchor.start, anchor.end)]
        return []

    def lookup(self, position: int) -> DocAnchor | None:
        """查字符位置命中的第一个锚点（兼容接口）。"""
        hits = self.lookup_all(position)
        return hits[0] if hits else None
