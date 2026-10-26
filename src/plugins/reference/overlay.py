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
MIN_HEIGHT = 84


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

    def __init__(self, entry: OverlayEntry, tr, parent: QWidget | None = None,
                 note_service=None, on_pick=None) -> None:
        super().__init__(parent)
        self._entry = entry
        self._note_service = note_service
        self._on_pick_notes = on_pick
        self._on_pick = on_pick
        self._dict_tr = tr
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

        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.setSpacing(6)
        self.note_source_label = QLabel(tr("overlay.note_child"))
        self.note_source_label.setObjectName("overlayHint")
        note_row.addWidget(self.note_source_label, 1)
        self.notes_btn = QPushButton()
        self.notes_btn.setObjectName("referenceChip")
        self.notes_btn.setCursor(Qt.PointingHandCursor)
        self.notes_btn.setFixedHeight(20)
        self.notes_btn.setMinimumWidth(84)          # 之前被挤压到看不见
        self.notes_btn.clicked.connect(self._open_picker)
        note_row.addWidget(self.notes_btn, 0)
        layout.addLayout(note_row)

        self.note_edit = QTextEdit()
        self.note_edit.setObjectName("referenceNote")
        self.note_edit.setPlainText(entry.note)
        self.note_edit.setFixedHeight(56)      # 三行左右：用户反馈两行显示不全
        self.note_edit.setPlaceholderText(tr("overlay.note_placeholder"))
        layout.addWidget(self.note_edit)
        self.note_edit.textChanged.connect(self._on_text_changed)
        self._refresh_note_meta()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(AUTOSAVE_MS)
        self._timer.timeout.connect(self.flush)

    def refresh_note_state(self) -> None:
        """重新解析当前应显示的备注（挑选变化后调用）。"""
        if self._note_service is None:
            return
        text, _source, _hit = self._note_service.effective(self._entry.anchor_key)
        if text and self.note_edit.toPlainText() != text:
            self.note_edit.blockSignals(True)
            self.note_edit.setPlainText(text)
            self.note_edit.blockSignals(False)
        self._refresh_note_meta()

    def _refresh_note_meta(self) -> None:
        """刷新「来源徽标 + 总库条数」：只有 1 条时不需要用户挑选。"""
        if self._note_service is None or not hasattr(self, "notes_btn"):
            self.notes_btn.setVisible(False)
            self.note_source_label.setVisible(False)
            return
        ref_key = self._entry.anchor_key
        notes = self._note_service.master_notes(ref_key)
        text, source, _hit = self._note_service.effective(ref_key)
        labels = {
            "child": self._dict_tr("overlay.note_child"),
            "inherit": self._dict_tr("overlay.note_inherit"),
            "auto": self._dict_tr("overlay.note_auto"),
            "default": self._dict_tr("overlay.note_default"),
            "none": self._dict_tr("overlay.note_none"),
        }
        self.note_source_label.setText(labels.get(source, ""))
        template = self._dict_tr("overlay.master_count")
        self.notes_btn.setText(template.replace("{count}", str(len(notes))))
        self.notes_btn.setVisible(bool(notes))
        if text and not self.note_edit.toPlainText():
            self.note_edit.setPlainText(text)     # 自动填充/跟随：先把内容显示出来

    def _open_picker(self) -> None:
        if self._note_service is None:
            return
        from plugins.reference.note_picker import NotePicker

        ref_key = self._entry.anchor_key
        dialog = NotePicker(self._note_service.master_notes(ref_key),
                            current_id=self._note_service.inherit_id(ref_key),
                            tr=self.tr, parent=self.window(),
                            service=self._note_service)
        if dialog.exec() != dialog.Accepted:
            return
        if dialog.new_note_requested:
            self._create_master_note(ref_key)
            return
        if dialog.fork_requested:
            picked = next((note for note in self._note_service.master_notes(ref_key)
                           if note.note_id == dialog.selected_note_id), None)
            if picked is not None:
                self.note_edit.setPlainText(picked.text)
                self.noteEdited.emit(ref_key, picked.text)   # 走统一的保存路径
            self._refresh_note_meta()
            return
        if callable(self._on_pick):
            self._on_pick(ref_key, dialog.selected_note_id)
        self._refresh_note_meta()

    def _create_master_note(self, ref_key: str) -> None:
        """新建一条总库备注，建好后自动挑选它。"""
        from plugins.reference.note_editor import NoteEditor

        editor = NoteEditor(tr=self._dict_tr, parent=self.window())
        if editor.exec() != editor.Accepted:
            return
        note = self._note_service.create_master_note(ref_key, editor.text, editor.label)
        if callable(self._on_pick):
            self._on_pick(ref_key, note.note_id)
        self._refresh_note_meta()

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


class _CloseButton(QWidget):
    """自绘关闭按钮（纯 QWidget：不受全局 QPushButton 样式影响，也不依赖字体/emoji）。"""

    clicked = Signal()
    SIZE = 22

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("referenceOverlayClose")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip("")

    def sizeHint(self):                        # noqa: N802 - Qt 命名惯例
        from PySide6.QtCore import QSize

        return QSize(self.SIZE, self.SIZE)

    # 点击（按下在按钮内、抬起也在按钮内才算）
    def mouseReleaseEvent(self, event) -> None:      # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:             # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:             # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:             # noqa: N802
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        base = self.palette().color(self.foregroundRole())
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        if self.underMouse():
            fill = QColor(base)
            fill.setAlpha(40)
        else:
            fill = QColor(self.palette().color(self.window().backgroundRole()))
            fill = fill.lighter(104)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 6, 6)

        border = QPen(QColor(base.red(), base.green(), base.blue(), 70))
        border.setWidthF(1.0)
        painter.setPen(border)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 6, 6)

        pen = QPen(base)
        pen.setWidthF(1.9)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        inner = rect.adjusted(6.5, 6.5, -6.5, -6.5)
        painter.drawLine(inner.topLeft(), inner.bottomRight())
        painter.drawLine(inner.topRight(), inner.bottomLeft())
        painter.end()


class ReferenceOverlay(QWidget):
    """桌面上浮的引用信息卡。"""

    dismissed = Signal(str)                # 用户点 ✕（本次不再自动弹出的 anchor_key）
    bindRequested = Signal(str, str)       # (anchor_key, doc_name)
    createRequested = Signal(str, str)     # (anchor_key, doc_name)
    noteSaved = Signal(str)                # 备注已落盘（anchor_key），供页面刷新

    def __init__(self, store, tr, parent: QWidget | None = None, note_service=None,
                 on_pick=None) -> None:
        super().__init__(parent)
        self._store = store
        self._tr = tr
        self._note_service = note_service
        self._on_pick_notes = on_pick
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
        self.title_bar_host.setFixedHeight(30)
        header = QHBoxLayout(self.title_bar_host)
        header.setContentsMargins(8, 4, 6, 4)
        header.setSpacing(0)
        header.addStretch(1)
        self.close_btn = _CloseButton(self.title_bar_host)
        self.close_btn.setToolTip(self._tr("overlay.close_tip"))
        self.close_btn.clicked.connect(self._on_close_clicked)
        header.addWidget(self.close_btn, 0, Qt.AlignTop)
        self.title_bar_host.installEventFilter(self)
        frame_layout.addWidget(self.title_bar_host)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 不要可见滚动条
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
        self._fit_height()
        self.adjustSize()
        area = self._work_area(anchor)
        x, y = place_near(anchor[0], anchor[1], self.width(), self.height(), area)
        self.move(x, y)
        self.show()
        self.raise_()

    def _fit_height(self) -> None:
        """按内容定高：单条文献不留空白，多条才封顶到 MAX_HEIGHT 走滚动。

        直接累加每张卡片的 sizeHint（备注框是固定两行高），不依赖滚动区的 sizeHint 刷新时机。
        """
        self._body_layout.activate()
        cards = sum(card.sizeHint().height() for card in self._cards)
        gaps = max(0, len(self._cards) - 1) * self._body_layout.spacing()
        chrome = self.title_bar_host.height() + 16      # 顶部细条 + 内外边距
        content = cards + gaps + 8
        self.setFixedHeight(max(MIN_HEIGHT, min(content + chrome, MAX_HEIGHT)))

    def hide_overlay(self) -> None:
        # R5：关闭前收尾草稿（与切引用共用同一条路径）
        for card in getattr(self, "_cards", []):
            try:
                card.flush()
            except Exception:                  # noqa: BLE001
                pass
        for card in self._cards:
            card.flush()
        self.hide()

    def move_to(self, x: int, y: int) -> None:
        self.move(int(x), int(y))

    def refresh_notes(self) -> None:
        """挑选总库备注后，就地刷新卡片上的来源徽标与备注文本（不重建窗口）。"""
        for card in self._cards:
            try:
                card.refresh_note_state()
            except Exception:              # noqa: BLE001 - 单卡异常不影响其它卡
                continue

    def _rebuild(self, entries: list[OverlayEntry]) -> None:
        # R5：卡片即将被销毁 —— 先把 800ms 自动保存里的草稿落盘，
        # 否则"输入后立刻点下一个引用"会把刚敲的文字连同定时器一起丢掉。
        for card in self._cards:
            try:
                card.flush()
            except Exception:                  # noqa: BLE001 - 单卡失败不影响重建
                pass
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []
        for entry in entries:
            card = _EntryCard(entry, self._tr, self.scroll.widget(),
                              note_service=self._note_service,
                              on_pick=self._on_pick_notes)
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
