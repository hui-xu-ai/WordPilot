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
from plugins.reference.reference_store import Reference, ReferenceStore, ReferenceStoreError
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
        self._last_point: tuple[int, int] = (200, 200)
        self._last_items: list[dict] = []
        self._shown_signature: tuple[str, ...] = ()
        self._bib_cache: dict[str, float] = {}     # 文档路径 → 已导入的 mtime

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        self._store = ReferenceStore()
        self._index = ReferenceIndex()
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
            self._page = LibraryPage(self.ctx, self._store, self._index)
        return self._page

    # ---------------- 选区监听 ----------------
    def _start_watch(self) -> None:
        if self._watch is not None or self._store is None:
            return
        self._watch = WordWatch()
        self._watch.citationFound.connect(self._on_citation_found)
        self._watch.citationCleared.connect(self._on_citation_cleared)
        self._watch.start()

    def _ensure_overlay(self) -> ReferenceOverlay:
        if self._overlay is None:
            self._overlay = ReferenceOverlay(self._store, self.tr)
            self._overlay.dismissed.connect(self._on_overlay_dismissed)
            self._overlay.bindRequested.connect(self._on_bind_requested)
            self._overlay.createRequested.connect(self._on_create_requested)
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

        entries = [entry for entry in (self._entry_for_item(item) for item in items) if entry]
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

    def _on_citation_cleared(self) -> None:
        if self._overlay is not None:
            self._overlay.hide_overlay()
        self._dismissed.clear()        # 光标离开后允许再次弹出
        self._shown_signature = ()

    def _on_overlay_dismissed(self, anchor_key: str) -> None:
        if anchor_key:
            self._dismissed.add(anchor_key)
        self._shown_signature = ()     # 允许下次点击重新弹出

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
        self._page = None
        super().deactivate()
