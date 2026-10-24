# plugins/reference/plugin.py
"""参考文献助手插件。

把 Word 里的引用（Zotero/EndNote 域、脚注/尾注、`REF _Ref…` 交叉引用）与本地 CSV 文献库绑定：
- 主界面「参考文献助手」页：管理整库（浏览/搜索/编辑/导入导出/孤立锚点）；
- 数据层：`references.csv` 是唯一事实源（用户可整份替换），SQLite 只是派生索引；
- 解析层：离线读 .docx（不依赖 Word 正在运行），实测 100 MB 文档 151 ms；
- 交互层（P4，下一阶段）：光标停在引用处时在正文旁弹出可拖动/置顶/可关闭的悬浮窗。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.sdk.plugin_base import PluginBase
from plugins.reference.library_page import LibraryPage
from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.reference_store import ReferenceStore, ReferenceStoreError

if TYPE_CHECKING:
    from core.sdk.context import PluginContext

logger = logging.getLogger(__name__)


class ReferencePlugin(PluginBase):
    """参考文献助手：主页面 + 数据层 + 解析层。"""

    def __init__(self) -> None:
        super().__init__()
        self._store: ReferenceStore | None = None
        self._index: ReferenceIndex | None = None
        self._page: LibraryPage | None = None

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
        ctx.logger.info(self.tr("log.activated"))

    def _build_page(self):
        if self._page is None:
            self._page = LibraryPage(self.ctx, self._store, self._index)
        return self._page

    def deactivate(self) -> None:
        if self._index is not None:
            self._index.close()
            self._index = None
        self._page = None
        super().deactivate()
