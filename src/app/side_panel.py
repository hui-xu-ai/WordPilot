# app/side_panel.py
"""内容区右侧的「次要界面」抽屉（见 docs/adr/002-side-panel.md）。

用途：插件把次要控件（参数、监控、清洗等）注册到 `ctx.ui.add_side_panel(...)`，
外壳在这里放一个可折叠容器，默认收起，只留一条 36px 竖条；展开宽度可拖（QSplitter），
展开状态与宽度都落到配置 `ui.side_panel.open` / `ui.side_panel.width`。

设计取舍：
- 不用 `QDockWidget`：它要 `QMainWindow` 停靠体系 + 自带标题栏，与自绘无边框标题栏冲突，
  浮出后还会脱离 `WindowManager` 的层级/阴影治理。
- 折叠态只留竖条而不是隐藏整块：否则用户找不到入口（"点不出来"）。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

logger = logging.getLogger(__name__)

COLLAPSED_WIDTH = 36
DEFAULT_WIDTH = 360


class SidePanelHost(QWidget):
    """右侧抽屉容器：`set_content(widget, title)` 换内容，`set_open(True/False)` 展开/收起。"""

    toggled = Signal(bool)

    def __init__(self, parent=None, width: int = DEFAULT_WIDTH, opened: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("sidePanelHost")
        self._opened = bool(opened)
        self._width = max(220, int(width))
        self._content: QWidget | None = None
        self._toggle_title = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 折叠时只剩这条竖条：一个按钮 + 竖排文字，保证"找得到入口"
        self.toggle_btn = QToolButton()
        self.toggle_btn.setObjectName("sidePanelToggle")
        self.toggle_btn.setAutoRaise(True)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setFixedWidth(COLLAPSED_WIDTH)
        self.toggle_btn.setToolTip(self.tr("展开/收起次要面板"))
        self.toggle_btn.clicked.connect(self._on_toggle_clicked)
        root.addWidget(self.toggle_btn)

        divider = QFrame()
        divider.setObjectName("sidePanelDivider")
        divider.setFrameShape(QFrame.VLine)
        root.addWidget(divider)

        self._container = QWidget()
        self._container.setObjectName("sidePanel")
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(12, 12, 12, 12)
        self._container_layout.setSpacing(10)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        # 只允许竖向：横向滚动条会和内容里的输入框/按钮挤在一起，看着像"第二条长滚动条"
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._container_layout.addWidget(self._scroll, 1)
        root.addWidget(self._container, 1)

        self._content: QWidget | None = None
        self._apply_state()
        self.setVisible(False)

    # ---------------- 内容 ----------------
    def set_content(self, widget: QWidget | None, title: str = "") -> None:
        """换内容；`None` 表示当前页没有次要界面（整个抽屉隐藏）。

        注意：**不销毁**旧控件（外壳按插件缓存它们，切回来要复用）；
        取下前必须先 `hide()`——对仍可见的控件 `setParent(None)` 会让它变成顶层窗口。
        """
        current = self._scroll.takeWidget()
        if current is not None:
            current.hide()
            current.setParent(None)
        self._content = widget
        self._toggle_title = title
        if widget is None:
            self.setVisible(False)
            return
        widget.setParent(None)
        self._scroll.setWidget(widget)
        widget.show()
        self.setVisible(True)
        self._apply_state()

    def has_content(self) -> bool:
        return self._content is not None

    # ---------------- 开合 ----------------
    def is_open(self) -> bool:
        return self._opened and self._content is not None

    def set_open(self, opened: bool) -> None:
        opened = bool(opened) and self._content is not None
        if opened == self._opened:
            self._apply_state()
            return
        self._opened = opened
        self._apply_state()
        self.toggled.emit(self._opened)

    def toggle(self) -> None:
        self.set_open(not self._opened)

    def content_width(self) -> int:
        return self._width

    def set_content_width(self, width: int) -> None:
        self._width = max(220, int(width))
        self._apply_state()

    def _on_toggle_clicked(self) -> None:
        self.toggle()

    def _apply_state(self) -> None:
        self._container.setVisible(self._opened)
        self.toggle_btn.setArrowType(Qt.LeftArrow if self._opened else Qt.RightArrow)
        self.toggle_btn.setToolTip(self._toggle_title or self.tr("次要面板"))
        if self._opened:
            self.setFixedWidth(self._width + COLLAPSED_WIDTH + 1)
        else:
            self.setFixedWidth(COLLAPSED_WIDTH + 1)

    def retranslate(self, title: str) -> None:
        self._toggle_title = title
        self.toggle_btn.setToolTip(title or self.tr("次要面板"))
