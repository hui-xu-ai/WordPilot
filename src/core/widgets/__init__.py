# core/widgets/__init__.py
"""内核级通用 Qt 控件库：外壳（app/）与插件（plugins/）都可以引用。

放在 core 层的原因：插件按分层约定不允许 import app 层模块，
而 Switch / MonitorPanel 这类控件同时被外壳页面与插件页面使用。
"""
from core.widgets.switch import Switch
from core.widgets.monitor_panel import MonitorPanel

__all__ = ["Switch", "MonitorPanel"]
