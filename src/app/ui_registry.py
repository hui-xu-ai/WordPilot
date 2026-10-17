# app/ui_registry.py
"""UI 贡献点的 Qt 实现。

插件调用 ctx.ui.add_tool(...) / ctx.ui.add_tray_action(...) 时传入的是
「i18n key」而不是写死的文案，因此切换语言后主窗口只需重建菜单即可完成重翻译。

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
        self._i18n = None
        # (order, label_key, handler, icon, plugin_id)
        self._tools: list[tuple[int, str, Callable, Any, str | None]] = []
        # (order, label_key, handler, plugin_id)
        self._tray: list[tuple[int, str, Callable, str | None]] = []
        # (order, label_key, factory, icon, plugin_id) —— 主界面「导航+内容页」贡献点
        self._pages: list[tuple[int, str, Callable, Any, str | None]] = []

    def bind_i18n(self, i18n) -> None:
        """由主窗口注入 i18n 服务，供渲染时现场查词。"""
        self._i18n = i18n

    def main_window(self):
        """供插件获取主窗口（作为对话框的父窗口，可为 None）。"""
        return self._main_window

    def _text(self, label_key: str, plugin_id: str | None) -> str:
        """带插件命名空间的查词；无 i18n 服务时原样返回 key。"""
        if self._i18n is None:
            return label_key
        if plugin_id and not label_key.startswith(f"{plugin_id}."):
            label_key = f"{plugin_id}.{label_key}"
        return self._i18n.tr(label_key)

    def add_tool(self, label: str, handler: Callable, order: int = 100,
                 icon: Any = None, plugin_id: str | None = None) -> None:
        self._tools.append((order, label, handler, icon, plugin_id))
        self._tools.sort(key=lambda item: item[0])

    def add_tray_action(self, label: str, handler: Callable, order: int = 100,
                        plugin_id: str | None = None) -> None:
        self._tray.append((order, label, handler, plugin_id))
        self._tray.sort(key=lambda item: item[0])

    def tools(self) -> list[tuple[str, Callable, Any]]:
        return [(self._text(label, pid), handler, icon)
                for _o, label, handler, icon, pid in self._tools]

    def add_page(self, label: str, factory: Callable, order: int = 100,
                 icon: Any = None, plugin_id: str | None = None) -> None:
        self._pages.append((order, label, factory, icon, plugin_id))
        self._pages.sort(key=lambda item: item[0])

    def tray_actions(self) -> list[tuple[str, Callable]]:
        return [(self._text(label, pid), handler)
                for _o, label, handler, pid in self._tray]

    def pages(self) -> list[tuple[str, Callable, Any]]:
        return [(self._text(label, pid), factory, icon)
                for _o, label, factory, icon, pid in self._pages]

    def remove_plugin(self, plugin_id: str) -> None:
        """插件卸载时清理它注册的全部界面入口。"""
        self._tools = [item for item in self._tools if item[4] != plugin_id]
        self._tray = [item for item in self._tray if item[3] != plugin_id]
        self._pages = [item for item in self._pages if item[4] != plugin_id]
