# app/ui_registry.py
"""UI 贡献点的 Qt 实现。

插件调用 ctx.ui.add_tool(...) / ctx.ui.add_tray_action(...)，
由本类统一收集，主窗口在插件加载完成后一次性渲染成菜单与托盘项。

内核与插件只依赖 core.sdk.ui.UiRegistry 接口，不认识 Qt；
这里才是「贡献点」落到具体控件的地方。
"""
from __future__ import annotations

from typing import Any, Callable

from core.sdk.ui import UiRegistry


class QtUiRegistry(UiRegistry):
    """收集插件的界面入口，供主窗口渲染。"""

    def __init__(self, main_window=None) -> None:
        super().__init__()
        self._main_window = main_window
        # (order, label, handler, icon, plugin_id)
        self._tools: list[tuple[int, str, Callable, Any, str | None]] = []
        # (order, label, handler, plugin_id)
        self._tray: list[tuple[int, str, Callable, str | None]] = []

    def main_window(self):
        """供插件获取主窗口（作为对话框的父窗口，可为 None）。"""
        return self._main_window

    def add_tool(self, label: str, handler: Callable, order: int = 100,
                 icon: Any = None, plugin_id: str | None = None) -> None:
        self._tools.append((order, label, handler, icon, plugin_id))
        self._tools.sort(key=lambda item: item[0])

    def add_tray_action(self, label: str, handler: Callable, order: int = 100,
                        plugin_id: str | None = None) -> None:
        self._tray.append((order, label, handler, plugin_id))
        self._tray.sort(key=lambda item: item[0])

    def tools(self) -> list[tuple[str, Callable, Any]]:
        return [(label, handler, icon) for _o, label, handler, icon, _p in self._tools]

    def tray_actions(self) -> list[tuple[str, Callable]]:
        return [(label, handler) for _o, label, handler, _p in self._tray]

    def remove_plugin(self, plugin_id: str) -> None:
        """插件卸载时清理它注册的全部界面入口。"""
        self._tools = [item for item in self._tools if item[4] != plugin_id]
        self._tray = [item for item in self._tray if item[3] != plugin_id]
