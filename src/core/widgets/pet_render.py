# core/widgets/pet_render.py
"""宠物矢量绘制：将64×64坐标空间直接映射到当前尺寸，含状态装饰。

抽成独立模块的原因：`pet_window.py` 已接近文件行数阈值，且这段逻辑与窗口状态无关。
"""
from __future__ import annotations

BASE_SIZE = 64


def paint_scaled(window, painter) -> None:
    """按窗口尺寸把宠物等比绘制出来（layout 坐标始终在基准空间，不会错位）。"""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter

    width, height = window.width(), window.height()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    # 直接缩放矢量绘制，避免先栅格化64px再放大造成文字角标发糊。
    painter.save()
    painter.scale(width / BASE_SIZE, height / BASE_SIZE)
    window._paint_rect = QRect(0, 0, BASE_SIZE, BASE_SIZE)
    try:
        window._paint(painter)
    finally:
        window._paint_rect = None
        painter.restore()


def paint_pet(window, painter) -> None:
    """在固定基准空间绘制状态装饰与可轻跳的角色，窗口几何保持不动。"""
    import math

    from PySide6.QtCore import QRectF

    rect = getattr(window, "_paint_rect", None) or window.rect()
    skins = window._skins
    if skins is None or not skins.current_id:
        window._paint_fallback(painter, rect)
        return
    enhanced = skins.status_overlay_enabled()
    color = window.state_color()
    if enhanced:
        _paint_halo(painter, window._state, color)

    painter.save()
    scale = 1.0 + float(window._anim.get("breathe_scale", 0.045)) * math.sin(
        2 * math.pi * window._breathe)
    painter.translate(rect.center().x(), rect.bottom())
    painter.scale(scale, scale)
    painter.translate(-rect.center().x(), -rect.bottom())
    painter.translate(0, -2.5 * window._attention)
    colors = window._colors()
    window._draw(painter, "body", rect, colors)
    layout = skins.layout()
    blinking = skins.eye_variant(window._state) not in ("sleep", "happy")
    for name in ("eye_left", "eye_right", "mouth"):
        box = layout.get(name)
        if not box:
            continue
        target = QRectF(box["x"], box["y"], box["w"], box["h"])
        if name != "mouth" and blinking and window._blink < 1.0:
            shrink = target.height() * (1 - window._blink)
            target = target.adjusted(0, shrink / 2, 0, -shrink / 2)
        window._draw(painter, name, target, colors)
    painter.restore()
    if enhanced:
        _paint_badge(painter, window._state, color)


def _paint_halo(painter, state, color) -> None:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPen

    if state not in ("ok", "error", "busy"):
        return
    painter.save()
    fill = QColor(color)
    fill.setAlpha(30)
    painter.setBrush(fill)
    painter.setPen(QPen(color, 2.2))
    if state == "error":
        painter.translate(31.5, 31.5)
        painter.rotate(45)
        painter.drawRoundedRect(QRectF(-19.7, -19.7, 39.4, 39.4), 7, 7)
    else:
        painter.drawEllipse(QRectF(3.5, 3.5, 56, 56))
    painter.restore()


def _paint_badge(painter, state, color) -> None:
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QPainterPath, QPen

    if state not in ("ok", "error", "busy", "disabled"):
        return
    painter.save()
    painter.setBrush(color)
    painter.setPen(QPen(QColor("#ffffff"), 1.4))
    if state == "error":
        painter.save()
        painter.translate(53, 53)
        painter.rotate(45)
        painter.drawRoundedRect(QRectF(-7.1, -7.1, 14.2, 14.2), 2.5, 2.5)
        painter.restore()
    else:
        painter.drawEllipse(QRectF(44, 44, 18, 18))
    pen = QPen(QColor("#ffffff"), 2.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    if state == "ok":
        check = QPainterPath(QPointF(48.5, 53))
        check.lineTo(51.5, 56)
        check.lineTo(57.5, 49.5)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(check)
    elif state == "error":
        painter.drawLine(QPointF(53, 48), QPointF(53, 53))
        painter.drawPoint(QPointF(53, 57))
    elif state == "busy":
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(QRectF(48, 48, 10, 10), 30 * 16, 265 * 16)
    else:
        for x in (51, 55):
            painter.drawLine(QPointF(x, 49.5), QPointF(x, 56.5))
    painter.restore()
