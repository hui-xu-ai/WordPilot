# plugins/reference/plugin.py
"""参考文献助手插件。

把 Word 里的引用（Zotero/EndNote 域、脚注/尾注）与本地 CSV 文献库绑定：
- 主页面「参考文献助手」：管理整库（浏览/搜索/编辑/导入导出/孤立锚点）；
- 数据层：`references.csv` 是唯一事实源（用户可整份替换），SQLite 只是派生索引；
- 解析层：离线读 .docx（不依赖 Word），实测 100 MB 文档 151 ms；
- 交互层：光标停在引用处时，在正文旁弹出可拖动/置顶/可关闭的悬浮窗；
  EndNote"只有域壳没有数据"的引用会显示「未绑定」，可现场绑定或新建。
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from core.sdk.plugin_base import PluginBase
from plugins.reference.library_page import LibraryPage
from plugins.reference.overlay import OverlayEntry, ReferenceOverlay
from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.libraries import LibraryRegistry
from plugins.reference.master_notes import clear_master
from plugins.reference.notes import NoteService, NoteStore
from plugins.reference.notes_migration import migrate_legacy_notes
from plugins.reference.bib_import import import_bibliography
from plugins.reference.doc_bindings import bind_doc
from plugins.reference.overlay_debug import hint_missing_reference
from plugins.reference.pet_anchor import (pet_anchor_point, pet_preferred,
                                          pet_screen_rect, resolve_window)
from plugins.reference.watch_bridge import connect_watch, on_document_switched
from plugins.reference.watch_bridge import push_current_document
from plugins.reference.reference_store import Reference, ReferenceStore, ReferenceStoreError
from plugins.reference.matching import SnapshotStore, cite_key_set, diff
from plugins.reference.scan_cache import ScanCache, fingerprint
from plugins.reference.scan_routing import route_before_import, suggest_library_name
from plugins.reference.switch_plan import KEEP, NEW, PROMPT, SWITCH, plan_switch
from plugins.reference.word_watch import WordWatch

if TYPE_CHECKING:
    from core.sdk.context import PluginContext

logger = logging.getLogger(__name__)


def _label_from_key(anchor_key: str) -> str:
    """`doc:论文.docx:cite:12` → `12`；其它身份键返回空串。"""
    marker = ":cite:"
    return anchor_key.rsplit(marker, 1)[-1] if marker in anchor_key else ""


def _screen_of(x: int, y: int) -> str:
    """某个屏幕坐标落在哪块屏（名字@原点）。"""
    try:
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.screenAt(QPoint(int(x), int(y))) or QGuiApplication.primaryScreen()
        area = screen.geometry()
        return f"{screen.name()}@{area.x()},{area.y()}"
    except Exception:                      # noqa: BLE001
        return "?"


def _same_document(a: str, b: str) -> bool:
    """判断两个"文档身份"是不是同一篇（路径 vs 文件名混用也要认出来）。

    `WordWatch` 里 `doc_identity = FullName or doc_name`：Read FullName 失败时会退化成
    纯文件名，而 payload 里带的是全路径。直接比较字符串会把**当前文档的命中**当成过期
    丢掉（实测：点 Zotero 的引用被丢弃，②③④ 全不出现）。所以按文件名比较。
    """
    if not a or not b:
        return True
    if os.path.normcase(a) == os.path.normcase(b):
        return True
    return os.path.normcase(os.path.basename(a)) == os.path.normcase(os.path.basename(b))


def _pet_window():
    """宠物窗口（已证明可见）：拿它给悬浮卡当属主，避免"窗口存在但不显示"。"""
    try:
        from core.services.pet_host import PetHost

        window = PetHost.instance().window()
        return window if window is not None and window.isVisible() else None
    except Exception:                      # noqa: BLE001 - 拿不到就退化成独立窗口
        return None


def _force_topmost(widget) -> None:
    """用 Win32 把窗口压到最上层（Qt 的 flags 在少数环境不生效）。

    必须声明 `argtypes`：不声明时 `HWND_TOPMOST(-1)` 会按 32 位 int 传递，64 位下被截断，
    `SetWindowPos` 直接返回 0（实测"强制置顶=失败"，卡片因此被别的置顶窗口盖住）。
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint]
        user32.SetWindowPos.restype = wintypes.BOOL
        swp_nosize, swp_nomove, swp_noactivate, swp_showwindow = 0x1, 0x2, 0x10, 0x40
        user32.SetWindowPos(wintypes.HWND(int(widget.winId())), wintypes.HWND(-1), 0, 0, 0, 0,
                            swp_nomove | swp_nosize | swp_noactivate | swp_showwindow)
    except Exception:                      # noqa: BLE001 - 置顶失败不影响功能
        pass


def _pet_on_work_screen(pet_rect) -> bool:
    """宠物是否和"前台窗口"在同一块屏（不在同一块，用户就看不到贴着它的卡片）。

    老用户现场：宠物在副屏 Mi Monitor(1920..3840)，Word 在主屏 → 卡片贴着宠物画在副屏，
    用户只看到"没弹窗"。取不到窗口信息时按"同屏"处理（维持原规则，不乱跳）。
    """
    if pet_rect is None:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        user32.GetWindowRect(user32.GetForegroundWindow(), ctypes.byref(rect))
        foreground = _screen_of((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        pet = _screen_of(pet_rect[0] + pet_rect[2] // 2, pet_rect[1] + pet_rect[3] // 2)
        return foreground == pet
    except Exception:                      # noqa: BLE001
        return True


class ReferencePlugin(PluginBase):
    """参考文献助手：主页面 + 数据层 + 解析层 + 选区悬浮窗。"""

    def __init__(self) -> None:
        super().__init__()
        self._store: ReferenceStore | None = None
        self._index: ReferenceIndex | None = None
        self._page: LibraryPage | None = None
        self._overlay: ReferenceOverlay | None = None
        self._watch: WordWatch | None = None
        self._dismissed: set[str] = set()
        self._dismissed_signature: tuple[str, ...] = ()
        self._scan_cache: ScanCache | None = None
        self._last_point: tuple[int, int] = (200, 200)
        self._last_pet_rect: tuple[int, int, int, int] | None = None
        self._last_items: list[dict] = []
        self._shown_signature: tuple[str, ...] = ()
        self._bib_cache: dict[str, float] = {}     # 文档路径 → 已导入的 mtime

    def assets(self):
        """本插件的**用户资产**：文献库本体、总库备注、库列表与文档绑定（清理时永不删）。

        每次盘点实时调用 —— 所以这里遍历的是**当前**所有子库，新建库立刻受保护。
        `doc_scan.db` / `index.db` 是缓存，故意不列进来。
        """
        items: list = []
        try:
            items.extend([self._libraries.registry_path, self._libraries.bindings_path,
                          self._libraries.root / "notes.csv"])
            for name in self._libraries.names():
                items.append(self._libraries.library_dir(name) / "references.csv")
        except Exception:                  # noqa: BLE001 - 未激活/读失败就只返回已知的
            logger.debug("收集参考文献资产清单失败", exc_info=True)
        return items

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        self._libraries = LibraryRegistry()
        self._libraries.migrate_default_name()   # 旧「默认库」按当前语言改名（有数据才动）
        self._libraries.ensure_default()
        self._data_dir = self._libraries.active_path()
        self._active_name = self._libraries.active_name()
        self._store = ReferenceStore(directory=self._data_dir)
        self._index = ReferenceIndex(db_path=self._data_dir / "index.db")
        self._scan_cache = ScanCache(db_path=self._data_dir / "doc_scan.db")
        # C1：总库是**全局一份**（config/references/notes.csv），不随子库切换；
        # 旧版本把总库写在每个子库目录里，这里合并迁移一次（原文件改名保留）。
        self._master_notes = NoteStore()
        self._migrate_legacy_notes()
        self._notes = self._master_notes
        self._note_service = NoteService(self._store, self._master_notes)
        try:
            # 方案 A 回填：给历史总库备注补上文献信息快照（只填空值；子库已删的补不了，
            # 挑选面板会显示 ref_key 提醒这条"失了上下文"）。
            filled = self._note_service.backfill_master_meta()
            if filled:
                ctx.logger.info("总库备注已补文献信息 %d 条", filled)
        except Exception:                      # noqa: BLE001 - 回填失败不该挡住启动
            logger.debug("回填总库文献信息失败", exc_info=True)
        try:
            self._store.ensure_directory()
            self._index.ensure(self._store)
        except ReferenceStoreError as exc:      # 数据目录不可写也不要挡住启动
            logger.warning("参考文献数据层初始化失败：%s", exc)

        ctx.ui.add_page("page_label", self._build_page, order=20,
                        icon="book", plugin_id="reference")
        self.subscribe("i18n.language_changed", self._on_language_changed)
        self._start_watch()
        self._connect_doc_signal()
        ctx.logger.info(self.tr("log.activated"))

    def _migrate_legacy_notes(self) -> None:
        """把旧版分散在各子库目录里的 notes.csv 合并进全局总库（实现见 notes_migration）。"""
        migrate_legacy_notes(self._libraries, self._master_notes)

    def _connect_doc_signal(self) -> None:
        """把监听线程的信号经 QObject 中转（实现见 watch_bridge）。"""
        connect_watch(self, getattr(self, "_watch", None))

    def _on_language_changed(self, _event=None, _lang=None) -> None:
        """界面语言切换：页面文案由主窗口统一调 `retranslateUi`，悬浮窗要自己重译。"""
        overlay = getattr(self, "_overlay", None)
        if overlay is not None:
            try:
                overlay.retranslateUi()
            except Exception:                  # noqa: BLE001 - 重译失败不该影响切换语言
                logger.debug("悬浮窗重译失败", exc_info=True)

    def _on_document_changed(self, name: str) -> None:
        """换文档：更新显示并清掉旧上下文（实现见 watch_bridge）。"""
        on_document_switched(self, name)

    def _push_current_document(self) -> None:
        """页面刚建好时先显示当前文档（监听只在"切换"时发信号，实现见 watch_bridge）。"""
        push_current_document(self)

    def _build_page(self):
        if self._page is None:
            self._page = LibraryPage(self.ctx, self._store, self._index, plugin=self,
                                    note_service=self._note_service,
                                    libraries=self._libraries.names(),
                                    active_library=self._libraries.active_name(),
                                    on_switch_library=self.switch_library,
                                    on_create_library=self.create_library,
                 on_delete_library=self.delete_library,
                 on_publish_note=self.publish_note_to_master,
                 on_publish_all_notes=self.publish_all_notes_to_master,
                 on_dedupe=self.dedupe_references)
        self._push_current_document()
        return self._page

    # ---------------- 选区监听 ----------------
    def _start_watch(self) -> None:
        if self._watch is not None or self._store is None:
            return
        self._watch = WordWatch(cache=self._scan_cache)
        # 不直连槽函数：槽会在工作线程跑，统一经 _WatchRelay 转到 GUI 线程
        self._watch.start()

    def _ensure_overlay(self) -> ReferenceOverlay:
        if self._overlay is None:
            # 用**宠物窗口**当属主：独立顶层窗口在这台机器上出现过"Windows 说可见、
            # 抓屏里有、人眼看不到"。属主窗口（owned window）一定跟随属主显示，
            # 而宠物窗口已被证明是可见的（用户一直看得见它）。
            self._overlay = ReferenceOverlay(self._store, self.tr,
                                        parent=_pet_window(),
                                        note_service=self._note_service,
                                        on_pick=self._on_note_picked)
            self._overlay.dismissed.connect(self._on_overlay_dismissed)
            self._overlay.bindRequested.connect(self._on_bind_requested)
            self._overlay.createRequested.connect(self._on_create_requested)
            self._overlay.noteSaved.connect(lambda _key: self.refresh_page())
        return self._overlay

    def _identity_of(self, anchor_key: str) -> str:
        """同一篇文献的身份：标题（小写）优先，其次解析后的键；都取不到就用锚点本身。"""
        reference = self._store.get(anchor_key)
        title = (getattr(reference, "title", "") or "").strip().lower()
        if title:
            return f"title:{title}"
        return f"key:{self._store.resolve_key(anchor_key) or anchor_key}"

    def _is_dismissed(self, anchor_key: str) -> bool:
        """这个锚点是否被用户点过 ✕。

        只有**同一组引用**才压制：点 ✕ 压制的是当次那几张卡，换成另一个引用就该正常弹。
        以前是永久压制 `_dismissed`，光标不离开就再也不弹（用户现场：关一次之后连换引用都没反应）。
        """
        return (anchor_key in self._dismissed
                or self._identity_of(anchor_key) in self._dismissed)

    def _entry_for_item(self, item: dict) -> OverlayEntry | None:
        """把一个选区命中项解析成悬浮窗条目（域元数据 → 库 → anchors.csv 绑定）。"""
        store = self._store
        anchor_key = item.get("anchor_key") or ""
        if not anchor_key or self._is_dismissed(anchor_key):
            return None
        doc_name = item.get("doc_name") or ""
        label = item.get("label") or _label_from_key(anchor_key)

        reference = store.get(anchor_key)
        if reference is None and item.get("reference") is not None:
            try:                                   # 域里带元数据 → 顺手入库（键最准）
                reference = store.upsert(item["reference"])
            except ReferenceStoreError as exc:
                logger.warning("自动导入文献失败：%s", exc)
                reference = item["reference"]
        if reference is None:
            # 锚点键查不到就按**文中编号**兜底：重建/换库后条目的 ref_key 会变（`zotero:906`
            # 变成 doi/标题键），旧实现只在 `label_fallback` 时才兜底，于是换库后点了只有
            # 空白卡（实测诊断行 `有文献=0/1`）。
            reference = store.find_by_label(label)
            if reference is None and item.get("kind") == "label_fallback":
                return None                # 空壳域 + 库里没有同编号 → 不打扰
        if reference is None and doc_name and label:
            anchor = store.anchor_for(doc_name, label)   # 用户之前绑定过
            if anchor is not None:
                reference = store.get(anchor.anchor_key)

        meta_parts = [
            reference.authors if reference else "",
            reference.year if reference else "",
            reference.container if reference else "",
            f"DOI {reference.doi}" if reference and reference.doi else "",
        ]
        return OverlayEntry(
            anchor_key=anchor_key,
            label=label or (reference.cite_label if reference else ""),
            title=reference.title if reference else "",
            meta=" · ".join(part for part in meta_parts if part),
            note=reference.note if reference else "",
            doc_name=doc_name,
            force_edit=reference is not None,
        )

    def _on_citation_found(self, payload: dict) -> None:
        store = self._store
        if store is None or not isinstance(payload, dict):
            return
        # 过期命中必须丢掉：切文档瞬间，监听线程里上一份文档的命中会被延迟送到 GUI，
        # 若照旧处理，会拿 A 文档的锚点去 B 库查（→ 空白卡 `有文献=0/1`），
        # 并把 A 的文末列表写进 B 库（实测 22:42:32：EndNote 的 43 条写进了 zotero）。
        payload_doc = str(payload.get("doc_path") or "")
        current_doc = str(getattr(getattr(self, "_watch", None), "_last_doc_identity", "") or "")
        if payload_doc and current_doc and not _same_document(payload_doc, current_doc):
            logger.info("丢弃过期命中：来自 %s，当前是 %s",
                        os.path.basename(payload_doc), os.path.basename(current_doc))
            return
        try:
            store.reload_if_changed()
        except ReferenceStoreError as exc:
            logger.warning("读取文献库失败：%s", exc)

        items = payload.get("items") or []
        self._last_items = items
        # 换了一组引用 → 解除"上次点 ✕ 的压制"，否则同一篇里换引用也不弹（用户现场）
        signature_now = tuple(str(item.get("anchor_key") or "") for item in items)
        if signature_now != getattr(self, "_dismissed_signature", ()):
            self._dismissed = set()
        point = payload.get("point") or (200, 200)
        self._last_point = (int(point[0]), int(point[1]))
        pet_anchor = self._pet_anchor_point()
        if pet_anchor is not None and self._pet_preferred():
            self._last_point = pet_anchor
            self._last_pet_rect = self._pet_rect()
        self._import_bibliography(payload.get("doc_path") or "",
                                  payload.get("doc_name") or "")

        entries = []
        seen: set[str] = set()
        seen_keys: set[str] = set()
        for item in items:
            entry = self._entry_for_item(item)
            if entry is None:
                continue
            identity = self._identity_of(entry.anchor_key)
            if identity in seen:                       # 同一文献的多个锚点 → 只留一张卡
                continue
            seen.add(identity)
            # 再按**解析到的文献键**去重：域锚点的 key 是 `zotero:…`/`doi:…`，文字补充的 key 是
            # `doc:<文档>:cite:<编号>`，两者落到同一篇时会各留一张（用户现场：一个编号两张卡）。
            resolved = str(getattr(entry, "ref_key", "") or "")
            if resolved and resolved in seen_keys:
                continue
            if resolved:
                seen_keys.add(resolved)
            entries.append(entry)
        if not entries:
            hint_missing_reference(self, items)   # 记录原因 + 界面提示
            return
        overlay = self._ensure_overlay()
        signature = tuple(entry.anchor_key for entry in entries)
        if signature == self._shown_signature and overlay.isVisible():
            # 这条日志是"卡片到底有没有显示"的判据：有它说明程序**认为**已经显示着。
            # `raise_()` 必须有：用户在 Word 里点击会把 Word 提到前面，之后每次命中都走
            # 这条早退分支 → 卡片再也回不到前台（用户现场：日志说显示、屏幕上看不到）。
            overlay.raise_()
            _force_topmost(overlay)                # 每次命中都重新压到最上层
            logger.debug("悬浮卡已在显示，同组引用不重画：%d 张 pos=%s 库=%s",
                        len(entries), (overlay.x(), overlay.y()),
                        getattr(self, "_active_name", ""))
            return                                  # 同一组锚点且已在显示 → 不重画，避免闪烁
        self._shown_signature = signature
        point = overlay._clamp_to_screen(*self._last_point)
        with_ref = sum(1 for entry in entries if entry.title)
        # 宠物在别的屏（用户看不到）→ 放弃"贴宠物"，退回光标定位，保证卡片出现在眼前。
        anchor_pet = self._last_pet_rect
        if not _pet_on_work_screen(anchor_pet):
            anchor_pet = None
        logger.info("显示引用卡片：%d 张 pos=%s 库=%s 有文献=%d/%d 宠物矩形=%s",
                    len(entries), point, getattr(self, "_active_name", ""),
                    with_ref, len(entries), anchor_pet)
        overlay.show_entries(entries, point, pet_rect=anchor_pet)
        _force_topmost(overlay)
        logger.info("悬浮卡落点：左上=(%d,%d) 尺寸=%dx%d 库=%s",
                    overlay.x(), overlay.y(), overlay.width(), overlay.height(),
                    getattr(self, "_active_name", ""))

    def _pet_anchor_point(self):
        """宠物作为定位锚点（窗口查找与几何兜底见 pet_anchor）。"""
        return pet_anchor_point(resolve_window(getattr(self, "ctx", None)))

    def _pet_rect(self):
        """宠物窗口矩形（贴宠物上方用；见 overlay_geometry.place_near）。"""
        return pet_screen_rect(resolve_window(getattr(self, "ctx", None)))

    @staticmethod
    def _pet_preferred() -> bool:
        return pet_preferred()

    def _import_bibliography(self, doc_path: str, doc_name: str) -> None:
        """把文末参考文献列表解析进库（实现见 bib_import）。"""
        import_bibliography(self, doc_path, doc_name)

    def _on_watch_status(self, code: str) -> None:
        """监听线程的连接/扫描状态 → 页面底部状态栏（用户要求"看得见"）。"""
        text = self._watch_status_text(code)
        if self._page is not None:
            try:
                self._page.append_status(text)
            except Exception:              # noqa: BLE001 - 页面可能还没建好
                pass
        try:
            self.ctx.ui.status(text, 2000)
        except Exception:                  # noqa: BLE001
            pass

    def _watch_status_text(self, code: str) -> str:
        for prefix in ("watch.scanned:", "watch.cached:"):
            if code.startswith(prefix):
                # 之前只处理了 scanned:，`cached:18` 会原样落到 `tr()` 里 → 缺少翻译 →
                # 界面直接显示 `reference.watch.cached:18`（用户看到的就是这行"报错"）。
                return self.tr(prefix[:-1], count=code.split(":", 1)[1])
        mapping = {
            "watch.starting": "watch.starting",
            "watch.no_word": "watch.no_word",
            "watch.scanning": "watch.scanning",
        }
        return self.tr(mapping.get(code, code))

    def _on_citation_cleared(self) -> None:
        if self._overlay is not None:
            self._overlay.hide_overlay()
        self._dismissed.clear()        # 光标离开后允许再次弹出
        self._shown_signature = ()

    def _on_note_picked(self, ref_key: str, note_id: str) -> None:
        """用户显式采用总库某条备注（复制进子库并记录来源）。"""
        if not note_id:
            return
        try:
            self._note_service.adopt_master_note(ref_key, note_id)
        except ReferenceStoreError as exc:
            logger.warning("保存挑选的备注失败：%s", exc)
            return
        if self._overlay is not None:
            self._overlay.refresh_notes()
        self.refresh_page()

    def bind_document(self, doc_path: str, library: str) -> bool:
        """把某份文档关联到指定库（实现见 doc_bindings）。"""
        return bind_doc(self._libraries, doc_path, library)

    def switch_library(self, name: str, update_page: bool = True) -> None:
        """切换到另一个文献仓库（用户手动改绑）：重开数据文件并重绑页面。

        比较基准是**实际已打开的库** `_active_name`，不是 registry 的 active：
        `create_library()` 会先把 registry.active 改成新库，若拿它比较就会提前返回，
        于是新库注册了、Store 却还停在旧库（审查 S1）。
        """
        if not name or name == getattr(self, "_active_name", None):
            return
        try:
            directory = self._libraries.set_active(name)
        except KeyError as exc:
            logger.warning("切换仓库失败：%s", exc)
            return

        if self._index is not None:
            self._index.close()
        if self._scan_cache is not None:
            self._scan_cache.close()

        self._data_dir = directory
        self._store = ReferenceStore(directory=directory)
        self._index = ReferenceIndex(db_path=directory / "index.db")
        self._scan_cache = ScanCache(db_path=directory / "doc_scan.db")
        self._notes = getattr(self, "_master_notes", None) or NoteStore()
        self._note_service = NoteService(self._store, self._notes)
        if self._watch is not None:
            self._watch._cache = self._scan_cache
            self._watch._index.clear()

        if update_page and self._page is not None:
            # R6：只有 GUI 线程可以直接动页面控件；后台扫描线程传 update_page=False，
            # 由页面在扫描完成的回调（主线程）里再 rebind 一次。
            self._page.rebind(self._store, self._index, self._note_service, name)
        if self._overlay is not None:
            self._overlay._store = self._store
            self._overlay._note_service = self._note_service
        self._active_name = name
        logger.info("已切换到文献仓库：%s", name)

    def clear_master_notes(self) -> bool:
        """删除总库备注（实现见 master_notes：先备份再清空，可回滚）。"""
        return clear_master(self)

    def refresh_page(self) -> None:
        """悬浮窗写完备注后刷新页面（用户反馈"表格更新不及时"）。"""
        if self._page is not None:
            try:
                self._page.reload()
            except Exception:              # noqa: BLE001
                pass

    def _on_overlay_dismissed(self, anchor_key: str) -> None:
        # 关一次就关掉整个弹窗：压制当前显示的**所有**条目（[7-8] 不该只剩 [8]）
        self._dismissed_signature = tuple(
            str(item.get("anchor_key") or "") for item in self._last_items)
        for item in self._last_items:
            key = item.get("anchor_key") or ""
            if not key:
                continue
            self._dismissed.add(key)
            self._dismissed.add(self._identity_of(key))
            self._dismissed.add(self._store.resolve_key(key) or key)
        if False:
            self._dismissed.add(anchor_key)
            identity = self._identity_of(anchor_key)
            self._dismissed.add(identity)
            resolved = self._store.resolve_key(anchor_key) or anchor_key
            self._dismissed.add(resolved)
            for item in self._last_items:              # 同一文献的其它锚点一并压制
                if (item.get("anchor_key") or "") == anchor_key:
                    continue
                other_key = item.get("anchor_key") or ""
                same_identity = self._identity_of(other_key) == identity if other_key else False
                other_resolved = self._store.resolve_key(other_key) or ""
                if same_identity or other_resolved == resolved:
                    self._dismissed.add(other_key)
        # 故意保留 _shown_signature：光标没离开前不再重弹（用户在 ✕ 后原地点击会复现 bug）

    # ---------------- 未绑定引用的两种处理 ----------------
    def _on_bind_requested(self, anchor_key: str, doc_name: str) -> None:
        from plugins.reference.bind_dialog import ReferencePicker

        label = _label_from_key(anchor_key)
        picker = ReferencePicker(self._store, self.tr)
        if picker.exec() and picker.selected_key:
            try:
                self._store.bind_anchor(doc_name, label, picker.selected_key)
            except ReferenceStoreError as exc:
                logger.warning("绑定失败：%s", exc)
                self.ctx.ui.status(str(exc), 4000)
                return
            self.ctx.ui.status(self.tr("status.bound", ref=picker.selected_key), 3000)
            self._reshow(anchor_key, doc_name, label)

    def _on_create_requested(self, anchor_key: str, doc_name: str) -> None:
        label = _label_from_key(anchor_key)
        try:
            self._store.upsert(Reference(ref_key=anchor_key, provider="manual",
                                         cite_label=label, aliases=[anchor_key]))
        except ReferenceStoreError as exc:
            logger.warning("新建文献失败：%s", exc)
            self.ctx.ui.status(str(exc), 4000)
            return
        self._reshow(anchor_key, doc_name, label)

    # ---------------- 扫描（手动 + 打开页面自动） ----------------
    def scan_active_document(self) -> dict:
        """扫描当前 Word 文档：导入"参考文献列表"与带元数据的引用，并重建点击查表索引。

        返回统计字典（供页面状态栏显示）：文档名、列表条目数、域引用数、入库条数、锚点数。
        失败时抛异常，由调用方（工作线程）转成状态栏提示。
        """
        from plugins.reference.docx_scan import merge_provider_citations, scan_docx
        from core.services.word_bridge import get_word_app

        word = get_word_app()
        document = word.ActiveDocument
        path = document.FullName
        name = document.Name
        # 绑定库优先：否则本次文献会先写进当前库、再切绑定库（审查 S11）
        self._ensure_scan_library(path)
        stats: dict = {"doc_name": name, "doc_path": path, "bibliography": 0,
                       "provider": 0, "imported": 0, "anchors": 0}

        fp = fingerprint(path)
        if self._scan_cache is not None and self._scan_cache.bibliography_done(path, fp):
            # 文档没改动过：不重复解析文末列表，直接用已有库 + 缓存锚点
            stats["anchors"] = self._watch.rescan_index(word) if self._watch else 0
            stats["cached"] = 1
            return stats

        result = scan_docx(path, name)
        stats["bibliography"] = len(result.references)
        stats["provider"] = len(result.provider_citations)

        # 入库候选：以文末列表为准、域元数据只补全同一篇；匹配不上的域引用丢弃
        # （Zotero/EndNote 的域键与列表键不同，双边都入会出现大量"无编号重复条目"）。
        candidates, filled, ignored = merge_provider_citations(result)
        stats["filled"] = filled
        stats["ignored"] = ignored
        if ignored:
            logger.info("同步本文：丢弃 %d 条文末列表里没有的域引用（文档=%s）", ignored, name)

        # 先按引用集合决定仓库，再入库：否则这份文档的文献会先落进当前库，
        # 两个文档就能把默认库塞满（用户实测 294 + 13 合并成 307 条）。
        keys = cite_key_set(candidates)
        if not self._route_before_import(path, name, keys, stats):
            stats["keys"] = len(keys)      # R2：需要确认时本次不导入任何条目
            return stats

        failed = 0
        for reference in candidates:
            try:
                self._store.upsert(reference)          # 保留用户已有备注
                stats["imported"] += 1
            except ReferenceStoreError as exc:
                failed += 1
                logger.warning("扫描入库失败（%s）：%s", reference.ref_key, exc)
        if failed:
            # R3：写失败就**不能**记成"已完成" —— 旧实现照样写缓存，
            # 用户解决占用后再扫会被 cached 命中直接跳过，看起来像"扫了没反应"。
            stats["failed"] = failed
            stats["error"] = "部分条目写入失败，可重试"
            return stats
        self._bib_cache[path] = 0.0                    # 成功后才标记本次已导入
        if self._scan_cache is not None:
            self._scan_cache.mark_bibliography(path, fp, name)

        if self._watch is not None:
            stats["anchors"] = self._watch.rescan_index(word)

        stats["keys"] = len(keys)
        previous = set(SnapshotStore(self._data_dir).load().get(path, {}).get("keys", []))
        added, removed = diff(previous, keys) if previous else ([], [])
        if previous:
            stats["diff_added"] = added
            stats["diff_removed"] = removed
        try:
            SnapshotStore(self._data_dir).record(path, keys)
        except Exception as exc:                   # noqa: BLE001 - 快照失败不影响扫描
            logger.warning("记录引用快照失败：%s", exc)
        self._apply_switch_plan(path, keys, stats, already_routed=True)
        return stats

    def _suggest_library_name(self, doc_name: str) -> str:
        """用文档名当新仓库名（实现见 scan_routing）。"""
        return suggest_library_name(self._libraries.names(), doc_name)

    def _route_before_import(self, doc_path: str, doc_name: str,
                             keys: set[str], stats: dict) -> bool:
        """入库前路由到"该去的仓库"；False 表示要等用户确认（实现见 scan_routing）。"""
        return route_before_import(self, doc_path, doc_name, keys, stats)

    # ---------------- 仓库自动识别 ----------------
    def _library_snapshots(self) -> dict[str, set[str]]:
        """除当前库外，其余各库的历史快照并集（用于相似度比较）。"""
        result: dict[str, set[str]] = {}
        for name in self._libraries.names():
            if name == self._libraries.active_name():
                continue
            path = self._libraries.libraries_dir / name
            keys = SnapshotStore(path).library_snapshot()
            if keys:
                result[name] = keys
        return result

    def _apply_switch_plan(self, doc_path: str, keys: set[str], stats: dict,
                           already_routed: bool = False) -> None:
        """按相似度决定是否切库，并把结论写到状态栏（不弹模态框，先让用户看见）。"""
        bindings = self._libraries.doc_bindings()
        bound = bindings.get(doc_path, "")
        plan = plan_switch(keys, self._library_snapshots(),
                           current_library=self._libraries.active_name(),
                           bound_library=bound,)
        stats["switch"] = plan.action

        if already_routed:
            # 入库前已经按同一套规则路由过，这里只补绑定/提示，避免二次切库
            if plan.action in (SWITCH, KEEP) and plan.library:
                self._libraries.bind_document(doc_path, plan.library)
            return
        if plan.action == SWITCH and plan.library:
            self.switch_library(plan.library)
            self._libraries.bind_document(doc_path, plan.library)
            stats["switch_text"] = self.tr("switch.auto", library=plan.library,
                                           percent=int(plan.score * 100))
        elif plan.action in (PROMPT, NEW):
            # 弹框必须回到 GUI 线程：把决策放进 stats，由页面在扫描完成回调里处理
            stats["plan"] = plan
        elif plan.action == KEEP and plan.library:
            self._libraries.bind_document(doc_path, plan.library)

    def dedupe_references(self) -> tuple[int, int]:
        """把"无编号、但与某条有编号条目是同一篇"的重复文献并进那条（默认不动其它行）。

        来由：旧版「同步本文」把文末列表和域元数据都入库，同一篇出现两次
        （`bib:<hash>` 有编号 / `doi:...`、`zotero:...` 无编号）。这里**合并而不是硬删**：
        把重复条的 ref_key 收进保留条的别名，旧锚点/旧键仍能解析，然后才删掉重复行。

        返回 (合并条数, 保留条数)。
        """
        if self._store is None:
            return 0, 0
        from plugins.reference.docx_scan import work_identities

        references = list(self._store.all())
        labeled = [ref for ref in references if (ref.cite_label or "").strip()]
        by_identity: dict[str, Reference] = {}
        for ref in labeled:
            for identity in work_identities(ref):
                by_identity.setdefault(identity, ref)

        merged = 0
        for ref in references:
            if (ref.cite_label or "").strip():
                continue
            targets = {id(by_identity[key]): by_identity[key]
                       for key in work_identities(ref) if key in by_identity}
            if len(targets) != 1:
                continue                       # 无重复，或对不上唯一的保留条 → 不动
            target = next(iter(targets.values()))
            aliases = list(dict.fromkeys([*target.aliases, ref.ref_key, *ref.aliases]))
            target.aliases = [alias for alias in aliases if alias != target.ref_key]
            self._store.upsert(target)
            self._store.delete(ref.ref_key)
            merged += 1
        if merged:
            logger.info("清理重复文献：合并 %d 条，保留有编号的 %d 条", merged, len(labeled))
        return merged, len(labeled)

    def publish_note_to_master(self, ref_key: str):
        """把单篇文献的子库备注发布到总库（用户手动触发；不做自动同步）。"""
        if self._note_service is None:
            return None
        note = self._note_service.publish_to_master(ref_key)
        if note is not None:
            logger.info("备注已更新到总库：%s（%s）", ref_key, note.note_id)
            self.refresh_page()
        return note

    def publish_all_notes_to_master(self) -> tuple[int, int, int]:
        """把当前子库所有非空备注一次性更新到总库（下拉菜单里手动触发）。"""
        if self._note_service is None:
            return 0, 0, 0
        result = self._note_service.publish_all_to_master(
            getattr(self, "_active_name", "") or "")
        logger.info("全部更新到总库：新增 %d 条，已在总库 %d 条，无备注 %d 条（库=%s）",
                    result[0], result[1], result[2], getattr(self, "_active_name", ""))
        self.refresh_page()
        overlay = getattr(self, "_overlay", None)
        if overlay is not None and hasattr(overlay, "refresh_notes"):
            try:
                overlay.refresh_notes()
            except Exception:                  # noqa: BLE001
                pass
        return result

    def create_library(self, name: str) -> bool:
        """新建仓库并切过去；名字不合法时返回 False（调用方据此提示用户）。"""
        try:
            self._libraries.create(name)
        except ValueError as exc:
            logger.warning("新建仓库失败：%s", exc)
            return False
        self.switch_library(name)
        return True

    def _ensure_scan_library(self, doc_path: str) -> None:
        """扫描前把库切到该文档绑定的仓库（没有绑定则保持现状）。"""
        try:
            bound = self._libraries.doc_bindings().get(doc_path, "")
        except Exception:                      # noqa: BLE001 - 绑定读失败不该阻塞扫描
            logger.debug("读取文档绑定失败", exc_info=True)
            return
        if bound and bound != getattr(self, "_active_name", None):
            self.switch_library(bound)

    def delete_library(self, name: str) -> bool:
        """删除仓库并刷新界面（当前库被删时自动切到剩下的库）。"""
        # 删的若是当前库，必须先切走：否则 index.db / doc_scan.db 仍被本进程打开，
        # Windows 会拒绝把目录移走（实测 PermissionError WinError 5，用户看到的就是"删不掉"）。
        if getattr(self, "_active_name", None) == name:
            remaining = [lib for lib in self._libraries.names() if lib != name]
            if not remaining:
                logger.warning("删除仓库失败：至少要保留一个仓库")
                return False
            self._active_name = None                     # 强制 switch_library 真正重开
            self.switch_library(remaining[0])
        try:
            self._libraries.delete(name)
        except (KeyError, ValueError) as exc:
            logger.warning("删除仓库失败（%s）：%s", name, exc)
            return False
        if self._page is not None:
            try:
                self._page.reload()
            except Exception:                      # noqa: BLE001
                pass
        return True

    def bind_current_document(self, doc_path: str, library: str) -> None:
        self._libraries.bind_document(doc_path, library)

    def _notify_status(self, text: str) -> None:
        if self._page is not None:
            try:
                self._page.append_status(text)
            except Exception:                      # noqa: BLE001
                pass
        try:
            self.ctx.ui.status(text, 3000)
        except Exception:                          # noqa: BLE001
            pass

    def _reshow(self, anchor_key: str, doc_name: str, label: str) -> None:
        """绑定/新建之后，用库里最新数据把悬浮窗重画一次（用户不用挪光标）。"""
        entry = self._entry_for_item({"anchor_key": anchor_key, "doc_name": doc_name,
                                      "label": label, "reference": None})
        if entry is not None:
            self._ensure_overlay().show_entries([entry], self._last_point,
                                                pet_rect=self._last_pet_rect)

    # ---------------- 生命周期 ----------------
    def deactivate(self) -> None:
        if self._watch is not None:
            self._watch.stop()
            self._watch.wait(2000)
            self._watch = None
        if self._overlay is not None:
            self._overlay.hide_overlay()
            self._overlay = None
        if self._index is not None:
            self._index.close()
            self._index = None
        if self._scan_cache is not None:
            self._scan_cache.close()
            self._scan_cache = None
        self._page = None
        super().deactivate()
