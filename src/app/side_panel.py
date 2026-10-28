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
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

logger = logging.getLogger(__name__)

MIN_CONTENT_WIDTH = 260   # 抽屉展开时给主内容留的最小宽度

COLLAPSED_WIDTH = 36
DEFAULT_WIDTH = 360


class SidePanelHost(QWidget):
    """右侧抽屉容器：`set_content(widget, title)` 换内容，`set_open(True/False)` 展开/收起。"""

    toggled = Signal(bool)

    def __init__(self, parent=None, width: int = DEFAULT_WIDTH, opened: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("sidePanelHost")
        # QWidget 子类必须显式开启才会绘制 QSS 背景/边框（见 title_bar.py 的说明）
        self.setAttribute(Qt.WA_StyledBackground, True)
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
        self._toggle_tip_key = "展开/收起次要面板"
        self.toggle_btn.setToolTip(self.tr(self._toggle_tip_key))
        self.toggle_btn.clicked.connect(self._on_toggle_clicked)
        root.addWidget(self.toggle_btn)

        divider = QFrame()
        divider.setObjectName("sidePanelDivider")
        divider.setFrameShape(QFrame.VLine)
        root.addWidget(divider)

        self._container = QWidget()
        self._container.setObjectName("sidePanel")
        self._container.setAttribute(Qt.WA_StyledBackground, True)
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(12, 12, 12, 12)
        self._container_layout.setSpacing(10)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        # 只允许竖向：横向滚动条会和内容里的输入框/按钮挤在一起，看着像"第二条长滚动条"
        # 内容比抽屉宽时宁可出横向滚动条，也不要静默裁掉右侧（U05）
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._container_layout.addWidget(self._scroll, 1)
        root.addWidget(self._container, 1)

        self._content: QWidget | None = None
        self._apply_state()
        self.setVisible(False)

    # ---------------- 内容 ----------------
    def retranslateUi(self) -> None:        # noqa: N802 - Qt 命名惯例
        """切语言后刷新自身文案（tooltip）。"""
        self.toggle_btn.setToolTip(self.tr(self._toggle_tip_key))

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
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._apply_state)      # 布局定型后再按真实宽度算一次

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

    def _content_min_width(self) -> int:
        """抽屉内容自身需要的最小宽度（含竖条与边距）。

        以前只看配置宽度，内容比它宽时右侧被直接裁掉（用户截图里的"显示不全"）。
        """
        content = getattr(self, "_content", None)
        if content is None:
            return 0
        try:
            need = content.minimumSizeHint().width()
        except Exception:                      # noqa: BLE001
            return 0
        margins = 0
        layout = self._container_layout
        if layout is not None:
            margins = layout.contentsMargins().left() + layout.contentsMargins().right()
        # 竖向滚动条本身也占宽度：漏掉它会让视口比内容窄十几像素，右侧正好被裁
        try:
            scrollbar = self._scroll.verticalScrollBar().sizeHint().width()
        except Exception:                      # noqa: BLE001
            scrollbar = 16
        return int(need) + margins + int(scrollbar) + COLLAPSED_WIDTH + 2

    def _fit_width(self) -> int:
        """展开宽度：优先用配置宽度，窗口装不下时才收窄（永远给主内容留 MIN_CONTENT_WIDTH）。

        用**顶层窗口**的宽度而不是 parentWidget()：布局未定型时父控件宽度可能是 0 或偏小，
        按它算会把抽屉固定成很小的值，之后拉大窗口也不会恢复（用户看到"显示不全、拉窗口也没用"）。
        """
        desired = max(self._width + COLLAPSED_WIDTH + 1, self._content_min_width())
        window = self.window()
        total = window.width() if window is not None else 0
        if total <= 0:
            return desired
        limit = total - MIN_CONTENT_WIDTH - 8
        if limit <= 0:
            return desired                     # 窗口还没布局好：先按期望值，resize 后再收
        return max(COLLAPSED_WIDTH + 1, min(desired, limit))

    def eventFilter(self, watched, event) -> bool:      # noqa: N802 - Qt 命名惯例
        """顶层窗口变宽/变窄时重算抽屉宽度。"""
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Resize and self._opened and watched is self.window():
            self.setFixedWidth(self._fit_width())
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:                 # noqa: N802 - Qt 命名惯例
        """显示时把事件过滤器挂到顶层窗口上（构造阶段 parent 可能还是 None）。"""
        super().showEvent(event)
        window = self.window()
        if window is not None and window is not self and not getattr(self, "_filter_on", None):
            window.installEventFilter(self)
            self._filter_on = window
        self.setFixedWidth(self._fit_width())

    def _apply_state(self) -> None:
        self._container.setVisible(self._opened)
        self.toggle_btn.setArrowType(Qt.LeftArrow if self._opened else Qt.RightArrow)
        self.toggle_btn.setToolTip(self._toggle_title or self.tr("次要面板"))
        if self._opened:
            # U05：原先固定宽度会让抽屉无视窗口宽度——默认 550px 窗口里抽屉右边跑到 718px，
            # 内容被裁掉且禁了横向滚动。现在按"父容器宽度 - 主内容最小宽度"夹一下。
            self.setFixedWidth(self._fit_width())
        else:
            self.setFixedWidth(COLLAPSED_WIDTH + 1)

    def retranslate(self, title: str) -> None:
        self._toggle_title = title
        self.toggle_btn.setToolTip(title or self.tr("次要面板"))
