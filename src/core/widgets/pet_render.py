# core/widgets/pet_render.py
"""宠物绘制缩放：在 64×64 基准画布上画完，再整体缩放到窗口矩形。

抽成独立模块的原因：`pet_window.py` 已接近文件行数阈值，且这段逻辑与窗口状态无关。
"""
from __future__ import annotations

BASE_SIZE = 64


def paint_scaled(window, painter) -> None:
    """按窗口尺寸把宠物等比绘制出来（layout 坐标始终在基准空间，不会错位）。"""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QPainter, QPixmap

    width, height = window.width(), window.height()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    if (width, height) == (BASE_SIZE, BASE_SIZE):
        window._paint(painter)
        return

    canvas = QPixmap(BASE_SIZE, BASE_SIZE)
    canvas.fill(Qt.transparent)
    inner = QPainter(canvas)
    inner.setRenderHint(QPainter.Antialiasing, True)
    inner.setRenderHint(QPainter.SmoothPixmapTransform, True)
    window._paint_rect = QRect(0, 0, BASE_SIZE, BASE_SIZE)
    try:
        window._paint(inner)
    finally:
        window._paint_rect = None
        inner.end()

    painter.drawPixmap(window.rect(), canvas)
