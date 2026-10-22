# app/switch.py
"""iOS/Fluent 风格拨动开关（Switch）。

QCheckBox 的 QSS 表现力有限（画不出滑动的圆头），这里直接自绘：
- 40x22 的胶囊轨道 + 圆形滑块，checked 时轨道用主题强调色；
- 颜色由调用方按主题令牌注入（accent / border / on_accent），不直接依赖主题服务；
- 接口与 QAbstractButton 一致：setChecked / isChecked / toggled。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton


class Switch(QAbstractButton):
    WIDTH = 40
    HEIGHT = 22

    def __init__(self, parent=None, *, accent: str = "#0078d4",
                 track_off: str = "#c8c8c8", knob: str = "#ffffff") -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self._accent = QColor(accent)
        self._track_off = QColor(track_off)
        self._knob = QColor(knob)

    def set_colors(self, accent: str, track_off: str, knob: str = "#ffffff") -> None:
        """主题切换后更新配色。"""
        self._accent = QColor(accent)
        self._track_off = QColor(track_off)
        self._knob = QColor(knob)
        self.update()

    def paintEvent(self, _event) -> None:      # noqa: N802 - Qt 命名惯例
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.WIDTH, self.HEIGHT
        radius = h / 2
        # 轨道
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._accent if self.isChecked() else self._track_off)
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), radius, radius)
        # 滑块（贴左=关，贴右=开；开状态用主题色的更深一档不可得，统一白圆）
        margin = 3
        diameter = h - margin * 2
        x = (w - diameter - margin) if self.isChecked() else margin
        painter.setBrush(self._knob)
        painter.drawEllipse(QRectF(x + 0.5, margin + 0.5, diameter - 1, diameter - 1))
        painter.end()
