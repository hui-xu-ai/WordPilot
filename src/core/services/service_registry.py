# core/services/service_registry.py
"""内核服务定位器。

插件与外壳一律通过名字向内核索取服务（ctx.get_service("word_bridge")），
不直接 import 服务模块内部实现，从而可以替换实现而不影响调用方。

已注册的内核服务：
    config          ConfigManager        配置（含 Fernet 加密的 API Key）
    ai_client       DeepSeekClient       DeepSeek / OpenAI 兼容客户端
    ignore          IgnoreCacheManager   忽略句缓存
    caption_cache   CaptionCache         题注/标题缓存
    word_bridge     module               Word COM 统一入口
    hotkeys         HotkeyService        全局热键
    ui              UiRegistry 实现      UI 贡献点（由 app 层挂载）
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    def register(self, name: str, instance: Any, *, overwrite: bool = True) -> Any:
        if name in self._services and not overwrite:
            raise KeyError(f"服务已注册: {name}")
        self._services[name] = instance
        return instance

    def get(self, name: str, default: Any = None) -> Any:
        return self._services.get(name, default)

    def require(self, name: str) -> Any:
        try:
            return self._services[name]
        except KeyError:
            raise KeyError(f"未注册的内核服务: {name}") from None

    def has(self, name: str) -> bool:
        return name in self._services

    def unregister(self, name: str) -> None:
        self._services.pop(name, None)

    def names(self) -> tuple[str, ...]:
        return tuple(self._services)

    def __contains__(self, name: str) -> bool:
        return name in self._services

    def __iter__(self):
        return iter(self._services)
