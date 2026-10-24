# plugins/reference/overlay_geometry.py
"""悬浮窗定位的纯函数（不依赖 Qt 对象，便于单测）。

规则：默认贴在引用点右下方；右边放不下就翻到左侧，下边放不下就翻到上方，
最后整体夹进工作区（多显示器/任务栏都考虑）。
"""
from __future__ import annotations

GAP = 8


def place_near(anchor_x: int, anchor_y: int, width: int, height: int,
               area: tuple[int, int, int, int], gap: int = GAP) -> tuple[int, int]:
    """返回悬浮窗左上角坐标。

    area = (left, top, right, bottom) 工作区（右/下为包含端）。
    """
    left, top, right, bottom = area

    x = anchor_x + gap
    if x + width > right:                 # 右边放不下 → 翻到引用点左侧
        x = anchor_x - gap - width

    y = anchor_y + gap
    if y + height > bottom:               # 下边放不下 → 翻到引用点上方
        y = anchor_y - gap - height

    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    return x, y
