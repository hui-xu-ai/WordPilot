# plugins/reference/pet_anchor.py
"""桌宠锚点：悬浮卡贴在宠物附近而不是挡住正文（用户要求）。

从 plugin.py 抽出，便于单测并控制文件行数。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def pet_anchor_point(window=None) -> tuple[int, int] | None:
    """宠物当前的锚点（屏幕坐标）。

    先问 PetService；它只在宠物窗口上报过几何后才有值，拿不到就**退回宠物窗口几何**
    —— 否则悬浮卡会落回鼠标位置，宠物在屏幕右侧时卡片会跑到很远的地方（用户反馈）。
    """
    try:
        from core.services.pet_service import PetService

        point = PetService.instance().anchor_point()
        if point is not None:
            return point
    except Exception:                          # noqa: BLE001
        pass

    # 兜底 1：PetService 自己记的位置（窗口上报过位置但没算过锚点）
    try:
        from core.services.pet_service import PetService

        service = PetService.instance()
        pos = service.position()
        if pos is not None:
            return pos.x() + int(service.size()) + 8, pos.y()
    except Exception:                          # noqa: BLE001
        pass

    # 兜底 2：宠物窗口几何（调用方传进来，或宿主窗口）
    for candidate in (window, _host_window()):
        try:
            if candidate is not None and candidate.isVisible():
                rect = candidate.frameGeometry()
                return rect.right() + 8, rect.top()
        except Exception:                      # noqa: BLE001
            continue
    return None


def _host_window():
    """宿主登记的宠物窗口（没有就返回 None）。"""
    try:
        from core.services.pet_host import PetHost

        return PetHost.instance().window()
    except Exception:                          # noqa: BLE001
        return None


def pet_preferred() -> bool:
    """是否优先把悬浮卡贴到宠物上（宠物隐藏/未定位时调用方会回退到光标位置）。"""
    return True


def resolve_window(ctx=None):
    """从内核服务里找宠物窗口（找不到返回 None）。"""
    try:
        manager = ctx.get_service("window_manager") if ctx else None
        if manager is None:
            return None
        from core.widgets.pet_window import WINDOW_ID

        return manager.get(WINDOW_ID)
    except Exception:                          # noqa: BLE001
        return None
