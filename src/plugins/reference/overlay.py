# plugins/reference/overlay.py
"""引用处悬浮窗：显示文献信息 + 直接编辑备注（可拖动 / 置顶 / 可关闭 / 多篇堆叠）。

交互约定（用户已确认）：
- 标题条可拖动；右上 ✕ 关闭；光标离开该引用 600 ms 后自动隐藏（由 word_watch 触发）；
- `[1-3]` 这类一格里多篇时，按编号从上往下逐条列，每条独立编辑框；
- 备注编辑停顿 800 ms 自动保存到 CSV，另有「保存」按钮手动触发；
- **EndNote 那种"只有域壳、没有文献数据"的引用**：显示「未绑定」卡片，
  提供「绑定已有文献 / 新建文献」两个动作（用户反馈"点了没反应"就是这一段缺失）。

视觉：卡片**不透明底色 + 1 px 主边框 + 投影**，避免压在白色 Word 正文上看不见边界。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from plugins.reference.overlay_geometry import place_near

logger = logging.getLogger(__name__)

AUTOSAVE_MS = 800
MAX_VISIBLE = 8
WIDTH = 360
MAX_HEIGHT = 520


@dataclass
class OverlayEntry:
    """悬浮窗里的一条文献。"""

    anchor_key: str
    label: str = ""
    title: str = ""
    meta: str = ""
    note: str = ""
    doc_name: str = ""
    #: 库里已有这条（哪怕是空占位）→ 直接给编辑框，而不是"未绑定"卡片
    force_edit: bool = False

    @property
    def unbound(self) -> bool:
        """没有元数据也没有备注 = 只有域壳的引用（EndNote 空壳域 / 交叉引用）。"""
        if self.force_edit:
            return False
        return not (self.title or self.meta or self.note)


class _EntryCard(QFrame):
    """单条文献卡片：标题 + 元信息 + 备注编辑（独立保存）；未绑定时给绑定入口。"""

    noteEdited = Signal(str, str)          # (anchor_key, 文本)
    bindRequested = Signal(str)            # anchor_key
    createRequested = Signal(str)          # anchor_key

    def __init__(self, entry: OverlayEntry, tr, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setObjectName("referenceEntry")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.label_chip = QLabel(f"[{entry.label}]" if entry.label else "•")
        self.label_chip.setObjectName("referenceChip")
        head.addWidget(self.label_chip, 0, Qt.AlignTop)
        self.title_label = QLabel(entry.title or tr("overlay.untitled"))
        self.title_label.setObjectName("referenceTitle")
        self.title_label.setWordWrap(True)
        head.addWidget(self.title_label, 1)
        layout.addLayout(head)

        if entry.meta:
            self.meta_label = QLabel(entry.meta)
            self.meta_label.setObjectName("overlayMeta")
            self.meta_label.setWordWrap(True)
            layout.addWidget(self.meta_label)

        if entry.unbound:
            hint = QLabel(tr("overlay.unbound_hint"))
            hint.setObjectName("overlayHint")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            actions = QHBoxLayout()
            actions.setSpacing(6)
            self.bind_btn = QPushButton(tr("overlay.bind"))
            self.create_btn = QPushButton(tr("overlay.create"))
            for button in (self.bind_btn, self.create_btn):
                button.setCursor(Qt.PointingHandCursor)
                button.setFixedHeight(24)
                actions.addWidget(button)
            actions.addStretch(1)
            layout.addLayout(actions)
            self.bind_btn.clicked.connect(lambda: self.bindRequested.emit(entry.anchor_key))
            self.create_btn.clicked.connect(lambda: self.createRequested.emit(entry.anchor_key))
            return

        self.note_edit = QTextEdit()
        self.note_edit.setObjectName("referenceNote")
        self.note_edit.setPlainText(entry.note)
        self.note_edit.setMinimumHeight(40)
        self.note_edit.setMaximumHeight(80)
        self.note_edit.setPlaceholderText(tr("overlay.note_placeholder"))
        layout.addWidget(self.note_edit)
        self.note_edit.textChanged.connect(self._on_text_changed)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(AUTOSAVE_MS)
        self._timer.timeout.connect(self.flush)

    def _on_text_changed(self) -> None:
        self._timer.start()

    def flush(self) -> None:
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        if getattr(self, "note_edit", None) is None:
            return
        self.noteEdited.emit(self._entry.anchor_key, self.note_edit.toPlainText())

    @property
    def entry(self) -> OverlayEntry:
        return self._entry


class ReferenceOverlay(QWidget):
    """桌面上浮的引用信息卡。"""

    dismissed = Signal(str)                # 用户点 ✕（本次不再自动弹出的 anchor_key）
    bindRequested = Signal(str, str)       # (anchor_key, doc_name)
    createRequested = Signal(str, str)     # (anchor_key, doc_name)
    noteSaved = Signal(str)                # 备注已落盘（anchor_key），供页面刷新

    def __init__(self, store, tr, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._tr = tr
        self._cards: list[_EntryCard] = []
        self._drag_offset: QPoint | None = None
        self._dismissed_key = ""

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        # 透明只为圆角抗锯齿；卡片本身用不透明底色绘制（否则压在白色 Word 上看不出边界）
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("referenceOverlay")
        self.setFixedWidth(WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)      # 给投影留白
        outer.setSpacing(0)

        self.frame = QFrame()
        self.frame.setObjectName("referenceOverlayFrame")
        outer.addWidget(self.frame)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.frame.setGraphicsEffect(shadow)

        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(0, 0, 0, 8)
        frame_layout.setSpacing(6)

        # 顶部细条：整条可拖动，只有右上角一个 ✕（不再放"参考文献"标题，省空间）
        self.title_bar_host = QWidget()
        self.title_bar_host.setObjectName("referenceOverlayBar")
        self.title_bar_host.setFixedHeight(16)
        header = QHBoxLayout(self.title_bar_host)
        header.setContentsMargins(0, 0, 2, 0)
        header.setSpacing(0)
        header.addStretch(1)
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("referenceOverlayClose")
        self.close_btn.setFixedSize(16, 16)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip(self._tr("overlay.close_tip"))
        self.close_btn.clicked.connect(self._on_close_clicked)
        header.addWidget(self.close_btn, 0, Qt.AlignTop)
        self.title_bar_host.installEventFilter(self)
        frame_layout.addWidget(self.title_bar_host)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(10, 0, 10, 0)
        self._body_layout.setSpacing(6)
        self.scroll.setWidget(self._body)
        frame_layout.addWidget(self.scroll, 1)

    # ---------------- 生命周期 ----------------
    def show_entries(self, entries: list[OverlayEntry], anchor: tuple[int, int]) -> None:
        visible = entries[:MAX_VISIBLE]
        if not visible:
            self.hide_overlay()
            return
        self._rebuild(visible)
        self.adjustSize()
        self.setMaximumHeight(MAX_HEIGHT)
        area = self._work_area(anchor)
        x, y = place_near(anchor[0], anchor[1], self.width(), self.height(), area)
        self.move(x, y)
        self.show()
        self.raise_()

    def hide_overlay(self) -> None:
        for card in self._cards:
            card.flush()
        self.hide()

    def move_to(self, x: int, y: int) -> None:
        self.move(int(x), int(y))

    def _rebuild(self, entries: list[OverlayEntry]) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []
        for entry in entries:
            card = _EntryCard(entry, self._tr)
            card.noteEdited.connect(self._save_note)
            card.bindRequested.connect(lambda key, doc=entry.doc_name: self.bindRequested.emit(key, doc))
            card.createRequested.connect(
                lambda key, doc=entry.doc_name: self.createRequested.emit(key, doc))
            self._body_layout.addWidget(card)
            self._cards.append(card)
        self._body_layout.addStretch(1)

    def _save_note(self, anchor_key: str, text: str) -> None:
        try:
            self._store.set_note(anchor_key, note=text)
            self.noteSaved.emit(anchor_key)
        except Exception as exc:                     # noqa: BLE001 - 保存失败不能崩悬浮窗
            logger.warning("悬浮窗保存备注失败（%s）：%s", anchor_key, exc)

    # ---------------- 交互 ----------------
    @property
    def current_key(self) -> str:
        return self._cards[0].entry.anchor_key if self._cards else ""

    def _on_close_clicked(self) -> None:
        self._dismissed_key = self.current_key
        self.hide_overlay()
        self.dismissed.emit(self._dismissed_key)

    def eventFilter(self, watched, event):           # noqa: N802 - Qt 命名惯例
        if watched is self.title_bar_host:
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == event.Type.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() == event.Type.MouseButtonRelease:
                self._drag_offset = None
                return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _work_area(anchor: tuple[int, int]) -> tuple[int, int, int, int]:
        screen = QGuiApplication.screenAt(QPoint(*anchor)) or QGuiApplication.primaryScreen()
        rect = screen.availableGeometry()
        return (rect.left(), rect.top(), rect.right(), rect.bottom())
