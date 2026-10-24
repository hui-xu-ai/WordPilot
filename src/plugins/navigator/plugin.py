# plugins/navigator/plugin.py
"""Word 导航助手插件。

扫描文档中的标题、图表题注与公式，生成可搜索、可跳转的导航面板。

P10 后续：本插件原先只注册 `add_tool`（打开独立浮窗），与其他插件
（智能纠错/翻译改写/图片工具）都注册成主界面导航页的做法不一致
——用户看到的是"浮在主界面上的另一块窗口"。现在改成标准页面形态：
`ctx.ui.add_page()` 注册内容页，侧边栏点图标直达；托盘项改为切到该页，
不再创建窗口。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from core.sdk.plugin_base import PluginBase
from plugins.navigator.word_navigator_dialog import WordNavigatorPanel

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class NavigatorPlugin(PluginBase):
    """导航助手：主界面内容页（与其它插件一致），页面惰性构造一次。"""

    def __init__(self) -> None:
        super().__init__()
        self._panel: WordNavigatorPanel | None = None

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        ctx.ui.add_page("nav.page", self._build_page, order=12, icon="compass",
                        plugin_id="navigator")
        # 托盘项与插件菜单入口：切到本页（不再另开窗口）
        ctx.ui.add_tray_action("tray_label", self.open, order=10, plugin_id="navigator")
        # 语言切换后刷新已构造的页面（未构造时静默跳过）
        self.subscribe("i18n.language_changed", self._on_language_changed)
        ctx.logger.info("导航助手插件已激活（内容页形态）")

    def deactivate(self) -> None:
        if self._panel is not None:
            try:
                self._panel.shutdown()
            except Exception:
                if self.ctx is not None:
                    self.ctx.logger.exception("导航页面释放失败")
            self._panel = None
        super().deactivate()

    def _build_page(self) -> QWidget:
        """外壳惰性构造内容页时调用（只构造一次，之后由外壳缓存）。"""
        self._panel = WordNavigatorPanel(config=self.ctx.config,
                                        window_manager=self.ctx.windows,
                                        i18n=self.ctx.services.get("i18n"),
                                        plugin_id=self.ctx.id)
        return self._panel

    def _on_language_changed(self, _event, _lang=None) -> None:
        if self._panel is not None and hasattr(self._panel, "retranslateUi"):
            self._panel.retranslateUi()

    def open(self) -> None:
        """把主界面切到本插件的导航页（托盘/插件菜单入口）。

        页面没构造也没关系：`show_page` 走外壳的惰性构造；
        外壳不可用（例如离屏冒烟）时只记日志，不再创建独立窗口。
        """
        ui = getattr(self.ctx, "ui", None) if self.ctx is not None else None
        main_window = ui.main_window() if ui is not None and hasattr(ui, "main_window") else None
        if main_window is None or not hasattr(main_window, "show_page"):
            if self.ctx is not None:
                self.ctx.logger.warning("主窗口不可用，无法打开导航页")
            return
        main_window.show_page(f"plugin:{self.ctx.id}")

    def _log_exception(self, message: str) -> None:
        if self.ctx is not None:
            self.ctx.logger.exception(message)
