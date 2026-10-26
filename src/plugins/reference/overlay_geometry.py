# plugins/reference/overlay_geometry.py
"""悬浮窗定位的纯函数（不依赖 Qt 对象，便于单测）。

两套规则：
- **贴宠物**（给了 `pet_rect`）：卡片**底部中间**落在宠物头像**正上方**，位置完全由宠物决定
  —— 用户要求"位置统一、可预期"，所以不翻边、不夹进屏幕（屏幕外由用户自己挪宠物解决）。
- **贴光标**（没有宠物信息）：默认落引用点右下方，右边放不下翻左、下边放不下翻上，最后夹进工作区。
"""
from __future__ import annotations

GAP = 8


def place_near(anchor_x: int, anchor_y: int, width: int, height: int,
               area: tuple[int, int, int, int], gap: int = GAP,
               pet_rect: tuple[int, int, int, int] | None = None) -> tuple[int, int]:
    """返回悬浮窗左上角坐标。

    area = (left, top, right, bottom) 工作区（右/下为包含端）。
    pet_rect = 宠物矩形 (x, y, w, h)：给定时忽略 anchor，把卡片底边贴在宠物上方 gap 处。
    """
    left, top, right, bottom = area

    if pet_rect is not None:
        pet_x, pet_y, pet_w, _pet_h = pet_rect
        return pet_x + pet_w // 2 - width // 2, pet_y - gap - height

    x = anchor_x + gap
    y = anchor_y + gap
    if x + width > right:                 # 右边放不下 → 翻到引用点左侧
        x = anchor_x - gap - width
    if y + height > bottom:               # 下边放不下 → 翻到引用点上方
        y = anchor_y - gap - height

    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    return x, y
