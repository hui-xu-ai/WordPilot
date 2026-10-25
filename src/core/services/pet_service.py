# core/services/pet_service.py
"""桌宠的**共享状态服务**：几何位置、尺寸、皮肤、可见性 + 悬浮窗锚点。

为什么要它：宠物原来是 `proofread` 插件的内部对象，别的插件想用它做定位锚点 /
快速入口，只能依赖 proofread（关掉校对宠物就没了）。现在宠物状态住在内核里：

- 渲染方（现在是 proofread，之后可以是独立的 `pet` 插件）只管画，
  画完把几何写进 `update_geometry()`；
- 任何插件都能读 `anchor_point()` 把自己的窗口贴到宠物旁边；
- 皮肤/尺寸/可见性存在配置里（`pet.*`），与任何插件无关。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QPoint, Signal

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "pet"
DEFAULT_SIZE = 96
MIN_SIZE = 48
MAX_SIZE = 200


class PetService(QObject):
    """宠物几何与外观的单一真相（全进程一个实例）。"""

    geometryChanged = Signal()
    visibilityChanged = Signal(bool)

    _instance: "PetService | None" = None

    def __init__(self, config=None) -> None:
        super().__init__()
        self._config = config
        self._pos: QPoint | None = None
        self._size = DEFAULT_SIZE
        self._skin = "classic"
        self._visible = False
        # 插件快捷项：{plugin_id: [{label, callback, order}]}，只显示已启用插件的
        self._actions: dict[str, list[dict]] = {}
        self._active_plugins: set[str] = set()
        self._load()

    # ---------------- 单例 ----------------
    @classmethod
    def instance(cls, config=None) -> "PetService":
        if cls._instance is None:
            cls._instance = cls(config)
        elif config is not None:
            cls._instance._config = config
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # ---------------- 配置 ----------------
    def _get(self, key: str, default):
        if self._config is None:
            return default
        try:
            return self._config.get(f"{CONFIG_PREFIX}.{key}", default)
        except Exception:                      # noqa: BLE001 - 配置层不可用就用默认值
            return default

    def _set(self, key: str, value) -> None:
        if self._config is None:
            return
        try:
            self._config.set(f"{CONFIG_PREFIX}.{key}", value)
        except Exception:                      # noqa: BLE001
            logger.debug("保存宠物配置失败：%s", key, exc_info=True)

    def _load(self) -> None:
        self._size = int(self._get("size", DEFAULT_SIZE) or DEFAULT_SIZE)
        self._skin = str(self._get("skin", "classic") or "classic")
        # 历史 bug 曾把"窗口隐藏"当成用户选择写进配置，导致宠物永远不出现。
        # 只有当用户显式切换过（hidden_by_user=True）才尊重 pet.visible。
        self._visible = (bool(self._get("visible", True))
                         if self._get("hidden_by_user", False) else True)

    # ---------------- 属性 ----------------
    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def size(self) -> int:
        return self._size

    @property
    def skin(self) -> str:
        return self._skin

    @property
    def position(self) -> QPoint | None:
        return self._pos

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._visible:
            return
        self._visible = visible
        self._set("visible", visible)
        self._set("hidden_by_user", True)      # 记录"这是用户的选择"，之后才尊重
        self.visibilityChanged.emit(visible)

    def set_size(self, size: int) -> None:
        size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        if size == self._size:
            return
        self._size = size
        self._set("size", size)
        self.geometryChanged.emit()

    def set_skin(self, skin: str) -> None:
        skin = (skin or "classic").strip() or "classic"
        if skin == self._skin:
            return
        self._skin = skin
        self._set("skin", skin)
        self.geometryChanged.emit()

    # ---------------- 插件快捷入口 ----------------
    def register_action(self, plugin_id: str, label: str, callback, order: int = 100) -> None:
        """插件注册宠物右键快捷项；插件停用时自动不显示（无需手动注销）。"""
        self._actions.setdefault(plugin_id, []).append(
            {"label": label, "callback": callback, "order": order})

    def unregister_plugin(self, plugin_id: str) -> None:
        self._actions.pop(plugin_id, None)
        self._active_plugins.discard(plugin_id)

    def set_plugin_active(self, plugin_id: str, active: bool) -> None:
        if active:
            self._active_plugins.add(plugin_id)
        else:
            self._active_plugins.discard(plugin_id)

    def actions(self) -> list[tuple[str, object]]:
        """当前**已启用插件**注册的快捷项（按 order、标签排序）。"""
        items: list[tuple[int, str, object]] = []
        for plugin_id, entries in self._actions.items():
            if plugin_id not in self._active_plugins:
                continue
            for entry in entries:
                items.append((int(entry["order"]), str(entry["label"]), entry["callback"]))
        items.sort(key=lambda item: (item[0], item[1]))
        return [(label, callback) for _order, label, callback in items]

    # ---------------- 几何 ----------------
    def update_geometry(self, x: int, y: int, size: int | None = None) -> None:
        """渲染方（宠物窗口）移动/缩放后调用：更新锚点基准。"""
        self._pos = QPoint(int(x), int(y))
        if size is not None:
            self._size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        self.geometryChanged.emit()

    def anchor_point(self) -> tuple[int, int] | None:
        """悬浮窗贴靠点：宠物**右上角**再往右一点；宠物不可用/不可见时返回 None。"""
        if not self._visible or self._pos is None:
            return None
        return self._pos.x() + self._size + 8, self._pos.y()
