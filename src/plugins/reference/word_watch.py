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

from plugins.reference.doc_index import DocIndex, classify_field
from plugins.reference.docx_scan import footnote_anchor, parse_citation_labels
from plugins.reference.providers.endnote import parse_endnote_cite
from plugins.reference.providers.zotero import parse_csl_citation

logger = logging.getLogger(__name__)

POLL_MS = 300
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


def labels_at_position(word, position: int, window: int = 40, document=None) -> list[str]:
    """按"参考文献角标规则"从**正文文字**里取出光标涉及的编号。

    用户建议（采纳）：不要只依赖域代码，直接看光标附近的 `[5,6]` / `[2-4]` 文字，
    按括号规则拆开 —— 这样 `[5,6]` 必然得到 5 和 6 两条。

    `document` 必须是**前台窗口那份**：`word.ActiveDocument` 在两篇文档同时打开时会停在
    旧的那篇，读出来的文字是别人的 → 永远提取不到编号 → 点了不弹窗。
    """
    try:
        document = document if document is not None else word.ActiveDocument
        start = max(0, position - window)
        text = document.Range(start, position + window).Text or ""
    except Exception:                      # noqa: BLE001
        return []
    offset = position - start
    best: tuple[int, int, str] | None = None
    for match in re.finditer(r"[\[（(][^\]）)]{1,40}[\]）)]", text):
        group = match.group(0)
        if not re.search(r"\d", group):
            continue
        if match.start() <= offset <= match.end():
            best = (match.start(), match.end(), group)
            break
        if best is None:
            distance = min(abs(offset - match.start()), abs(offset - match.end()))
            if distance <= 2:
                best = (match.start(), match.end(), group)
    if best is None:
        return []
    return parse_citation_labels(best[2])


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
    if code and result_text == "":
        try:
            result_text = (selection.Text or "").strip()
        except Exception:                  # noqa: BLE001
            result_text = ""

    if code:
        for anchor_key, label, kind in classify_field(code, result_text, doc_name):
            items.append({"anchor_key": anchor_key, "label": label,
                          "reference": None, "doc_name": doc_name, "kind": kind})
        if not items:
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
    doc_path = ""
    try:
        doc_path = word.ActiveDocument.FullName or ""
    except Exception:                      # noqa: BLE001
        doc_path = ""
    return {"doc_name": doc_name, "doc_path": doc_path, "items": items,
            "point": screen_point(word, selection)}


def _device_ratio() -> float:
    """Qt 逻辑像素 ↔ Win32 物理像素的比例（高分屏缩放）。"""
    try:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen is not None else 1.0
    except Exception:                      # noqa: BLE001
        return 1.0


def _client_origin() -> tuple[int, int] | None:
    """Word 窗口**客户区**在屏幕上的物理像素原点（不含标题栏与边框）。"""
    try:
        import win32gui

        hwnd = win32gui.FindWindow("OpusApp", None)
        if not hwnd:
            return None
        left, top, _right, _bottom = win32gui.GetClientRect(hwnd)
        return win32gui.ClientToScreen(hwnd, (left, top))
    except Exception:                      # noqa: BLE001
        return None


def _word_point(word, selection) -> tuple[int, int] | None:
    """用 Word 自己的 API 把选区换算成屏幕物理像素；失败返回 None。"""
    try:
        window = word.ActiveWindow
        page_x = int(selection.Information(5))     # 相对页面左边缘（pt）
        page_y = int(selection.Information(6))     # 相对页面顶端（pt）
        if page_x <= 0 and page_y <= 0:
            return None
        pixel_x = int(window.PointsToScreenPixelsX(page_x))
        pixel_y = int(window.PointsToScreenPixelsY(page_y))
    except Exception:                      # noqa: BLE001
        return None

    origin = _client_origin()
    if origin is not None:
        return origin[0] + pixel_x, origin[1] + pixel_y
    return pixel_x, pixel_y


def _cursor_point() -> tuple[int, int] | None:
    """鼠标位置兜底：用户刚点击引用，光标就在引用附近。"""
    try:
        import win32api

        return win32api.GetCursorPos()
    except Exception:                      # noqa: BLE001
        return None


def screen_point(word, selection) -> tuple[int, int]:
    """引用位置在屏幕上的坐标（Qt 逻辑像素）。"""
    physical = _word_point(word, selection) or _cursor_point()
    if physical is None:
        origin = _client_origin()
        physical = ((origin[0] + 120, origin[1] + 80) if origin else (200, 200))

    ratio = _device_ratio() or 1.0
    return int(physical[0] / ratio), int(physical[1] / ratio)


def _active_window(word):
    """前台 Word 窗口 + 匹配方式（失败返回 (None, "无")）。延迟导入，避免无 pywin32 时崩。"""
    try:
        from core.services.word_bridge import active_window_with_source

        return active_window_with_source(word=word)
    except Exception:                      # noqa: BLE001
        return None, "无"


class WordWatch(QThread):
    """后台轮询 Word 选区，命中文献引用时发信号。"""

    citationFound = Signal(object)         # dict，见 resolve_selection
    citationCleared = Signal()
    statusChanged = Signal(str)
    documentChanged = Signal(str)        # 当前文档名（方案：界面显示"当前文件"）            # 连接状态/扫描进度（供页面状态栏显示）

    def __init__(self, parent=None, cache=None) -> None:
        super().__init__(parent)
        self._cache = cache
        self._running = False
        self._last_signature: tuple[str, int, int] | None = None
        self._last_hit_at = 0.0
        self._cleared = True
        self._index = DocIndex()
        self._last_anchor = ""
        self._last_status = ""
        self._document = None
        self._selection = None
        self._healed: set[tuple] = set()              # BUG-2 自愈：同一篇文档只重扫一次

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

            self._emit_status("watch.starting")

            while self._running:
                started = time.perf_counter()
                try:
                    self._tick(get_word_app)
                except Exception:          # noqa: BLE001
                    # 关键：单次异常绝不能让监听线程退出（曾经因此整晚都"点了没弹窗"）
                    logger.exception("参考文献监听单次循环失败")
                    self._emit_status("watch.error")
                    self._handle_miss()

                elapsed_ms = (time.perf_counter() - started) * 1000
                self.msleep(max(80, int(POLL_MS - elapsed_ms)))
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:              # noqa: BLE001
                pass

    # ---------------- 单次循环 ----------------
    @staticmethod
    def _doc_identity(word, document=None) -> str:
        """S10：文档身份用**全路径**。同名不同目录的 same.docx 不能共用锚点。"""
        try:
            document = document if document is not None else word.ActiveDocument
            return str(getattr(document, "FullName", "") or "")
        except Exception:                  # noqa: BLE001 - Word 正在关闭/未打开
            return ""

    def _tick(self, get_word_app) -> None:
        try:
            word = get_word_app()
            # 同一个 Word 进程开着两篇文档时，`ActiveDocument` / `Selection` 会停在**旧窗口**
            # （用户现场：切到另一篇后仓库不跟着切、点了也不弹）。按**前台窗口句柄**找到窗口，
            # 再从窗口取 Document；若 Word 自认为的活动文档不一致，就 Activate 一次让它跟上
            # —— 此时该窗口本来就是前台窗口，不会抢焦点，但 Selection/域位置才读得准。
            document = word.ActiveDocument
            try:
                window, source = _active_window(word)
                if window is not None and getattr(window, "Document", None) is not None:
                    document = window.Document
                    if str(getattr(word.ActiveDocument, "Name", "")) != str(document.Name):
                        document.Activate()
                    # 只在文档或匹配方式变化时记一行：切文档后到底认的是哪篇、凭哪条线索认的
                    key = (str(document.Name), source)
                    if key != getattr(self, "_last_window_log", None):
                        self._last_window_log = key
                        logger.info("当前文档窗口：%s（匹配方式=%s）", document.Name, source)
            except Exception:                  # noqa: BLE001 - 窗口匹配失败就用 ActiveDocument
                logger.debug("按前台窗口定位文档失败，退回 ActiveDocument", exc_info=True)
            doc_name = document.Name
            self._document = document            # 本轮统一的文档对象
            try:
                self._selection = word.Selection  # 后续 payload/坐标都用它
            except Exception:                  # noqa: BLE001
                self._selection = None
        except Exception:                  # noqa: BLE001 - Word 未开/关闭中
            self._emit_status("watch.no_word")
            self._handle_miss()
            self.msleep(min(RETRY_AFTER_MS, 1000))
            return

        doc_identity = self._doc_identity(word, document) or doc_name
        self._last_doc_identity = doc_identity        # 供 GUI 侧按**全路径**切绑定库
        if doc_name != getattr(self, "_last_doc_name", None):
            self._last_doc_name = doc_name
            self.documentChanged.emit(doc_name)

        signature = None
        try:
            from plugins.reference.doc_index import document_signature

            signature = document_signature(document)
        except Exception:                      # noqa: BLE001 - 取不到指纹就退回按路径判断
            signature = None
        needs = self._index.needs_rescan(doc_identity, signature)
        if needs:
            doc_path, fp = self._document_fingerprint(word, document)
            cached = None
            if self._cache is not None:
                cached = self._cache.load_anchors(doc_path, fp)
            if cached is not None:
                count = self._index.load(doc_identity, cached)
                # **必须**把当前内容指纹记进索引：`DocIndex.load()` 不写 signature，
                # 它会一直保持 `clear()` 后的 (0, 0)，而 (0,0) ≠ 实时指纹 ⇒ 每一轮
                # `needs_rescan` 都为真 ⇒ 每轮都在这里重扫并 return，**永远走不到读光标、
                # 提取编号那一步**（用户现场：点了引文 ②③④ 全不出现，日志每轮都是 rescan）。
                if signature is not None:
                    self._index.signature = signature
                self._emit_status(f"watch.cached:{count}")
            else:
                self._emit_status("watch.scanning")
                count = self._index.rescan(word, document=document)
                if self._cache is not None:
                    self._cache.save_anchors(doc_path, fp, doc_name, self._index.anchors)
                self._emit_status(f"watch.scanned:{count}")
            # 这里**不能**调 `_handle_miss()`：重扫只说明"索引还没建好"，不是"光标离开引用"。
            # 之前它会把卡片当成 miss，超过 CLEAR_AFTER_MS(600ms) 就发 citationCleared，
            # 用户现象正是"弹窗闪一下就自己关"（切文档时连着几轮都在重扫）。
            return

        try:
            # 用**前台窗口的选区**：`word.Selection` 是 Word 的应用级状态，可能停在旧窗口
            selection = self._selection if self._selection is not None else word.Selection
            position = int(selection.Start)
            start, end = int(selection.Start), int(selection.End)
        except Exception:                  # noqa: BLE001
            self._handle_miss()
            return

        # 用户选中了整句/整段时不要弹窗（否则一句话里多个引用会一起触发）
        if end - start > 1:
            self._handle_miss()
            return

        anchors = (self._index.lookup_all(position)
                   or self._index.lookup_all(position - 1))
        try:
            text_labels = labels_at_position(word, position, document=self._document)
        except Exception:                  # noqa: BLE001 - 文字解析失败不影响域锚点
            logger.debug("角标文字解析失败（忽略）", exc_info=True)
            text_labels = []
        if not anchors and (text_labels or not self._index.anchors):
            # BUG-2 自愈：① 索引里有锚点却一个都不命中光标，而这里确实有引用文字；
            # ② 索引干脆是空的（实测 21:47:52：索引锚点=0 已扫=True 需重扫=False →
            #    监听线程认定"这篇已扫过"，于是一直用空索引，只能点「同步本文」才恢复）。
            if self._heal_index(word, doc_identity, signature, document=self._document):
                anchors = (self._index.lookup_all(position)
                           or self._index.lookup_all(position - 1))
        state = (bool(anchors), tuple(text_labels))
        if state != getattr(self, "_last_cursor_state", None):
            self._last_cursor_state = state
            logger.info("光标检查：pos=%d 域锚点=%d 文字编号=%s 索引锚点=%d 文档=%s",
                        position, len(anchors), text_labels, len(self._index.anchors), doc_name)
        if anchors or text_labels:
            self._last_anchor = anchors[0].anchor_key if anchors else ""
            self._cleared = False
            self._last_hit_at = time.perf_counter()
            self.citationFound.emit(self._anchor_payload(
                word, anchors, doc_name, position,
                document=self._document, selection=self._selection))
            return

        payload = (self._footnote_payload(word, doc_name, selection=self._selection)
                   or self._label_fallback_payload(word, doc_name,
                                                   selection=self._selection))
        if payload is not None:
            self._cleared = False
            self._last_hit_at = time.perf_counter()
            self.citationFound.emit(payload)
        else:
            self._handle_miss()

    def _heal_index(self, word, doc_identity: str, signature, document=None) -> bool:
        """索引与光标对不上（或索引是空的）时重扫一次；同一篇文档只做一次，避免反复扫 COM。

        为什么需要：`needs_rescan` 只看"文档身份 + 内容指纹"，一旦索引被清空或被缓存填成
        空表，它会认为"这篇已经扫过"，监听线程就再也不重扫 —— 用户只能点「同步本文」。
        返回 True 表示确实重扫了，调用方应重试查表。
        """
        key = (doc_identity, signature)
        if key in self._healed:
            return False
        self._healed.add(key)
        try:
            self._index.clear()
            count = self._index.rescan(word, document=document)
            # 自愈结果要写回缓存：否则下次启动又装着那份"0 个锚点"的旧缓存，
            # 每篇文档第一次点击都得靠自愈补救（用户现场：切文档后第一下不弹）。
            if self._cache is not None and count:
                doc_path, fp = self._document_fingerprint(word, document)
                if doc_path:
                    self._cache.save_anchors(doc_path, fp, self._index.doc_name,
                                             self._index.anchors)
        except Exception:                  # noqa: BLE001 - 自愈失败不能打死监听线程
            logger.exception("索引自愈重扫失败")
            return False
        logger.info("BUG-2 自愈：索引与光标不符，已重扫（%s，%d 个锚点）",
                    self._index.doc_name, count)
        return True

    @staticmethod
    def _document_fingerprint(word, document=None) -> tuple[str, tuple[int, int] | None]:
        """取文档路径与文件指纹；失败返回 ("", None)（此时不做缓存）。

        `document` 由调用方传入**前台窗口那份文档**——不能再用 `word.ActiveDocument`：
        两个文档同时打开时它会停在旧窗口，缓存就会按错误的路径存取。
        """
        try:
            from plugins.reference.scan_cache import fingerprint

            document = document if document is not None else word.ActiveDocument
            path = document.FullName or ""
            return path, fingerprint(path)
        except Exception:                  # noqa: BLE001
            return "", None

    def rescan_index(self, word) -> int:
        """手动扫描后重建点击查表索引（供页面「扫描本文」调用）。"""
        self._index.clear()
        return self._index.rescan(word)

    def _emit_status(self, text: str) -> None:
        if text != self._last_status:
            self._last_status = text
            self.statusChanged.emit(text)

    def _handle_miss(self) -> None:
        if not self._cleared and (
                (time.perf_counter() - self._last_hit_at) * 1000 >= CLEAR_AFTER_MS):
            self._cleared = True
            self.citationCleared.emit()

    def _anchor_payload(self, word, anchors, doc_name: str, position: int = -1,
                        document=None, selection=None) -> dict:
        doc_path = ""
        try:
            document = document if document is not None else word.ActiveDocument
            doc_path = document.FullName or ""
        except Exception:                  # noqa: BLE001
            doc_path = ""
        try:
            point = screen_point(word, selection if selection is not None else word.Selection)
        except Exception:                  # noqa: BLE001
            point = (200, 200)

        items = [{"anchor_key": anchor.anchor_key, "label": anchor.label,
                  "reference": None, "doc_name": doc_name, "kind": anchor.kind}
                 for anchor in anchors]

        # 角标文字补充：域只给出 5、正文写着 `[5,6]` 时，把 6 也补上（用户明确要求按角标规则解析）
        if position >= 0:
            known = {item["anchor_key"] for item in items}
            try:
                extra_labels = labels_at_position(word, position)
            except Exception:              # noqa: BLE001 - 文字解析失败不影响域锚点
                logger.debug("角标文字解析失败（忽略）", exc_info=True)
                extra_labels = []
            for label in extra_labels:
                key = f"doc:{doc_name}:cite:{label}"
                if key not in known:
                    known.add(key)
                    items.append({"anchor_key": key, "label": label, "reference": None,
                                  "doc_name": doc_name, "kind": "label_text"})

        items.sort(key=lambda item: (not item["label"].isdigit(),
                                     int(item["label"]) if item["label"].isdigit() else 0))
        return {"doc_name": doc_name, "doc_path": doc_path, "point": point, "items": items}

    def _label_fallback_payload(self, word, doc_name: str, selection=None) -> dict | None:
        """域索引没命中时，看光标旁边是不是 `[12]` 这类编号（EndNote 常把引用显示成文本）。

        只有插件能在库里解析到该编号时才真的弹窗（见 `plugin._entry_for_item`）。
        """
        try:
            selection = selection if selection is not None else word.Selection
            text = (selection.Text or "").strip()
            if not text:
                # 折叠光标：取前后各 3 个字符看看
                start = int(selection.Start)
                text = (selection.Document.Range(max(0, start - 3), start + 3).Text or "").strip()
        except Exception:                  # noqa: BLE001
            return None
        labels = parse_citation_labels(text)
        if not labels:
            return None
        try:
            point = screen_point(word, word.Selection)
        except Exception:                  # noqa: BLE001
            point = (200, 200)
        return {
            "doc_name": doc_name,
            "doc_path": "",
            "point": point,
            "items": [{"anchor_key": f"doc:{doc_name}:cite:{label}", "label": label,
                       "reference": None, "doc_name": doc_name, "kind": "label_fallback"}
                      for label in labels],
        }

    def _footnote_payload(self, word, doc_name: str, selection=None) -> dict | None:
        """光标在脚注/尾注里时（不在域索引覆盖范围内）单独处理。"""
        try:
            selection = selection if selection is not None else word.Selection
            notes = selection.Footnotes
            if notes is not None and notes.Count >= 1:
                text = notes(1).Range.Text or ""
                anchor_key = footnote_anchor(doc_name, text, _hash_text(text))
            else:
                endnotes = selection.Endnotes
                if endnotes is None or endnotes.Count < 1:
                    return None
                text = endnotes(1).Range.Text or ""
                anchor_key = footnote_anchor(doc_name, text, _hash_text(text), prefix="en")
        except Exception:                  # noqa: BLE001
            return None
        try:
            point = screen_point(word, word.Selection)
        except Exception:                  # noqa: BLE001
            point = (200, 200)
        return {"doc_name": doc_name, "doc_path": "", "point": point,
                "items": [{"anchor_key": anchor_key, "label": "", "reference": None,
                           "doc_name": doc_name, "kind": "footnote"}]}