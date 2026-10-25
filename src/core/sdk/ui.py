# core/sdk/ui.py
"""UI 贡献点接口。

内核与插件只依赖本模块；真正的 Qt 实现由 app.ui_registry.QtUiRegistry 提供。
无界面环境（单元测试 / 打包前的冒烟检查）下使用 NullUiRegistry，
所有 UI 贡献会被静默丢弃，功能逻辑照常运行。
"""
from __future__ import annotations

from typing import Any, Callable


class UiRegistry:
    """UI 贡献点收集器接口。"""

    def __init__(self) -> None:
        self._status_handler: Callable | None = None

    # ---- 状态栏 ----
    def set_status_handler(self, handler: Callable) -> None:
        """由外壳注入状态栏写入函数（签名: handler(message, timeout)）。"""
        self._status_handler = handler

    def status(self, message: str, timeout: int = 3000) -> None:
        if self._status_handler:
            self._status_handler(message, timeout)

    def main_window(self):
        """主窗口句柄（供插件把主窗口当对话框父级）；无界面环境下返回 None。"""
        return None

    # ---- 贡献点（子类实现）----
    def add_tool(self, label: str, handler: Callable, order: int = 100,
                 icon: Any = None, plugin_id: str | None = None) -> None:
        raise NotImplementedError

    def add_tray_action(self, label: str, handler: Callable, order: int = 100,
                        plugin_id: str | None = None) -> None:
        raise NotImplementedError

    def add_page(self, label: str, factory: Callable, order: int = 100,
                 icon: Any = None, plugin_id: str | None = None) -> None:
        """注册一个主界面「左侧导航 + 内容页」贡献点。

        factory() -> QWidget，由外壳在首次切到该页时惰性创建，
        label 传 i18n key（渲染时现场查词）。
        """
        raise NotImplementedError

    def add_side_panel(self, label: str, factory: Callable, width: int = 360,
                       plugin_id: str | None = None) -> None:
        """注册该插件页的「次要界面」：显示在内容区右侧的可折叠抽屉里。

        factory() -> QWidget（惰性创建，切到本插件页时构建）；
        抽屉展开状态与宽度由外壳记忆（配置 `ui.side_panel.open` / `ui.side_panel.width`）。
        见 `docs/adr/002-side-panel.md`。
        """
        raise NotImplementedError

    def remove_plugin(self, plugin_id: str) -> None:
        raise NotImplementedError

    # ---- 查询（供桌宠菜单等消费方枚举入口）----
    def tools(self) -> list:
        """[(已翻译名称, 回调, 图标, plugin_id)]，按 order 升序。"""
        return []

    def pages(self) -> list:
        """[(已翻译名称, 页面工厂, 图标, plugin_id)]，按 order 升序。"""
        return []

    def side_panels(self) -> dict:
        """{plugin_id: (已翻译标题, 工厂, 宽度)}，供外壳按当前页取次要面板。"""
        return {}

    def tray_actions(self) -> list:
        """[(已翻译名称, 回调)]，按 order 升序。"""
        return []


    # ---------------- 桌宠右键快捷项 ----------------
    def add_pet_action(self, plugin_id: str, label: str, handler, order: int = 100) -> None:
        """插件注册宠物右键快捷项；插件停用/卸载后自动隐藏（内核宠物服务统一管理）。"""
        try:
            from core.services.pet_service import PetService

            PetService.instance().register_action(plugin_id, label, handler, order=order)
        except Exception:                      # noqa: BLE001 - 宠物不可用不该影响插件
            pass

class NullUiRegistry(UiRegistry):
    """无界面环境的空实现。"""

    def add_tool(self, *args, **kwargs) -> None:
        return None

    def add_tray_action(self, *args, **kwargs) -> None:
        return None

    def add_page(self, *args, **kwargs) -> None:
        return None

    def add_side_panel(self, *args, **kwargs) -> None:
        return None

    def remove_plugin(self, *args, **kwargs) -> None:
        return None
