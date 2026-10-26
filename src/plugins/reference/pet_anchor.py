# plugins/reference/pet_anchor.py
"""桌宠锚点：悬浮卡贴在宠物附近而不是挡住正文（用户要求）。

从 plugin.py 抽出，便于单测并控制文件行数。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def pet_anchor_point() -> tuple[int, int] | None:
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

    try:
        from core.services.pet_host import PetHost

        window = PetHost.instance().window()
        if window is not None and window.isVisible():
            rect = window.frameGeometry()
            return rect.right() + 8, rect.top()
    except Exception:                          # noqa: BLE001 - 没有宠物就算了
        return None
    return None


def pet_preferred() -> bool:
    """是否优先把悬浮卡贴到宠物上（宠物隐藏/未定位时调用方会回退到光标位置）。"""
    return True
