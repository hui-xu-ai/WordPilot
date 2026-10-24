# plugins/navigator/plugin.py
"""Word 导航助手插件。

扫描文档中的标题、图表题注与公式，生成可搜索、可跳转的导航面板。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from core.sdk.plugin_base import PluginBase
from core.services.window_manager import CloseBehavior, Layer, Mode
from plugins.navigator.word_navigator_dialog import WordNavigatorDialog

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class NavigatorPlugin(PluginBase):
    """导航助手：非模态对话框，同一时间只保留一个实例（由 WindowManager 保障）。"""

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        ctx.ui.add_tool("tool_label", self.open, order=10, icon="compass",
                        plugin_id="navigator")
        ctx.ui.add_tray_action("tray_label", self.open, order=10, plugin_id="navigator")
        # 语言切换后刷新已打开的导航窗口（窗口未打开时静默跳过）
        self.subscribe("i18n.language_changed", self._on_language_changed)
        ctx.logger.info("导航助手插件已激活")

    def deactivate(self) -> None:
        super().deactivate()

    def _on_language_changed(self, _event, _lang=None) -> None:
        windows = self.ctx.windows if self.ctx is not None else None
        if windows is None:
            return
        dialog = windows.get(WordNavigatorDialog.WINDOW_ID)
        if dialog is not None and hasattr(dialog, "retranslateUi"):
            dialog.retranslateUi()

    def open(self) -> None:
        """打开或激活导航助手窗口；失败必须让用户看见原因（不能静默无反应）。"""
        windows = self.ctx.windows
        if windows is None:
            self.ctx.logger.error("窗口服务不可用，无法打开导航助手")
            return
        try:
            opened = windows.open(
                WordNavigatorDialog.WINDOW_ID,
                lambda: WordNavigatorDialog(config=self.ctx.config,
                                            window_manager=windows,
                                            i18n=self.ctx.services.get("i18n"),
                                            plugin_id=self.ctx.id),
                owner="navigator",
                mode=Mode.SINGLETON,
                layer=Layer.PALETTE,
                # 关闭即销毁：对话框关闭时要释放 COM 与缓存连接，不能转隐藏复用
                behavior=CloseBehavior.DESTROY,
                remember_geometry=True,
            )
            if opened is None:
                reason = windows.last_error(WordNavigatorDialog.WINDOW_ID)
                self.ctx.logger.error("打开导航助手失败: %s", reason)
                QMessageBox.warning(None, self.tr("error.open_title"),
                                    self.tr("error.open_failed", error=reason or "-"))
        except Exception:
            self._log_exception("无法打开 Word 导航助手")

    def _log_exception(self, message: str) -> None:
        if self.ctx is not None:
            self.ctx.logger.exception(message)
