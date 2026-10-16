# core/services/hotkey_service.py
"""全局热键服务：插件按需注册，服务统一绑定。

原本 HotkeyManager 直接引用 proofread 的内部函数、并反向调用主窗口属性，
导致外壳与功能双向缠绕；改为「谁的功能谁注册」，热键服务只负责绑定/解绑。
"""
import logging
from typing import Callable

import keyboard as kb

logger = logging.getLogger(__name__)


class HotkeyService:
    def __init__(self, config) -> None:
        self.config = config
        self._entries: dict[str, tuple[str, Callable, str | None]] = {}
        self._handles: list = []

    def register(self, name: str, default_combo: str, callback: Callable, owner: str | None = None) -> None:
        """注册一条热键。name 对应 config["hotkeys"] 里的键；缺省时用 default_combo。"""
        self._entries[name] = (default_combo, callback, owner)

    def unregister_owner(self, owner: str) -> None:
        """注销某个插件注册的全部热键（插件卸载时调用）。"""
        for name in [n for n, entry in self._entries.items() if entry[2] == owner]:
            del self._entries[name]

    def apply(self) -> None:
        """按当前配置重新绑定全部已注册热键（先解绑旧的）。"""
        self.clear()
        configured = self.config.get("hotkeys", {}) or {}
        for name, (default_combo, callback, _owner) in self._entries.items():
            combo = configured.get(name, default_combo)
            try:
                self._handles.append(kb.add_hotkey(combo, callback))
            except Exception:
                logger.exception("热键 [%s] 绑定失败（组合键: %s）", name, combo)

    def clear(self) -> None:
        for handle in self._handles:
            try:
                kb.remove_hotkey(handle)
            except Exception:
                pass
        self._handles.clear()

    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)
