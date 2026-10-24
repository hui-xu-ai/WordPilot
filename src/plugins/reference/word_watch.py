# plugins/reference/word_watch.py
"""监听 Word 选区，识别光标所在**文献引用**（COM，跑在 QThread 里）。

触发规则（用户明确要求，务必保持）：
- ✅ `ADDIN ZOTERO_ITEM CSL_CITATION`、`ADDIN EN.CITE…`（含没有内嵌数据的
  `EN.JS.CITE` / `EN.CITE.DATA` 旧新格式 —— 这类会弹「未绑定」卡片让用户绑文献）；
- ✅ 脚注 / 尾注（身份用正文哈希，复制/重编号都不失效）；
- ❌ `REF _Ref…`（题注、编号项交叉引用）、`SEQ`、`STYLEREF`：这些是图/表/章节号，
  点导航跳到图注时曾经误弹窗，现已排除；
- ❌ 段落样式是「题注 / Caption」的位置，一律不弹。

线程只做"识别 + 算屏幕坐标"，解析结果交给主线程渲染；Word 未运行/COM 报错时安静重试。
"""
from __future__ import annotations

import hashlib
import logging
import re
import time

from PySide6.QtCore import QThread, Signal

from plugins.reference.docx_scan import footnote_anchor, parse_citation_labels
from plugins.reference.providers.endnote import parse_endnote_cite
from plugins.reference.providers.zotero import parse_csl_citation

logger = logging.getLogger(__name__)

POLL_MS = 400
CLEAR_AFTER_MS = 600
RETRY_AFTER_MS = 5000

#: EndNote 无内嵌数据的旧/新格式（`EN.JS.CITE`、`EN.CITE.DATA`）
_ENDNOTE_SHELL = re.compile(r"^\s*ADDIN\s+EN\.(?:JS\.)?CITE(?:\.DATA)?\b", re.IGNORECASE)
#: 图/表/章节号相关域：绝不弹窗
_NUMBER_FIELD = re.compile(r"^\s*(SEQ|STYLEREF|REF)\b", re.IGNORECASE)
_CAPTION_STYLES = ("题注", "caption")


def _hash_text(text: str) -> str:
    payload = " ".join((text or "").split())
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def _field_code(selection) -> str:
    """兼容旧调用：只要域指令文本。"""
    return _field_info(selection)[0]


def _field_info(selection) -> tuple[str, str]:
    """返回 (域指令, 域结果文本)。

    用户"只点一下"引用时选区是折叠光标，`Selection.Fields` 有时为空，但
    `Selection.Range.Fields` 或"前后各扩一个字符"的 Range 仍能找到该域；
    这里按这三条路径依次尝试，避免"点了没反应"。
    """
    candidates = []
    try:
        candidates.append(selection.Fields)
    except Exception:                      # noqa: BLE001
        pass
    try:
        candidates.append(selection.Range.Fields)
    except Exception:                      # noqa: BLE001
        pass
    try:
        start = int(selection.Start)
        end = int(selection.End)
        document = selection.Document
        candidates.append(document.Range(max(0, start - 1), end + 1).Fields)
    except Exception:                      # noqa: BLE001
        pass

    for fields in candidates:
        try:
            if fields is None or fields.Count < 1:
                continue
            field = fields(1)
            code = (field.Code.Text or "").strip()
            try:
                result = (field.Result.Text or "").strip()
            except Exception:              # noqa: BLE001
                result = ""
            return code, result
        except Exception:                  # noqa: BLE001
            continue
    return "", ""


def _is_caption_paragraph(selection) -> bool:
    """题注段落里的任何位置都不弹（用户点导航跳到图注曾被误触发）。"""
    try:
        style = selection.Paragraphs(1).Style.NameLocal or ""
    except Exception:                      # noqa: BLE001
        return False
    lowered = style.lower()
    return any(token in lowered for token in _CAPTION_STYLES)


def _first_label(*texts: str) -> str:
    for text in texts:
        labels = parse_citation_labels(text or "")
        if labels:
            return labels[0]
    return ""


def resolve_selection(word) -> dict | None:
    """把当前选区解析成 {doc_name, items[], point}；不是文献引用则返回 None。

    做成纯函数（只吃"像 Word 的对象"），便于用假对象单测。
    """
    try:
        selection = word.Selection
        doc_name = word.ActiveDocument.Name
    except Exception:                      # noqa: BLE001 - Word 关闭/无文档
        return None

    if _is_caption_paragraph(selection):
        return None

    items: list[dict] = []
    code, result_text = _field_info(selection)
    if code and _NUMBER_FIELD.match(code):
        code, result_text = "", ""         # 图/表/章节号 → 不是文献引用

    selection_text = ""
    try:
        selection_text = getattr(selection, "Text", "") or ""
    except Exception:                      # noqa: BLE001
        selection_text = ""

    if code:
        reference = parse_csl_citation(code) or parse_endnote_cite(code)
        label = _first_label(result_text, selection_text, code)
        if reference is not None:
            items.append({"anchor_key": reference.ref_key, "label": label,
                          "reference": reference, "doc_name": doc_name})
        elif _ENDNOTE_SHELL.match(code):
            # EndNote 只写了域壳（数据在 .Data 文件里或在库里）：按文档编号建锚点，
            # 弹「未绑定」卡片让用户绑到库里的文献（比"点了没反应"好）。
            identity = label or _hash_text(result_text or code)
            items.append({
                "anchor_key": f"doc:{doc_name}:cite:{identity}",
                "label": label, "reference": None, "doc_name": doc_name,
            })
            logger.debug("识别到 EndNote 空壳域：label=%r 结果文本=%r", label, result_text[:60])
        else:
            logger.debug("未识别的域指令（不弹窗）：%r", code[:80])

    if not items:
        try:
            footnotes = selection.Footnotes
            endnotes = selection.Endnotes
            if footnotes is not None and footnotes.Count >= 1:
                text = footnotes(1).Range.Text or ""
                items.append({"anchor_key": footnote_anchor(doc_name, text, _hash_text(text)),
                              "label": "", "reference": None, "doc_name": doc_name})
            elif endnotes is not None and endnotes.Count >= 1:
                text = endnotes(1).Range.Text or ""
                items.append({"anchor_key": footnote_anchor(doc_name, text, _hash_text(text),
                                                            prefix="en"),
                              "label": "", "reference": None, "doc_name": doc_name})
        except Exception:                  # noqa: BLE001
            pass

    if not items:
        return None
    return {"doc_name": doc_name, "items": items, "point": screen_point(word, selection)}


def screen_point(word, selection) -> tuple[int, int]:
    """引用位置在屏幕上的坐标（点）。失败时退回 Word 窗口左上角附近。"""
    window = getattr(word, "ActiveWindow", None)
    offset_x = offset_y = 0
    try:
        import win32gui

        hwnd = win32gui.FindWindow("OpusApp", None)
        if hwnd:
            left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
            offset_x, offset_y = left, top
    except Exception:                      # noqa: BLE001 - 没有 pywin32/窗口时忽略
        pass

    try:
        page_x = int(selection.Information(5))       # wdHorizontalPositionRelativeToPage
        page_y = int(selection.Information(6))       # wdVerticalPositionRelativeToPage
        pixel_x = int(window.PointsToScreenPixelsX(page_x))
        pixel_y = int(window.PointsToScreenPixelsY(page_y))
        return offset_x + pixel_x, offset_y + pixel_y
    except Exception:                      # noqa: BLE001
        return offset_x + 200, offset_y + 200


class WordWatch(QThread):
    """后台轮询 Word 选区，命中文献引用时发信号。"""

    citationFound = Signal(object)         # dict，见 resolve_selection
    citationCleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._running = False
        self._last_signature: tuple[str, int, int] | None = None
        self._last_hit_at = 0.0
        self._cleared = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:                 # noqa: D401 - QThread 入口
        self._running = True
        try:
            import pythoncom
        except ImportError:                # 没有 pywin32：直接退出，不影响其它功能
            logger.warning("未安装 pywin32，参考文献实时监听不可用")
            return

        pythoncom.CoInitialize()
        try:
            from core.services.word_bridge import get_word_app

            while self._running:
                started = time.perf_counter()
                try:
                    word = get_word_app()
                    payload = resolve_selection(word)
                except Exception:          # noqa: BLE001 - Word 未开/关闭中
                    word, payload = None, None

                if payload is None:
                    if not self._cleared and (
                            (time.perf_counter() - self._last_hit_at) * 1000 >= CLEAR_AFTER_MS):
                        self._cleared = True
                        self._last_signature = None
                        self.citationCleared.emit()
                    self.msleep(RETRY_AFTER_MS if word is None else POLL_MS)
                    continue

                try:
                    selection = word.Selection
                    signature = (payload["doc_name"], int(selection.Start), int(selection.End))
                except Exception:          # noqa: BLE001
                    signature = None

                if signature is not None and signature != self._last_signature:
                    self._last_signature = signature
                    self._cleared = False
                    self._last_hit_at = time.perf_counter()
                    self.citationFound.emit(payload)

                elapsed_ms = (time.perf_counter() - started) * 1000
                self.msleep(max(80, int(POLL_MS - elapsed_ms)))
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:              # noqa: BLE001
                pass
