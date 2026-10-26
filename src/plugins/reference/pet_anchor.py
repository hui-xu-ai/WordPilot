# plugins/reference/pet_anchor.py
"""桌宠锚点：悬浮卡贴在宠物附近而不是挡住正文（用户要求）。

从 plugin.py 抽出，便于单测并控制文件行数。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def pet_anchor_point() -> tuple[int, int] | None:
    """宠物当前的锚点（屏幕坐标）；宠物没显示/取不到就返回 None。"""
    try:
        from core.services.pet_service import PetService

        return PetService.instance().anchor_point()
    except Exception:                          # noqa: BLE001
        return None


def pet_preferred() -> bool:
    """是否优先把悬浮卡贴到宠物上（宠物隐藏/未定位时调用方会回退到光标位置）。"""
    return True
