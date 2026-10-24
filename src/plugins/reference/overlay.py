# plugins/reference/overlay.py
"""引用处悬浮窗：显示文献信息 + 直接编辑备注（可拖动 / 置顶 / 可关闭 / 多篇堆叠）。

交互约定（用户已确认）：
- 标题条可拖动；右上 ✕ 关闭；**光标离开该引用 600 ms 后自动隐藏**（由 word_watch 触发）；
- `[1-3]` 这类一格里多篇时，按编号从上往下逐条列，**每条独立编辑框**；
- 备注编辑停顿 800 ms 自动保存到 CSV，同时保留「保存」按钮手动触发。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from plugins.reference.overlay_geometry import place_near

logger = logging.getLogger(__name__)

AUTOSAVE_MS = 800
MAX_VISIBLE = 8
WIDTH = 340


@dataclass
class OverlayEntry:
    """悬浮窗里的一条文献。"""

    anchor_key: str
    label: str = ""
    title: str = ""
    meta: str = ""
    note: str = ""


class _EntryCard(QFrame):
    """单条文献卡片：标题 + 元信息 + 备注编辑（独立保存）。"""

    noteEdited = Signal(str, str)          # (anchor_key, 文本)

    def __init__(self, entry: OverlayEntry, tr, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setObjectName("referenceEntry")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

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
            self.meta_label.setObjectName("hintLabel")
            self.meta_label.setWordWrap(True)
            layout.addWidget(self.meta_label)

        self.note_edit = QTextEdit()
        self.note_edit.setObjectName("referenceNote")
        self.note_edit.setPlainText(entry.note)
        self.note_edit.setMinimumHeight(46)
        self.note_edit.setMaximumHeight(96)
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
        self._timer.stop()
        self.noteEdited.emit(self._entry.anchor_key, self.note_edit.toPlainText())

    @property
    def entry(self) -> OverlayEntry:
        return self._entry


class ReferenceOverlay(QWidget):
    """桌面上浮的引用信息卡。"""

    #: 用户点 ✕ 关闭（参数为本次不再自动弹出的 anchor_key，空串表示全部）
    dismissed = Signal(str)

    def __init__(self, store, tr, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._tr = tr
        self._cards: list[_EntryCard] = []
        self._drag_offset: QPoint | None = None
        self._dismissed_key = ""

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("referenceOverlay")
        self.setFixedWidth(WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.frame = QFrame()
        self.frame.setObjectName("referenceOverlayFrame")
        outer.addWidget(self.frame)
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(10, 8, 10, 10)
        frame_layout.setSpacing(6)

        # 拖动条 + 关闭
        header = QHBoxLayout()
        header.setSpacing(6)
        self.title_bar = QLabel(self._tr("overlay.title"))
        self.title_bar.setObjectName("referenceOverlayTitle")
        header.addWidget(self.title_bar, 1)
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("referenceOverlayClose")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.clicked.connect(self._on_close_clicked)
        header.addWidget(self.close_btn, 0, Qt.AlignTop)
        header_host = QWidget()
        header_host.setLayout(header)
        frame_layout.addWidget(header_host)
        self._header_host = header_host
        header_host.installEventFilter(self)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
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
            self._body_layout.addWidget(card)
            self._cards.append(card)
        self._body_layout.addStretch(1)

    def _save_note(self, anchor_key: str, text: str) -> None:
        try:
            self._store.set_note(anchor_key, note=text)
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
        if watched is self._header_host:
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
