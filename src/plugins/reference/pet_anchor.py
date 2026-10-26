# plugins/reference/pet_anchor.py
"""桌宠锚点：悬浮卡贴在宠物附近而不是挡住正文（用户要求）。

从 plugin.py 抽出，便于单测并控制文件行数。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def pet_anchor_point(window=None) -> tuple[int, int] | None:
    """宠物当前的锚点（屏幕坐标）：宠物右缘再往右一点。

    先问 PetService（它只在宠物窗口上报过几何后才有值），拿不到才退回宠物窗口几何。
    注：不再保留"用 PetService 位置兜底"的分支 —— 能走到那里的情形只有 `_visible` 为假
    （用户关了宠物），那时把卡片贴到看不见的宠物旁边是错的；而且旧实现把 `position()` /
    `size()` 当方法调（它们是 property），TypeError 被 `except: pass` 吞掉，是纯死代码。
    """
    try:
        from core.services.pet_service import PetService

        point = PetService.instance().anchor_point()
        if point is not None:
            return point
    except Exception:                          # noqa: BLE001
        pass

    for candidate in (window, _host_window()):
        try:
            if candidate is not None and candidate.isVisible():
                rect = candidate.frameGeometry()
                return rect.right() + 8, rect.top()
        except Exception:                      # noqa: BLE001
            continue
    return None


def pet_screen_rect(window=None) -> tuple[int, int, int, int] | None:
    """宠物窗口矩形 (x, y, 宽, 高)：给悬浮窗做「贴宠物下方」的定位依据。

    优先真实窗口；窗口还没有时退回 PetService 记的位置与尺寸。
    """
    for candidate in (window, _host_window()):
        try:
            if candidate is not None and candidate.isVisible():
                rect = candidate.frameGeometry()
                if rect.width() > 0 and rect.height() > 0:
                    return rect.left(), rect.top(), rect.width(), rect.height()
        except Exception:                      # noqa: BLE001
            continue
    try:
        from core.services.pet_service import PetService

        service = PetService.instance()
        pos = service.position              # 属性，不是方法
        if pos is not None:
            size = int(service.size)
            return pos.x(), pos.y(), size, size
    except Exception:                          # noqa: BLE001
        pass
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
