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
from typing import TYPE_CHECKING

from core.sdk.plugin_base import PluginBase
from plugins.reference.library_page import LibraryPage
from plugins.reference.overlay import OverlayEntry, ReferenceOverlay
from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.libraries import LibraryRegistry
from plugins.reference.notes import NoteService, NoteStore
from plugins.reference.reference_store import Reference, ReferenceStore, ReferenceStoreError
from plugins.reference.matching import SnapshotStore, cite_key_set, diff
from plugins.reference.scan_cache import ScanCache, fingerprint
from plugins.reference.switch_plan import KEEP, NEW, PROMPT, SWITCH, plan_switch
from plugins.reference.word_watch import WordWatch

if TYPE_CHECKING:
    from core.sdk.context import PluginContext

logger = logging.getLogger(__name__)


def _label_from_key(anchor_key: str) -> str:
    """`doc:论文.docx:cite:12` → `12`；其它身份键返回空串。"""
    marker = ":cite:"
    return anchor_key.rsplit(marker, 1)[-1] if marker in anchor_key else ""


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
        self._scan_cache: ScanCache | None = None
        self._last_point: tuple[int, int] = (200, 200)
        self._last_items: list[dict] = []
        self._shown_signature: tuple[str, ...] = ()
        self._bib_cache: dict[str, float] = {}     # 文档路径 → 已导入的 mtime

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        self._libraries = LibraryRegistry()
        self._libraries.ensure_default()
        self._data_dir = self._libraries.active_path()
        self._store = ReferenceStore(directory=self._data_dir)
        self._index = ReferenceIndex(db_path=self._data_dir / "index.db")
        self._scan_cache = ScanCache(db_path=self._data_dir / "doc_scan.db")
        self._notes = NoteStore(directory=self._data_dir)
        self._note_service = NoteService(self._store, self._notes)
        try:
            self._store.ensure_directory()
            self._index.ensure(self._store)
        except ReferenceStoreError as exc:      # 数据目录不可写也不要挡住启动
            logger.warning("参考文献数据层初始化失败：%s", exc)

        ctx.ui.add_page("page_label", self._build_page, order=20,
                        icon="book", plugin_id="reference")
        self._start_watch()
        ctx.logger.info(self.tr("log.activated"))

    def _build_page(self):
        if self._page is None:
            self._page = LibraryPage(self.ctx, self._store, self._index, plugin=self,
                                    note_service=self._note_service,
                                    libraries=self._libraries.names(),
                                    active_library=self._libraries.active_name(),
                                    on_switch_library=self.switch_library,
                                    on_create_library=self.create_library)
        return self._page

    # ---------------- 选区监听 ----------------
    def _start_watch(self) -> None:
        if self._watch is not None or self._store is None:
            return
        self._watch = WordWatch(cache=self._scan_cache)
        self._watch.citationFound.connect(self._on_citation_found)
        self._watch.citationCleared.connect(self._on_citation_cleared)
        self._watch.statusChanged.connect(self._on_watch_status)
        self._watch.start()

    def _ensure_overlay(self) -> ReferenceOverlay:
        if self._overlay is None:
            self._overlay = ReferenceOverlay(self._store, self.tr,
                                        note_service=self._note_service,
                                        on_pick=self._on_note_picked)
            self._overlay.dismissed.connect(self._on_overlay_dismissed)
            self._overlay.bindRequested.connect(self._on_bind_requested)
            self._overlay.createRequested.connect(self._on_create_requested)
            self._overlay.noteSaved.connect(lambda _key: self.refresh_page())
        return self._overlay

    def _entry_for_item(self, item: dict) -> OverlayEntry | None:
        """把一个选区命中项解析成悬浮窗条目（域元数据 → 库 → anchors.csv 绑定）。"""
        store = self._store
        anchor_key = item.get("anchor_key") or ""
        if not anchor_key or anchor_key in self._dismissed:
            return None
        doc_name = item.get("doc_name") or ""
        label = item.get("label") or _label_from_key(anchor_key)

        reference = store.get(anchor_key)
        if (reference is None and item.get("kind") == "label_fallback"
                and item.get("reference") is None):
            return None                    # 编号兜底只对"库里已有这条文献"的情况弹窗
        if reference is None and item.get("reference") is not None:
            try:                                   # 域里带元数据 → 顺手入库
                reference = store.upsert(item["reference"])
            except ReferenceStoreError as exc:
                logger.warning("自动导入文献失败：%s", exc)
                reference = item["reference"]
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
        try:
            store.reload_if_changed()
        except ReferenceStoreError as exc:
            logger.warning("读取文献库失败：%s", exc)

        items = payload.get("items") or []
        self._last_items = items
        point = payload.get("point") or (200, 200)
        self._last_point = (int(point[0]), int(point[1]))
        self._import_bibliography(payload.get("doc_path") or "",
                                  payload.get("doc_name") or "")

        entries = []
        seen: set[str] = set()
        for item in items:
            entry = self._entry_for_item(item)
            if entry is None:
                continue
            resolved = self._store.resolve_key(entry.anchor_key) or entry.anchor_key
            if resolved in seen:                       # 同一文献的多个锚点 → 只留一张卡
                continue
            seen.add(resolved)
            entries.append(entry)
        if not entries:
            return
        overlay = self._ensure_overlay()
        signature = tuple(entry.anchor_key for entry in entries)
        if signature == self._shown_signature and overlay.isVisible():
            return                                  # 同一组锚点且已在显示 → 不重画，避免闪烁
        self._shown_signature = signature
        overlay.show_entries(entries, self._last_point)

    def _import_bibliography(self, doc_path: str, doc_name: str) -> None:
        """把文末"参考文献"列表解析进库（同一文档按 mtime 只做一次）。

        EndNote 的引用域常常只是空壳，但文末列表是完整文本 —— 用它补元数据，
        并让 `doc:<文档>:cite:<n>` 成为这些文献的别名，于是"点了没反应"变成"有信息可看"。
        """
        if not doc_path or not os.path.exists(doc_path):
            return
        try:
            mtime = os.path.getmtime(doc_path)
        except OSError:
            return
        if self._bib_cache.get(doc_path) == mtime:
            return
        from plugins.reference.docx_scan import scan_docx

        try:
            result = scan_docx(doc_path, doc_name or os.path.basename(doc_path))
        except Exception as exc:                    # noqa: BLE001 - 解析失败不影响弹窗
            logger.warning("解析参考文献列表失败：%s", exc)
            return
        imported = 0
        for reference in result.references:
            try:
                self._store.upsert(reference)       # 保留用户已有备注
                imported += 1
            except ReferenceStoreError as exc:
                logger.warning("导入参考文献条目失败：%s", exc)
        self._bib_cache[doc_path] = mtime
        if imported:
            logger.info("从文末参考文献列表补入 %d 条元数据（%s）", imported,
                        doc_name or os.path.basename(doc_path))

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
        if code.startswith("watch.scanned:"):
            return self.tr("watch.scanned", count=code.split(":", 1)[1])
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
        """子库挑选总库备注（跟随；note_id 为空表示"新建总库备注"暂不处理）。"""
        if not note_id:
            return
        try:
            self._note_service.set_inherit(ref_key, note_id)
        except ReferenceStoreError as exc:
            logger.warning("保存挑选的备注失败：%s", exc)
            return
        if self._overlay is not None:
            self._overlay.refresh_notes()
        self.refresh_page()

    def switch_library(self, name: str) -> None:
        """切换到另一个文献仓库（用户手动改绑）：重开数据文件并重绑页面。"""
        if not name or name == self._libraries.active_name():
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
        self._notes = NoteStore(directory=directory)
        self._note_service = NoteService(self._store, self._notes)
        if self._watch is not None:
            self._watch._cache = self._scan_cache
            self._watch._index.clear()

        if self._page is not None:
            self._page.rebind(self._store, self._index, self._note_service, name)
        if self._overlay is not None:
            self._overlay._store = self._store
            self._overlay._note_service = self._note_service
        logger.info("已切换到文献仓库：%s", name)

    def refresh_page(self) -> None:
        """悬浮窗写完备注后刷新页面（用户反馈"表格更新不及时"）。"""
        if self._page is not None:
            try:
                self._page.reload()
            except Exception:              # noqa: BLE001
                pass

    def _on_overlay_dismissed(self, anchor_key: str) -> None:
        if anchor_key:
            self._dismissed.add(anchor_key)
            resolved = self._store.resolve_key(anchor_key) or anchor_key
            self._dismissed.add(resolved)
            for item in self._last_items:              # 同一文献的其它锚点一并压制
                if (item.get("anchor_key") or "") == anchor_key:
                    continue
                other = self._store.resolve_key(item.get("anchor_key") or "") or ""
                if other == resolved:
                    self._dismissed.add(item.get("anchor_key") or "")
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
            self.ctx.ui.status(self.tr("status.bound", key=picker.selected_key), 3000)
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
        from plugins.reference.docx_scan import scan_docx
        from core.services.word_bridge import get_word_app

        word = get_word_app()
        document = word.ActiveDocument
        path = document.FullName
        name = document.Name
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

        candidates = list(result.references)
        candidates.extend(citation.reference for citation in result.provider_citations
                          if citation.reference is not None)
        for reference in candidates:
            try:
                self._store.upsert(reference)          # 保留用户已有备注
                stats["imported"] += 1
            except ReferenceStoreError as exc:
                logger.warning("扫描入库失败（%s）：%s", reference.ref_key, exc)
        self._bib_cache[path] = 0.0                    # 已手工扫过，避免再次全量导入
        if self._scan_cache is not None:
            self._scan_cache.mark_bibliography(path, fp, name)

        if self._watch is not None:
            stats["anchors"] = self._watch.rescan_index(word)

        keys = cite_key_set(candidates)
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
        self._apply_switch_plan(path, keys, stats)
        return stats

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

    def _apply_switch_plan(self, doc_path: str, keys: set[str], stats: dict) -> None:
        """按相似度决定是否切库，并把结论写到状态栏（不弹模态框，先让用户看见）。"""
        bindings = self._libraries.doc_bindings()
        bound = bindings.get(doc_path, "")
        plan = plan_switch(keys, self._library_snapshots(),
                           current_library=self._libraries.active_name(),
                           bound_library=bound,)
        stats["switch"] = plan.action

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

    def create_library(self, name: str) -> None:
        """新建仓库并切过去（扫描后的确认框会用到）。"""
        try:
            self._libraries.create(name)
        except ValueError as exc:
            logger.warning("新建仓库失败：%s", exc)
            return
        self.switch_library(name)

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
            self._ensure_overlay().show_entries([entry], self._last_point)

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
