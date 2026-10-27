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
from core.services.i18n import I18nService
from core.services.proof_cache import get_default_cache
from core.services.service_registry import ServiceRegistry
from core.services.skin_service import SkinService
from core.services.theme_service import ThemeService
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
        self._shutting_down = False

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
        try:
            from core.services import prompt_store

            prompt_store.migrate_legacy()              # 旧 config/prompts → plugins/<插件>/prompts
            prompt_store.repair_bogus_versions()       # 清掉内容为原始 key 的坏版本
            prompt_store.migrate_stale_defaults(config)  # config 里的旧版内置默认 → 用新默认
            prompt_store.seed_from_config(config)      # 已有自定义提示词 → 登记为「默认」版本
        except Exception:                  # noqa: BLE001 - 登记失败不阻断启动
            pass
        self.services.register("config", config)
        self.services.register("i18n", I18nService(config))
        self.services.register("theme", ThemeService(config))
        self.services.register("skins", SkinService(config))
        self.services.register("ai_client", DeepSeekClient(config, i18n=self.services.get("i18n")))
        self.services.register("ignore", IgnoreCacheManager())
        self.services.register("caption_cache", CaptionCache())
        self.services.register("proof_cache", get_default_cache())
        self.services.register("word_bridge", word_bridge)
        self.services.register("hotkeys", HotkeyService(config))
        self._booted = True
        logger.info("内核启动完成，已注册服务: %s", ", ".join(self.services.names()))
        return self

    def attach_ui(self, ui) -> "Kernel":
        """挂载 UI 贡献点实现（由 app 层提供 QtUiRegistry）。

        窗口治理服务依赖 QApplication，故在此注册而非 boot()，
        以保持内核本身的零 Qt 依赖。
        """
        self._ui = ui
        self.services.register("ui", ui, overwrite=True)
        from core.services.window_manager import WindowManager

        self.services.register("window_manager", WindowManager(self.config, self),
                               overwrite=True)
        return self

    def set_language(self, lang: str) -> str:
        """切换界面语言并广播，常驻界面订阅 i18n.language_changed 做重翻译。"""
        i18n = self.services.get("i18n")
        if i18n is None:
            return lang
        current = i18n.set_language(lang)
        self.events.emit("i18n.language_changed", current)
        return current

    def set_theme(self, name: str):
        """切换主题并广播，常驻界面可订阅 theme.changed 做局部刷新。"""
        theme = self.services.get("theme")
        if theme is None:
            return None
        theme.set_theme(name)
        self.events.emit("theme.changed", theme.current)
        return theme

    def set_skin(self, skin_id: str):
        """切换桌宠皮肤并广播，桌宠窗口订阅 skin.changed 后重绘。"""
        skins = self.services.get("skins")
        if skins is None:
            return None
        current = skins.set_skin(skin_id)
        self.events.emit("skin.changed", current)
        return skins

    def reload_ai_client(self):
        """API Key 变更后重建客户端，并广播给所有插件刷新引用。"""
        client = DeepSeekClient(self.config, i18n=self.services.get("i18n"))
        self.services.register("ai_client", client, overwrite=True)
        self.events.emit("ai.client_changed", client)
        return client

    def shutdown(self) -> None:
        """卸载全部插件、释放热键，广播关闭事件。

        幂等：``main_window.quit_app`` 与 ``main.py`` 收尾都会调它（历史上会跑三遍，
        广播三次 kernel.shutdown、unload_all/hotkeys.clear 各跑两遍）。
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        self.events.emit("kernel.shutdown", None)
        self.plugins.unload_all()
        hotkeys = self.services.get("hotkeys")
        if hotkeys is not None:
            hotkeys.clear()
        logger.info("内核已关闭")
