# core/services/pet_host.py
"""桌宠**宿主**（内核）：负责创建、显示、夹取位置，与任何插件无关。

之前宠物由 `proofread` 插件创建且失败静默，导致"插件一停/一出错，宠物整个消失"。
现在窗口类住在 `core/widgets/pet_window.py`，宿主在这里统一管理：

- `ensure_shown(**deps)`：没创建就创建，然后把位置夹回可见屏幕再 show + raise；
- 创建异常**写 logger.exception 并向上返回 None**（不再静默）；
- `window()` 供插件读取（例如气泡定位），`hide()/close()` 供外观页开关使用。
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication

from core.services.pet_service import MAX_SIZE, MIN_SIZE, PetService

logger = logging.getLogger(__name__)


class PetHost:
    """全进程一个实例：拥有唯一的桌宠窗口。"""

    _instance: "PetHost | None" = None

    def __init__(self) -> None:
        self._window = None

    @classmethod
    def instance(cls) -> "PetHost":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # ---------------- 创建/显示 ----------------
    def window(self):
        return self._window

    def ensure_shown(self, **deps):
        """没有则创建；创建后把位置夹回屏幕内并显示。返回窗口或 None。"""
        service = PetService.instance()
        if self._window is None:
            try:
                from core.widgets.pet_window import PetWindow

                self._window = PetWindow(**deps)
            except Exception:                  # noqa: BLE001 - 必须留下可诊断的日志
                logger.exception("桌宠创建失败（宠物将不可见）")
                return None

        try:
            self._apply_size(service.size)
            self._restore_position(service)
            self._clamp_into_screen()
            if service.visible:
                self._window.show()
                self._window.raise_()
            else:
                self._window.hide()
        except Exception:                      # noqa: BLE001
            logger.exception("桌宠显示失败")
            return self._window
        return self._window

    def apply_visibility(self, visible: bool) -> None:
        if self._window is None:
            return
        if visible:
            self._clamp_into_screen()
            self._window.show()
            self._window.raise_()
        else:
            self._window.hide()

    def apply_size(self, size: int) -> None:
        self._apply_size(size)

    def apply_skin(self, skin_id: str) -> None:
        """皮肤来自 skin 服务；拿到就切，拿不到交给渲染方的默认逻辑。"""
        if self._window is None:
            return
        switch = getattr(self._window, "_switch_skin", None)
        if callable(switch):
            try:
                switch(skin_id)
            except Exception:                  # noqa: BLE001
                logger.debug("切换宠物皮肤失败：%s", skin_id, exc_info=True)

    # ---------------- 内部 ----------------
    def _apply_size(self, size: int) -> None:
        if self._window is None:
            return
        size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        resize = getattr(self._window, "set_pet_size", None)
        if callable(resize):
            resize(size)

    def _restore_position(self, service) -> None:
        """优先用内核记住的位置；没有就用窗口当前坐标。"""
        if self._window is None:
            return
        saved = service.saved_position()
        if saved is not None:
            self._window.move(int(saved[0]), int(saved[1]))

    def _clamp_into_screen(self) -> None:
        """把宠物位置夹回**可见区域**（历史位置可能在屏幕外）。"""
        if self._window is None:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width = max(1, self._window.width())
        height = max(1, self._window.height())
        x = min(max(self._window.x(), area.left()), max(area.left(), area.right() - width))
        y = min(max(self._window.y(), area.top()), max(area.top(), area.bottom() - height))
        if (x, y) != (self._window.x(), self._window.y()):
            logger.info("桌宠位置越界，已夹回可见区域：(%s,%s) → (%s,%s)",
                        self._window.x(), self._window.y(), x, y)
            self._window.move(x, y)
