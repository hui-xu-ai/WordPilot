# core/kernel.py
"""微内核：只负责「注册内核服务 -> 挂载 UI -> 加载插件 -> 关闭」这条生命周期。

内核本身不含任何业务逻辑，也不依赖 PySide6；
所有具体功能都由 plugins/ 下的插件包提供。
"""
from __future__ import annotations

import logging
from pathlib import Path

import core.services.word_bridge as word_bridge
from core.event_bus import EventBus
from core.plugin_manager import PluginManager
from core.services.api_client import DeepSeekClient
from core.services.caption_cache import CaptionCache
from core.services.config_manager import CONFIG_DIR, ConfigManager
from core.services.hotkey_service import HotkeyService
from core.services.ignore_cache import IgnoreCacheManager
from core.services.service_registry import ServiceRegistry
from core.sdk.ui import NullUiRegistry

logger = logging.getLogger(__name__)


class Kernel:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = Path(root_dir) if root_dir else Path(__file__).resolve().parents[1]
        self.events = EventBus()
        self.services = ServiceRegistry()
        self.plugins = PluginManager(self)
        self.capabilities: dict[str, object] = {}
        self._ui = NullUiRegistry()
        self._booted = False

    # ---------------- 属性 ----------------
    @property
    def ui(self):
        return self._ui

    @property
    def config(self):
        return self.services.get("config")

    @property
    def data_dir(self) -> Path:
        """插件私有数据的根目录：config/plugins/"""
        path = CONFIG_DIR / "plugins"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---------------- 生命周期 ----------------
    def boot(self) -> "Kernel":
        """注册内核级基础服务（不含任何界面依赖）。"""
        if self._booted:
            return self
        config = ConfigManager()
        self.services.register("config", config)
        self.services.register("ai_client", DeepSeekClient(config))
        self.services.register("ignore", IgnoreCacheManager())
        self.services.register("caption_cache", CaptionCache())
        self.services.register("word_bridge", word_bridge)
        self.services.register("hotkeys", HotkeyService(config))
        self._booted = True
        logger.info("内核启动完成，已注册服务: %s", ", ".join(self.services.names()))
        return self

    def attach_ui(self, ui) -> "Kernel":
        """挂载 UI 贡献点实现（由 app 层提供 QtUiRegistry）。"""
        self._ui = ui
        self.services.register("ui", ui, overwrite=True)
        return self

    def reload_ai_client(self):
        """API Key 变更后重建客户端，并广播给所有插件刷新引用。"""
        client = DeepSeekClient(self.config)
        self.services.register("ai_client", client, overwrite=True)
        self.events.emit("ai.client_changed", client)
        return client

    def shutdown(self) -> None:
        """卸载全部插件、释放热键，广播关闭事件。"""
        self.events.emit("kernel.shutdown", None)
        self.plugins.unload_all()
        hotkeys = self.services.get("hotkeys")
        if hotkeys is not None:
            hotkeys.clear()
        logger.info("内核已关闭")
