# plugins/navigator/plugin.py
"""Word 导航助手插件。

扫描文档中的标题、图表题注与公式，生成可搜索、可跳转的导航面板。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

from core.sdk.plugin_base import PluginBase
from plugins.navigator.word_navigator_dialog import WordNavigatorDialog

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class NavigatorPlugin(PluginBase):
    """导航助手：非模态对话框，同一时间只保留一个实例。"""

    def __init__(self) -> None:
        super().__init__()
        self._dialog: WordNavigatorDialog | None = None

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        ctx.ui.add_tool("📑 Word导航助手", self.open, order=10, plugin_id="navigator")
        ctx.ui.add_tray_action("Word导航助手", self.open, order=10, plugin_id="navigator")
        ctx.logger.info("导航助手插件已激活")

    def deactivate(self) -> None:
        self._dialog = None
        super().deactivate()

    def open(self) -> None:
        """打开或激活导航助手窗口。"""
        if self._dialog is not None:
            try:
                self._dialog.raise_()
                self._dialog.activateWindow()
                return
            except RuntimeError:
                self._dialog = None  # C++ 对象已销毁，下次重建

        try:
            dialog = WordNavigatorDialog(self.ctx.ui.main_window() if hasattr(self.ctx.ui, "main_window") else None)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(self._on_dialog_destroyed)
            self._dialog = dialog
            dialog.show()
        except Exception:
            self._log_exception("无法打开 Word 导航助手")

    def _on_dialog_destroyed(self, *_args) -> None:
        self._dialog = None

    def _log_exception(self, message: str) -> None:
        if self.ctx is not None:
            self.ctx.logger.exception(message)
