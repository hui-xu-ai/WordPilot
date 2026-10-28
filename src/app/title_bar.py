# app/title_bar.py
"""自绘标题栏（无边框窗口用，Fluent 风格）。

布局：[应用图标]  标题  ─────  [最小化] [最大化/还原] [关闭]

- 整条栏可拖动窗口，双击切换最大化/还原；
- 按钮图标走 icon_factory，由调用方按主题令牌染色后注入；
- 关闭按钮悬停为红色（样式见 base.qss #titleBarClose）；
- 最小化/最大化/关闭只是发信号，具体行为由主窗口决定
  （关闭在主窗口语义里通常是「隐藏到托盘」，由 WindowManager 裁决）。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

TITLE_BAR_HEIGHT = 36
BUTTON_ICON_SIZE = 14


def restored_position(point_x: int, point_y: int, title_width: int,
                      normal_width: int, title_height: int) -> tuple[int, int]:
    """最大化时拖标题栏：算出还原后窗口左上角的屏幕坐标。

    规则与 Windows 原生一致：鼠标在**标题栏上的水平比例**保持不变，
    窗口因此"跟着鼠标走"，而不是跳回原来的位置。
    纯函数，便于单测（真实拖拽需要桌面事件，无法离屏验证）。
    """
    width = max(1, title_width)
    ratio = min(max(point_x / width, 0.0), 1.0)
    x = int(point_x - normal_width * ratio)
    y = max(0, int(point_y - title_height // 2))
    return x, y


class TitleBar(QWidget):
    minimizeRequested = Signal()
    maximizeToggled = Signal()
    closeRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        # QWidget 子类默认**不绘制**样式表里的 background-color / border（Qt 只为"纯 QWidget
        # 实例"自动开启）。不开这个属性，`QWidget#titleBar` 的配色在真机上完全不生效
        # ——这正是"标题栏与侧边栏分不清"的根因（实测真机像素为纯白）。
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._bg_color: str | None = None
        self._border_color: str | None = None
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self._drag_pos = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(0)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(18, 18)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel()
        self.title_label.setObjectName("titleBarTitle")
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.min_btn = self._make_button("titleBarBtn")
        self.min_btn.clicked.connect(self.minimizeRequested)
        self.max_btn = self._make_button("titleBarBtn")
        self.max_btn.clicked.connect(self.maximizeToggled)
        self.close_btn = self._make_button("titleBarClose")
        self.close_btn.clicked.connect(self.closeRequested)
        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

    # ---------------- 外观 ----------------
    def set_colors(self, background: str | None, border: str | None) -> None:
        """由主窗口按主题令牌注入分区色：自绘背景 + 底边线。

        QSS 的 `QWidget#titleBar` 规则保留（同一份令牌），但**不依赖它**：
        QWidget 子类在真机上可能不绘制样式表背景，这里自己画一份，确保一定看得见。
        """
        self._bg_color = background
        self._border_color = border
        self.update()

    def paintEvent(self, event):        # noqa: N802 - Qt 命名惯例
        if self._bg_color:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(self._bg_color))
            if self._border_color:
                painter.setPen(QColor(self._border_color))
                painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        super().paintEvent(event)

    def _make_button(self, name: str) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName(name)
        btn.setIconSize(QSize(BUTTON_ICON_SIZE, BUTTON_ICON_SIZE))
        btn.setCursor(Qt.ArrowCursor)
        btn.setFixedHeight(TITLE_BAR_HEIGHT)
        return btn

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_icon(self, pixmap: QPixmap) -> None:
        if not pixmap.isNull():
            self.icon_label.setPixmap(pixmap.scaled(
                18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def set_button_icons(self, minimize: QIcon, maximize: QIcon, close: QIcon) -> None:
        self.min_btn.setIcon(minimize)
        self.close_btn.setIcon(close)
        self._maximize_icon = maximize

    def set_maximized_icon(self, icon: QIcon, maximized: bool) -> None:
        """最大化时换「还原」图标。"""
        self.max_btn.setIcon(icon)

    def retranslateUi(self) -> None:        # noqa: N802 - Qt 命名惯例
        """切语言后刷新按钮 tooltip（key 由 set_tooltip_keys 提供）。"""
        keys = getattr(self, "_tooltip_keys", None)
        if keys:
            self.set_tooltips(self.tr(keys[0]), self.tr(keys[1]), self.tr(keys[2]))

    def set_tooltip_keys(self, minimize: str, maximize: str, close: str) -> None:
        """登记 tooltip 的语言包 key（供 retranslateUi 使用）。"""
        self._tooltip_keys = (minimize, maximize, close)

    def set_tooltips(self, minimize: str, maximize: str, close: str) -> None:
        self.min_btn.setToolTip(minimize)
        self.max_btn.setToolTip(maximize)
        self.close_btn.setToolTip(close)

    # ---------------- 拖动 / 双击 ----------------
    def mousePressEvent(self, event: QMouseEvent) -> None:      # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:       # noqa: N802
        if self._drag_pos is None or not event.buttons() & Qt.LeftButton:
            return
        window = self.window()
        point = event.globalPosition().toPoint()
        if window.isMaximized():
            # 最大化状态下拖标题栏 = 还原窗口并"接住"鼠标（Windows 原生行为）。
            # 历史实现直接 return ⇒ 最大化后拖不动窗口。
            normal = window.normalGeometry()
            x, y = restored_position(point.x(), point.y(), self.width(),
                                     normal.width(), self.height())
            window.showNormal()
            window.move(x, y)
            self._drag_pos = point
            event.accept()
            return
        window.move(window.pos() + point - self._drag_pos)
        self._drag_pos = point
        event.accept()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:   # noqa: N802
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:    # noqa: N802
        if event.button() == Qt.LeftButton:
            self.maximizeToggled.emit()
            event.accept()
