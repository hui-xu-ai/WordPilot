# app/nav_rail.py
"""可折叠图标导航轨（NavRail）。

现代桌面软件（VS Code / Fluent NavigationView）的侧边栏形态：

- 窄态（默认）：52px 宽，纯图标按钮，悬浮 tooltip 显示名称；
- 展开态：点击顶部汉堡键，变为 176px，图标 + 文字；
- 选中页：左侧 3px 强调色竖条 + 浅底色（样式在 base.qss 的 #navButton 选择器）；
- 底部槽位固定「设置」等全局入口，与功能页分组分开。

条目分两类：
- page   可勾选按钮，点击切换 QStackedWidget 内容页；
- action 普通按钮，点击触发回调（例如打开一个受管窗口）。

图标颜色由调用方（MainWindow）按主题令牌解析后经 icon_factory 注入，
本组件不直接读主题服务，保持可独立测试。
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame,
                               QToolButton, QVBoxLayout, QWidget)

COLLAPSED_WIDTH = 52
EXPANDED_WIDTH = 180
ICON_SIZE = 20


class NavRail(QWidget):
    pageRequested = Signal(str)          # page 型条目被点击
    collapsedChanged = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__()
        self.setObjectName("navRail")
        self._collapsed = True
        self._pages: dict[str, QToolButton] = {}
        self._current_page: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignTop)

        # 顶部：汉堡折叠键（与条目同宽同样式，点击展开为图标+文字）
        self.toggle_btn = QToolButton()
        self.toggle_btn.setObjectName("navAction")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.toggle_btn.setAutoRaise(True)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle_btn)

        # 中部：页面与动作条目（由 set_entries 填充）
        self._items_layout = QVBoxLayout()
        self._items_layout.setSpacing(2)
        self._items_layout.setContentsMargins(0, 6, 0, 0)
        layout.addLayout(self._items_layout)

        layout.addStretch(1)

        # 底部槽位：设置等全局入口
        self._bottom_layout = QVBoxLayout()
        self._bottom_layout.setSpacing(2)
        layout.addLayout(self._bottom_layout)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._apply_width()

    # ---------------- 条目 ----------------
    def set_entries(self,
                    pages: list[dict[str, Any]],
                    actions: list[dict[str, Any]],
                    bottom: list[dict[str, Any]]) -> None:
        """整体重建条目。

        每个条目 dict 字段：
            id      唯一标识（page 型用于切页）
            text    显示文本（展开态）与 tooltip
            icon    QIcon（已由调用方按主题染色）
            handler action 型点击回调
        """
        for lay in (self._items_layout, self._bottom_layout):
            while lay.count():
                item = lay.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    if isinstance(widget, QAbstractButton):
                        self._group.removeButton(widget)
                    # takeAt 只摘布局不摘父子关系；先 setParent(None) 让它立刻
                    # 脱离绘制树，再 deleteLater——否则重建时会留下全尺寸幽灵按钮
                    widget.setParent(None)
                    widget.deleteLater()
        self._pages.clear()

        for entry in pages:
            self._items_layout.addWidget(self._make_button(entry, page=True))
        if pages and actions:
            divider = QFrame()
            divider.setObjectName("navDivider")
            divider.setFrameShape(QFrame.HLine)
            divider.setFixedHeight(9)
            self._items_layout.addWidget(divider)
        for entry in actions:
            self._items_layout.addWidget(self._make_button(entry, page=False))
        for entry in bottom:
            self._bottom_layout.addWidget(self._make_button(entry, page=False))

        self._sync_button_mode()
        if self._current_page in self._pages:
            self._pages[self._current_page].setChecked(True)

    def _make_button(self, entry: dict[str, Any], page: bool) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("navButton" if page else "navAction")
        btn.setAutoRaise(True)
        btn.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        btn.setIcon(entry.get("icon", QIcon()))
        btn.setText(entry.get("text", ""))
        btn.setToolTip(entry.get("text", ""))
        btn.setCursor(Qt.PointingHandCursor)
        if page:
            btn.setCheckable(True)
            self._group.addButton(btn)
            page_id = entry["id"]
            self._pages[page_id] = btn
            btn.clicked.connect(lambda _c=False, pid=page_id: self._on_page_clicked(pid))
        else:
            handler: Callable | None = entry.get("handler")
            if handler is not None:
                btn.clicked.connect(lambda _c=False, h=handler: h())
        return btn

    # ---------------- 状态 ----------------
    def set_collapsed(self, collapsed: bool) -> None:
        self.toggle_btn.blockSignals(True)
        self.toggle_btn.setChecked(not collapsed)
        self.toggle_btn.blockSignals(False)
        self._collapsed = bool(collapsed)
        self._apply_width()
        self._sync_button_mode()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_current_page(self, page_id: str) -> None:
        self._current_page = page_id
        btn = self._pages.get(page_id)
        if btn is not None:
            btn.setChecked(True)

    def current_page(self) -> str | None:
        return self._current_page

    def set_toggle_icon(self, icon: QIcon, tooltip: str) -> None:
        self.toggle_btn.setIcon(icon)
        self.toggle_btn.setToolTip(tooltip)

    # ---------------- 内部 ----------------
    def _on_toggled(self, expanded: bool) -> None:
        self._collapsed = not expanded
        self._apply_width()
        self._sync_button_mode()
        self.collapsedChanged.emit(self._collapsed)

    def _apply_width(self) -> None:
        self.setFixedWidth(COLLAPSED_WIDTH if self._collapsed else EXPANDED_WIDTH)

    def _sync_button_mode(self) -> None:
        style = (Qt.ToolButtonIconOnly if self._collapsed
                 else Qt.ToolButtonTextBesideIcon)
        self.toggle_btn.setToolButtonStyle(style)
        for container in (self._items_layout, self._bottom_layout):
            for i in range(container.count()):
                widget = container.itemAt(i).widget()
                if isinstance(widget, QToolButton):
                    widget.setToolButtonStyle(style)

    def _on_page_clicked(self, page_id: str) -> None:
        self._current_page = page_id
        self.pageRequested.emit(page_id)
