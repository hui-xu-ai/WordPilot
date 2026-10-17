# core/sdk/context.py
"""插件上下文：插件与内核交互的唯一入口。

插件拿到的只有 ctx 一个对象，因此内核的演进（加服务、换实现）
不会波及插件代码。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from core.kernel import Kernel
    from core.sdk.plugin_base import PluginManifest


class PluginContext:
    def __init__(self, kernel: "Kernel", manifest: "PluginManifest") -> None:
        self.kernel = kernel
        self.manifest = manifest

    # ---- 基本信息 ----
    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.name

    # ---- 内核能力 ----
    @property
    def services(self):
        return self.kernel.services

    @property
    def events(self):
        return self.kernel.events

    @property
    def ui(self):
        """UI 贡献点；无界面环境下为 NullUiRegistry（静默丢弃）。"""
        return self.kernel.ui

    @property
    def config(self):
        return self.kernel.services.get("config")

    @property
    def windows(self):
        """窗口治理服务：打开子窗口一律走它，不要自己 new Dialog。"""
        return self.kernel.services.get("window_manager")

    @property
    def theme(self):
        """主题服务（P2 起可用）；未注册时返回 None，插件需自行判空。"""
        return self.kernel.services.get("theme")

    @property
    def tr(self):
        """插件文案翻译：key 自动带 <plugin_id>. 命名空间。

        服务缺失时返回恒等函数（返回 key 本身），与 NullUiRegistry 一样的降级哲学。
        """
        i18n = self.kernel.services.get("i18n")
        if i18n is None:
            return lambda key, **kwargs: key
        prefix = f"{self.manifest.id}."

        def _tr(key: str, /, **kwargs) -> str:
            return i18n.tr(prefix + key, **kwargs)

        return _tr

    def get_service(self, name: str, default: Any = None) -> Any:
        return self.kernel.services.get(name, default)

    def require_service(self, name: str) -> Any:
        return self.kernel.services.require(name)

    def register_capability(self, name: str, obj: Any) -> None:
        """对外暴露能力，例如 proofread 插件注册 "proofread" 供外壳按钮调用。"""
        self.kernel.capabilities[name] = obj

    # ---- 插件私有资源 ----
    @property
    def data_dir(self) -> "Path":
        """插件私有数据目录（config/plugins/<id>/），按需自动创建。"""
        path = self.kernel.data_dir / self.manifest.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"plugin.{self.manifest.id}")
