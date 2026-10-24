# app/nav_rail.py
"""可折叠图标导航轨（NavRail）。

现代桌面软件（VS Code / Fluent NavigationView）的侧边栏形态：

- 窄态（默认）：52px 宽，纯图标按钮，悬浮 tooltip 显示名称；
- 展开态：点击顶部汉堡键，变为 176px，图标 + 文字；
- 选中页：左侧 3px 强调色竖条 + 浅底色（样式在 base.qss 的 #navButton 选择器）；
- 底部槽位固定「插件管理 / 置顶 / 设置」等全局入口，与功能页分组分开；
- 标了 draggable 的条目（插件）可以直接**按住拖动排序**，松手经
  orderChanged 信号把新顺序交给调用方持久化。

条目分两类：
- page   可勾选按钮，点击切换 QStackedWidget 内容页；
- action 普通按钮，点击触发回调（例如打开一个受管窗口）。

图标颜色由调用方（MainWindow）按主题令牌解析后经 icon_factory 注入，
本组件不直接读主题服务，保持可独立测试。
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup,
                               QToolButton, QVBoxLayout, QWidget)

COLLAPSED_WIDTH = 52
EXPANDED_WIDTH = 180
ICON_SIZE = 20
_DRAG_THRESHOLD = 6     # 超过这个像素才算拖动，否则是正常点击


class NavRail(QWidget):
    pageRequested = Signal(str)          # page 型条目被点击
    collapsedChanged = Signal(bool)
    orderChanged = Signal(list)          # 拖动排序后：按新顺序排好的 entry key 列表

    def __init__(self, parent=None) -> None:
        super().__init__()
        self.setObjectName("navRail")
        # 同 TitleBar：不开启则 `QWidget#navRail` 的背景色/右边框在真机上不绘制（实测纯白）
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._bg_color: str | None = None
        self._border_color: str | None = None
        self._collapsed = True
        self._pages: dict[str, QToolButton] = {}
        self._current_page: str | None = None
        # 拖动排序状态
        self._drag: dict[str, Any] = {}          # button -> entry key（可拖动条目）
        self._drag_button: QToolButton | None = None
        self._drag_press_y = 0
        self._dragging = False

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

        # 底部槽位：插件管理 / 置顶 / 设置等全局入口
        self._bottom_layout = QVBoxLayout()
        self._bottom_layout.setSpacing(2)
        layout.addLayout(self._bottom_layout)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._apply_width()

    # ---------------- 外观 ----------------
    def set_colors(self, background: str | None, border: str | None) -> None:
        """由主窗口按主题令牌注入分区色（自绘背景 + 右边框）。

        不依赖 QSS：QWidget 子类在真机上可能不绘制样式表背景，这里自己画一份。
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
                painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        super().paintEvent(event)

    # ---------------- 条目 ----------------
    def set_entries(self,
                    pages: list[dict[str, Any]],
                    actions: list[dict[str, Any]],
                    bottom: list[dict[str, Any]]) -> None:
        """整体重建条目。

        每个条目 dict 字段：
            id         唯一标识（page 型用于切页）
            text       显示文本（展开态）与 tooltip
            icon       QIcon（已由调用方按主题染色）
            handler    action 型点击回调
            draggable  可拖动排序（插件条目）；拖动结果经 orderChanged 发出
            drag_key   拖动排序时上报的键（默认用 id）
        """
        for lay in (self._items_layout, self._bottom_layout):
            while lay.count():
                item = lay.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    if isinstance(widget, QAbstractButton):
                        self._group.removeButton(widget)
                        widget.removeEventFilter(self)
                    # 注意顺序：先 hide 再 setParent(None)——可见控件直接摘父级
                    # 会被 Qt 提升为可见的顶层窗口（屏幕上闪出小窗口的 bug），
                    # 最后 deleteLater 真正销毁
                    widget.hide()
                    widget.setParent(None)
                    widget.deleteLater()
        self._pages.clear()
        self._drag.clear()

        for entry in pages:
            self._items_layout.addWidget(self._make_button(entry, page=True))
        for entry in actions:
            self._items_layout.addWidget(self._make_button(entry, page=False))
        for entry in bottom:
            # 底部槽位同样支持页面型（插件管理/设置）与动作型（置顶钉）
            self._bottom_layout.addWidget(
                self._make_button(entry, page=bool(entry.get("id"))))

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
        handler: Callable | None = entry.get("handler")
        if page:
            btn.setCheckable(True)
            self._group.addButton(btn)
            page_id = entry["id"]
            self._pages[page_id] = btn
            btn.clicked.connect(lambda _c=False, pid=page_id: self._on_page_clicked(pid))
        elif entry.get("checkable"):
            # 可勾选的动作按钮（如置顶钉）：toggled 直传布尔给处理器
            btn.setCheckable(True)
            btn.setChecked(bool(entry.get("checked", False)))
            if handler is not None:
                btn.toggled.connect(handler)
        elif handler is not None:
            btn.clicked.connect(lambda _c=False, h=handler: h())
        if entry.get("draggable"):
            self._drag[btn] = entry.get("drag_key", entry.get("id", ""))
            btn.installEventFilter(self)
        return btn

    # ---------------- 拖动排序 ----------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:     # noqa: N802
        """只处理可拖动条目的鼠标事件；其余一律放行。"""
        if watched not in self._drag:
            return False
        etype = event.type()
        if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._drag_button = watched
            self._drag_press_y = event.globalPosition().toPoint().y()
            self._dragging = False
            return False
        if etype == QEvent.MouseMove and self._drag_button is watched \
                and event.buttons() & Qt.LeftButton:
            y = event.globalPosition().toPoint().y()
            if not self._dragging and abs(y - self._drag_press_y) > _DRAG_THRESHOLD:
                self._dragging = True
                watched.setCursor(Qt.ClosedHandCursor)
            if self._dragging:
                self._reflow(watched, y)
                return True     # 拖动中吞掉 move，避免按钮按下态闪烁
            return False
        if etype == QEvent.MouseButtonRelease and self._drag_button is watched:
            if self._dragging:
                watched.setCursor(Qt.PointingHandCursor)
                watched.setDown(False)   # 拖动吞掉了 release，手动复位按下态
                self._dragging = False
                self._drag_button = None
                self.orderChanged.emit(self.current_order())
                return True     # 吞掉 release，避免误触发点击
            self._drag_button = None
            return False
        return False

    def _reflow(self, button: QToolButton, global_y: int) -> None:
        """按鼠标的纵坐标把被拖按钮实时插到正确槽位（只在中部条目区）。"""
        rail_y = self.mapFromGlobal(QPoint(0, global_y)).y()
        siblings = self._draggable_widgets()
        if button not in siblings:
            return
        current = siblings.index(button)
        others = [w for w in siblings if w is not button]
        # 目标槽位 = 鼠标纵坐标落在哪个条目中心线之前
        target = len(others)
        for index, sibling in enumerate(others):
            center = sibling.mapTo(self, sibling.rect().center()).y()
            if rail_y < center:
                target = index
                break
        if target == current:
            return
        # 先摘出来，再插到目标条目之前（或追加到末尾）
        self._items_layout.removeWidget(button)
        remaining = self._draggable_widgets()
        if target >= len(remaining):
            self._items_layout.addWidget(button)
        else:
            anchor = remaining[target]
            self._items_layout.insertWidget(self._items_layout.indexOf(anchor), button)

    def _draggable_widgets(self) -> list[QToolButton]:
        """中部条目区里可拖动条目（插件），按布局顺序返回。"""
        widgets = []
        for i in range(self._items_layout.count()):
            widget = self._items_layout.itemAt(i).widget()
            if widget in self._drag:
                widgets.append(widget)
        return widgets

    def current_order(self) -> list[str]:
        """中部条目区里可拖动条目（插件）的当前顺序。"""
        keys = []
        for i in range(self._items_layout.count()):
            widget = self._items_layout.itemAt(i).widget()
            if widget in self._drag:
                keys.append(self._drag[widget])
        return keys

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
