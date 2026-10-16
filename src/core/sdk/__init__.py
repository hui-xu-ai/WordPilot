# core/sdk/__init__.py
"""插件 SDK：插件开发者唯一需要 import 的协议层。"""
from core.sdk.context import PluginContext
from core.sdk.plugin_base import PluginBase, PluginManifest
from core.sdk.ui import NullUiRegistry, UiRegistry

__all__ = [
    "PluginBase",
    "PluginManifest",
    "PluginContext",
    "UiRegistry",
    "NullUiRegistry",
]
