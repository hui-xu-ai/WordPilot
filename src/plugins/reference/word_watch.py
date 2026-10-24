# plugins/reference/word_watch.py
"""监听 Word 选区，识别光标所在引用（COM，跑在 QThread 里）。

判定顺序（与 docx_scan 保持一致）：
1. `Selection.Fields(1).Code.Text` 含 `CSL_CITATION` / `EN.CITE` → provider 身份；
2. 同一个 Field 是 `REF _Ref…` → `doc:<文档>:ref:<书签名>`；
3. 光标在脚注/尾注里 → `doc:<文档>:fn|en:<正文哈希>`。

线程只做"识别 + 算屏幕坐标"，解析结果交给主线程解析与渲染；
Word 未运行/COM 报错时安静重试（5 s），不弹框、不阻塞 GUI。
"""
from __future__ import annotations

import hashlib
import logging
import time

from PySide6.QtCore import QThread, Signal

from plugins.reference.docx_scan import footnote_anchor, parse_citation_labels
from plugins.reference.providers.endnote import parse_endnote_cite
from plugins.reference.providers.zotero import parse_csl_citation

logger = logging.getLogger(__name__)

POLL_MS = 400
CLEAR_AFTER_MS = 600
RETRY_AFTER_MS = 5000
_REF_FIELD = "REF "


def _hash_text(text: str) -> str:
    payload = " ".join((text or "").split())
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def resolve_selection(word) -> dict | None:
    """把当前选区解析成 {doc_name, items[], point}；不是引用则返回 None。

    做成纯函数（只吃一个"像 Word 的对象"），便于用假对象单测。
    """
    try:
        selection = word.Selection
        doc = word.ActiveDocument
        doc_name = doc.Name
    except Exception:                      # noqa: BLE001 - Word 关闭/无文档
        return None

    items: list[dict] = []
    try:
        fields = selection.Fields
        if fields is not None and fields.Count >= 1:
            code = (fields(1).Code.Text or "").strip()
            label = parse_citation_labels(selection.Text or "")
            reference = parse_csl_citation(code) or parse_endnote_cite(code)
            if reference is not None:
                items.append({"anchor_key": reference.ref_key, "label": label[0] if label else "",
                              "reference": reference})
            elif "REF " in code.upper():
                bookmark = code.split()[1] if len(code.split()) > 1 else ""
                if bookmark:
                    items.append({"anchor_key": f"doc:{doc_name}:ref:{bookmark}",
                                  "label": label[0] if label else "", "reference": None})
    except Exception:                      # noqa: BLE001
        pass

    if not items:
        try:
            footnotes = selection.Footnotes
            endnotes = selection.Endnotes
            if footnotes is not None and footnotes.Count >= 1:
                text = footnotes(1).Range.Text or ""
                items.append({"anchor_key": footnote_anchor(doc_name, text, _hash_text(text)),
                              "label": "", "reference": None})
            elif endnotes is not None and endnotes.Count >= 1:
                text = endnotes(1).Range.Text or ""
                items.append({"anchor_key": footnote_anchor(doc_name, text, _hash_text(text),
                                                           prefix="en"),
                              "label": "", "reference": None})
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
    """后台轮询 Word 选区，命中引用时发信号。"""

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
