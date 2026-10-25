# core/widgets/pet_events.py
"""宠物窗口的几何上报：位置/尺寸变化时通知内核 PetService（供悬浮窗锚定）。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _pet_service():
    try:
        from core.services.pet_service import PetService

        return PetService.instance()
    except Exception:                      # noqa: BLE001
        return None


class PetEventMixin:
    """把 move/resize/show/hide 统一上报给共享服务（渲染方只负责画）。"""

    def _report_geometry(self) -> None:
        service = _pet_service()
        if service is None:
            return
        try:
            service.update_geometry(self.x(), self.y(), self.width())
        except Exception:                  # noqa: BLE001
            logger.debug("上报宠物几何失败", exc_info=True)

    def moveEvent(self, event) -> None:        # noqa: N802 - Qt 命名惯例
        super().moveEvent(event)
        self._report_geometry()

    def resizeEvent(self, event) -> None:      # noqa: N802
        super().resizeEvent(event)
        self._report_geometry()

    def showEvent(self, event) -> None:        # noqa: N802
        super().showEvent(event)
        self._report_geometry()

    def hideEvent(self, event) -> None:        # noqa: N802
        super().hideEvent(event)
        self._report_geometry()
