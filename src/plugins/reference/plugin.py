# plugins/reference/plugin.py
"""参考文献助手插件。

把 Word 里的引用（Zotero/EndNote 域、脚注/尾注、`REF _Ref…` 交叉引用）与本地 CSV 文献库绑定：
- 主页面「参考文献助手」：管理整库（浏览/搜索/编辑/导入导出/孤立锚点）；
- 数据层：`references.csv` 是唯一事实源（用户可整份替换），SQLite 只是派生索引；
- 解析层：离线读 .docx（不依赖 Word），实测 100 MB 文档 151 ms；
- 交互层：光标停在引用处时，在正文旁弹出**可拖动/置顶/可关闭**的悬浮窗（多篇按编号堆叠）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.sdk.plugin_base import PluginBase
from plugins.reference.library_page import LibraryPage
from plugins.reference.overlay import OverlayEntry, ReferenceOverlay
from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.reference_store import ReferenceStore, ReferenceStoreError
from plugins.reference.word_watch import WordWatch

if TYPE_CHECKING:
    from core.sdk.context import PluginContext

logger = logging.getLogger(__name__)


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
        return self._overlay

    def _on_citation_found(self, payload: dict) -> None:
        store = self._store
        if store is None or not isinstance(payload, dict):
            return
        try:
            store.reload_if_changed()
        except ReferenceStoreError as exc:
            logger.warning("读取文献库失败：%s", exc)

        entries: list[OverlayEntry] = []
        for item in payload.get("items") or []:
            anchor_key = item.get("anchor_key") or ""
            if not anchor_key or anchor_key in self._dismissed:
                continue
            reference = store.get(anchor_key)
            if reference is None and item.get("reference") is not None:
                try:                                   # 域里带元数据 → 顺手入库
                    reference = store.upsert(item["reference"])
                except ReferenceStoreError as exc:
                    logger.warning("自动导入文献失败：%s", exc)
                    reference = item["reference"]
            meta_parts = [
                reference.authors if reference else "",
                reference.year if reference else "",
                reference.container if reference else "",
                f"DOI {reference.doi}" if reference and reference.doi else "",
            ]
            entries.append(OverlayEntry(
                anchor_key=anchor_key,
                label=item.get("label") or (reference.cite_label if reference else ""),
                title=reference.title if reference else "",
                meta=" · ".join(part for part in meta_parts if part),
                note=reference.note if reference else "",
            ))

        if not entries:
            return
        self._ensure_overlay().show_entries(entries, payload.get("point") or (200, 200))

    def _on_citation_cleared(self) -> None:
        if self._overlay is not None:
            self._overlay.hide_overlay()
        self._dismissed.clear()        # 光标离开后允许再次弹出

    def _on_overlay_dismissed(self, anchor_key: str) -> None:
        if anchor_key:
            self._dismissed.add(anchor_key)

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
