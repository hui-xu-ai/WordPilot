# plugins/navigator/plugin.py
"""Word 导航助手插件。

扫描文档中的标题、图表题注与公式，生成可搜索、可跳转的导航面板。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.sdk.plugin_base import PluginBase
from core.services.window_manager import CloseBehavior, Layer, Mode
from plugins.navigator.word_navigator_dialog import WordNavigatorDialog

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class NavigatorPlugin(PluginBase):
    """导航助手：非模态对话框，同一时间只保留一个实例（由 WindowManager 保障）。"""

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        ctx.ui.add_tool("📑 Word导航助手", self.open, order=10, plugin_id="navigator")
        ctx.ui.add_tray_action("Word导航助手", self.open, order=10, plugin_id="navigator")
        ctx.logger.info("导航助手插件已激活")

    def deactivate(self) -> None:
        super().deactivate()

    def open(self) -> None:
        """打开或激活导航助手窗口。"""
        windows = self.ctx.windows
        if windows is None:
            self.ctx.logger.error("窗口服务不可用，无法打开导航助手")
            return
        try:
            windows.open(
                WordNavigatorDialog.WINDOW_ID,
                lambda: WordNavigatorDialog(config=self.ctx.config,
                                            window_manager=windows),
                owner="navigator",
                mode=Mode.SINGLETON,
                layer=Layer.PALETTE,
                # 关闭即销毁：对话框关闭时要释放 COM 与缓存连接，不能转隐藏复用
                behavior=CloseBehavior.DESTROY,
                remember_geometry=True,
            )
        except Exception:
            self._log_exception("无法打开 Word 导航助手")

    def _log_exception(self, message: str) -> None:
        if self.ctx is not None:
            self.ctx.logger.exception(message)
