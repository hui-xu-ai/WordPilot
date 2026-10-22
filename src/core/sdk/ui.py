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

    def remove_plugin(self, plugin_id: str) -> None:
        raise NotImplementedError

    # ---- 查询（供桌宠菜单等消费方枚举入口）----
    def tools(self) -> list:
        """[(已翻译名称, 回调, 图标, plugin_id)]，按 order 升序。"""
        return []

    def pages(self) -> list:
        """[(已翻译名称, 页面工厂, 图标, plugin_id)]，按 order 升序。"""
        return []

    def tray_actions(self) -> list:
        """[(已翻译名称, 回调)]，按 order 升序。"""
        return []


class NullUiRegistry(UiRegistry):
    """无界面环境的空实现。"""

    def add_tool(self, *args, **kwargs) -> None:
        return None

    def add_tray_action(self, *args, **kwargs) -> None:
        return None

    def add_page(self, *args, **kwargs) -> None:
        return None

    def remove_plugin(self, *args, **kwargs) -> None:
        return None
