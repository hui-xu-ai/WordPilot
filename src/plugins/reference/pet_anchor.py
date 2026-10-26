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
    point: tuple[int, int] | None = None
    source = "none"
    note = ""
    try:
        from core.services.pet_service import PetService

        point = PetService.instance().anchor_point()
        if point is not None:
            source = "service"
    except Exception as exc:                   # noqa: BLE001
        note = f"service 异常={exc!r}"

    # 兜底 1：PetService 自己记的位置（窗口上报过位置但没算过锚点）
    if point is None:
        try:
            from core.services.pet_service import PetService

            service = PetService.instance()
            pos = service.position()
            if pos is not None:
                point = (pos.x() + int(service.size()) + 8, pos.y())
                source = "fallback-pos"
        except Exception as exc:                # noqa: BLE001
            note = f"兜底1 异常={exc!r}"

    # 兜底 2：宠物窗口几何（调用方传进来，或宿主窗口）
    if point is None:
        for candidate in (window, _host_window()):
            try:
                if candidate is not None and candidate.isVisible():
                    rect = candidate.frameGeometry()
                    point = (rect.right() + 8, rect.top())
                    source = "fallback-window"
                    break
            except Exception:                  # noqa: BLE001
                continue

    _log_anchor(source, point, window, note)
    return point


def _log_anchor(source: str, point, window, note: str = "") -> None:
    """BUG-1 取证：锚点决策链（与 plugin.py「显示引用卡片」那行按时间对照）。

    只读日志：看清"卡片贴到哪"这个值由谁给出、宠物窗口实际在哪、兜底为什么没生效。
    """
    try:
        from core.services.pet_service import PetService

        service = PetService.instance()
        visible = service.visible
        pos = service.position            # 属性，不是方法（同 176 行的 anchor_point）
        pos_text = f"({pos.x()},{pos.y()})" if pos is not None else "None"
        size = service.size
    except Exception as exc:                   # noqa: BLE001
        visible = pos_text = size = f"?({exc!r})"
    window_text = "None"
    for candidate in (window, _host_window()):
        if candidate is None:
            continue
        try:
            rect = candidate.frameGeometry()
            window_text = (f"可见={candidate.isVisible()} "
                           f"矩形=({rect.left()},{rect.top()},{rect.width()}x{rect.height()})")
        except Exception as exc:               # noqa: BLE001
            window_text = f"?({exc!r})"
        break
    logger.info("BUG-1 锚点决策：来源=%s 锚点=%s | service.visible=%s pos=%s size=%s | 宠物窗口=%s%s",
                source, point, visible, pos_text, size, window_text,
                f" | {note}" if note else "")


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
