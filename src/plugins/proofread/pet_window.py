# plugins/proofread/pet_window.py
"""桌宠窗口：桌宠本体 + 建议气泡合并为单个受管窗口。

为什么合并：
    旧实现里「悬浮按钮」和「建议卡片」是两个各自置顶的顶层窗口，
    谁抢到焦点、谁被盖住全看运气；合并后只有一个窗口，层级由 WindowManager
    统一仲裁，气泡作为子部件随窗口一起显隐、一起移动，不会再互相打架。

外观：
    造型来自皮肤包（assets/skins/<id>/skin.json + SVG 分部件），
    主体颜色即状态色——绿=无错 / 红=有错 / 黄=校对中 / 灰=未启用，
    配合眨眼与呼吸两个小动作，让「有没有错误」一眼可见。

交互：
    左键单击   切换建议气泡
    右键       菜单（换肤 / 暂停校对 / 隐藏气泡 / 设置）
    拖拽       移动，位置写入 config 的 pet.position
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import (QEasingCurve, QPoint, QRect, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMenu, QVBoxLayout, QWidget

from core.services.skin_service import STATE_TOKENS

if TYPE_CHECKING:
    from collections.abc import Callable

WINDOW_ID = "proofread.pet"

#: 状态 -> 回退色（主题服务不可用时兜底，保证桌宠永远看得见）
_FALLBACK_COLORS = {
    "idle": "#107c10",
    "ok": "#107c10",
    "error": "#c42b1c",
    "busy": "#d83b01",
    "disabled": "#8a8a8a",
}


class PetCanvas(QWidget):
    """桌宠画布：按皮肤包把分部件 SVG 画出来，并驱动眨眼/呼吸动画。"""

    def __init__(self, parent=None, skins=None, theme=None) -> None:
        super().__init__(parent)
        self._skins = skins
        self._theme = theme
        self._state = "idle"
        self._breathe = 0.0          # 0~1，呼吸相位强度
        self._blink = 1.0            # 眼睛纵向缩放，1=睁开
        self.setObjectName("petCanvas")

        width, height = (skins.size() if skins else (64, 64))
        self.setFixedSize(width, height)

        anim = skins.animation() if skins else {}
        self._anim = anim
        self._ticker = QTimer(self)
        self._ticker.setInterval(40)          # 25fps，够顺滑且不吃 CPU
        self._ticker.timeout.connect(self._tick)
        self._elapsed = 0.0
        self._next_blink = anim.get("blink_interval_ms", 3400) / 1000.0
        self._blink_left = 0.0

        self._breathe_anim = QVariantAnimation(self)
        self._breathe_anim.setStartValue(0.0)
        self._breathe_anim.setEndValue(1.0)
        self._breathe_anim.setDuration(int(anim.get("breathe_interval_ms", 2600)))
        self._breathe_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._breathe_anim.setLoopCount(-1)
        self._breathe_anim.valueChanged.connect(self._set_breathe)

    # ---------------- 状态 ----------------
    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state if state in STATE_TOKENS else "idle"
        self.update()

    def state(self) -> str:
        return self._state

    def set_services(self, skins=None, theme=None) -> None:
        """主题/皮肤切换后刷新引用并立即重绘。"""
        if skins is not None:
            self._skins = skins
            width, height = skins.size()
            self.setFixedSize(width, height)
        if theme is not None:
            self._theme = theme
        self.update()

    # ---------------- 动画 ----------------
    def start_animation(self) -> None:
        if not self._ticker.isActive():
            self._ticker.start()
        if self._breathe_anim.state() != QVariantAnimation.Running:
            self._breathe_anim.start()

    def stop_animation(self) -> None:
        self._ticker.stop()
        self._breathe_anim.stop()

    def _set_breathe(self, value) -> None:
        self._breathe = float(value)
        self.update()

    def _tick(self) -> None:
        step = self._ticker.interval() / 1000.0
        self._elapsed += step
        duration = self._anim.get("blink_duration_ms", 150) / 1000.0
        if self._blink_left > 0:
            self._blink_left -= step
            self._blink = 0.12 if self._blink_left > 0 else 1.0
        elif self._elapsed >= self._next_blink:
            self._blink_left = duration
            self._blink = 0.12
            self._elapsed = 0.0
            self._next_blink = self._anim.get("blink_interval_ms", 3400) / 1000.0
        else:
            self._blink = 1.0
        self.update()

    # ---------------- 绘制 ----------------
    def state_color(self) -> QColor:
        token = STATE_TOKENS.get(self._state, STATE_TOKENS["idle"])
        fallback = _FALLBACK_COLORS.get(self._state, "#107c10")
        value = self._theme.token(token, fallback) if self._theme is not None else fallback
        return QColor(value)

    def _colors(self) -> dict[str, str]:
        theme = self._theme
        body = theme.token("color.bg.card", "#ffffff") if theme else "#ffffff"
        accent = theme.token("color.accent", "#0078d4") if theme else "#0078d4"
        return {"STATE": QColor(self.state_color()).name(), "BODY": body, "ACCENT": accent}

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt 命名惯例
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        rect = self.rect()
        if self._skins is None or not self._skins.current_id:
            self._paint_fallback(painter, rect)
            return

        # 呼吸：整体轻微缩放，围绕底部中心（脚不动，像在喘气）
        scale = 1.0 + float(self._anim.get("breathe_scale", 0.045)) * math.sin(
            2 * math.pi * self._breathe)
        painter.translate(rect.center().x(), rect.bottom())
        painter.scale(scale, scale)
        painter.translate(-rect.center().x(), -rect.bottom())

        colors = self._colors()
        self._draw(painter, "body", rect, colors)

        layout = self._skins.layout()
        # 闭眼状态（打盹/停用）不眨眼
        eyes_open = self._skins.eye_variant(self._state) == "open"
        for name in ("eye_left", "eye_right"):
            box = layout.get(name)
            if not box:
                continue
            target = QRectF(box["x"], box["y"], box["w"], box["h"])
            if eyes_open and self._blink < 1.0:
                # 眨眼：以眼睛中心为轴压扁
                shrink = target.height() * (1 - self._blink)
                target = target.adjusted(0, shrink / 2, 0, -shrink / 2)
            self._draw(painter, name, target, colors)

        box = layout.get("mouth")
        if box:
            self._draw(painter, "mouth", QRectF(box["x"], box["y"], box["w"], box["h"]), colors)

    def _draw(self, painter: QPainter, part: str, target, colors: dict[str, str]) -> None:
        svg = self._skins.part_svg(part, self._state, colors=colors)
        if not svg:
            return
        renderer = QSvgRenderer(svg.encode("utf-8"))
        if not renderer.isValid():
            return
        if isinstance(target, QRectF):
            renderer.render(painter, target)
        else:
            renderer.render(painter, QRectF(target))

    def _paint_fallback(self, painter: QPainter, rect) -> None:
        """没有皮肤包时画一个极简圆头，保证功能不受影响。"""
        color = self.state_color()
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        side = min(rect.width(), rect.height()) * 0.8
        painter.drawEllipse(rect.center().x() - side / 2, rect.center().y() - side / 2,
                            side, side)


class PetWindow(QWidget):
    """桌宠 + 建议气泡的合并窗口。"""

    #: 气泡显隐变化（供插件同步状态）
    bubbleToggled = Signal(bool)
    #: 用户请求切换校对开关（右键菜单）
    proofreadToggleRequested = Signal()
    #: 用户请求打开设置
    settingsRequested = Signal()

    def __init__(self, config, api=None, parent=None, ignore_mgr=None,
                 correction_handler=None, theme=None, skins=None, i18n=None,
                 tr: "Callable[..., str] | None" = None,
                 tools_provider: "Callable[[], list] | None" = None) -> None:
        super().__init__(parent)
        self.config = config
        self.api = api
        self.ignore_mgr = ignore_mgr
        self._theme = theme
        self._skins = skins
        self._i18n = i18n
        self._tr = tr or (lambda key, **_p: key)
        self._correction_handler = correction_handler
        # 右键菜单「插件」子菜单的数据来源：返回 [(名称, 回调)]，现场取值
        # 保证切语言后菜单文案是最新的
        self._tools_provider = tools_provider
        self._drag_pos: QPoint | None = None
        self._dragged = False
        self._state = "idle"
        self._pet_anchor = None            # 宠物在屏幕上的矩形（QRect），布局变化的唯一锚点

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("petWindow")

        from plugins.proofread.suggestion_window import SuggestionCard

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.bubble = SuggestionCard(self, theme=theme, i18n=i18n, embedded=True)
        self.bubble.accepted.connect(self._on_accepted)
        self.bubble.ignored.connect(self._on_ignored)
        self.bubble.setVisible(False)
        layout.addWidget(self.bubble, 0, Qt.AlignHCenter)
        # 待处理建议（original, corrected, start, end）；None 表示"没有新建议"。
        # 用户采纳/忽略后必须清掉：否则下次点桌宠会把处理过的旧建议再弹出来，
        # 再点确认就按陈旧偏移重写正文。
        self._pending_suggestion = None

        self.canvas = PetCanvas(self, skins=skins, theme=theme)
        layout.addWidget(self.canvas, 0, Qt.AlignHCenter)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._apply_position()
        self._refresh_tooltip()

    # ---------------- 文案 ----------------
    def tr(self, key: str, /, **params) -> str:
        return self._tr(key, **params)

    def retranslateUi(self) -> None:        # noqa: N802 - Qt 命名惯例
        self._refresh_tooltip()
        self.bubble.retranslateUi()

    def _refresh_tooltip(self) -> None:
        state_text = self.tr(f"pet.state.{self._state}")
        self.setToolTip(f"{self.tr('pet.tooltip')}\n{self.tr('pet.state_label')}{state_text}")
        self.canvas.setToolTip(self.toolTip())

    # ---------------- 状态 ----------------
    def set_state(self, state: str) -> None:
        self._state = state if state in STATE_TOKENS else "idle"
        self.canvas.set_state(self._state)
        self._refresh_tooltip()

    def state(self) -> str:
        return self._state

    def set_services(self, skins=None, theme=None) -> None:
        if skins is not None:
            self._skins = skins
        if theme is not None:
            self._theme = theme
            self.bubble._theme = theme
        self.canvas.set_services(skins, theme)
        self._reflow_keeping_pet()

    # ---------------- 气泡 ----------------
    def set_suggestion(self, original, corrected, start=-1, end=-1) -> None:
        """收到校对结果：无差异则记 idle，有差异则记 error 并按配置弹出气泡。"""
        if not original or not corrected or original.strip() == corrected.strip():
            self._pending_suggestion = None
            self.bubble.clear_diff()
            self.bubble.setVisible(False)
            self._after_bubble_change()
            return
        self._pending_suggestion = (original, corrected, start, end)
        self.bubble.show_suggestion(original, corrected, None, self.config,
                                    start=start, end=end)
        if not self.config.get("auto_show_suggestion", True):
            self.bubble.setVisible(False)
        self._after_bubble_change()

    def _discard_suggestion(self) -> None:
        """建议被采纳/忽略后清现场：不清的话旧建议会"复活"并按陈旧偏移改写正文。"""
        self._pending_suggestion = None
        self.bubble.clear_diff()
        self.bubble.hide()
        self._after_bubble_change()

    def toggle_bubble(self) -> None:
        if self.bubble.isVisible():
            self.bubble.hide()
        elif self._pending_suggestion is not None:
            self.bubble.show()
        else:
            self.bubble.hide()      # 没有新建议：不把处理过的旧建议再弹出来
        self._after_bubble_change()

    def _after_bubble_change(self) -> None:
        """气泡显隐后重新布局，让**桌宠本体**在屏幕上的位置保持不变。

        单一事实源是「宠物矩形」（`_pet_anchor`）：布局变化后用"宠物应该在哪"反推窗口左上角，
        而不是像旧实现那样补偿"上一次的窗口底边"——后者会随每次补偿累积误差，
        表现为显隐几次之后宠物慢慢漂移。
        """
        self._reflow_keeping_pet()
        self.bubbleToggled.emit(self.bubble.isVisible())

    # ---------- 宠物定位（锚定宠物矩形，不依赖窗口底边） ----------
    def _pet_rect(self) -> QRect:
        """宠物画布在**屏幕坐标系**里的矩形。"""
        top_left = self.canvas.mapToGlobal(self.canvas.rect().topLeft())
        return QRect(top_left, self.canvas.size())

    def _remember_pet_anchor(self) -> None:
        rect = self._pet_rect()
        if not rect.isEmpty():
            self._pet_anchor = rect

    def _reflow_keeping_pet(self) -> None:
        """重新布局，并把宠物矩形的左上角还原到 `_pet_anchor`。"""
        target = getattr(self, "_pet_anchor", None)
        self.adjustSize()
        layout = self.layout()
        if layout is not None:
            layout.activate()
        if target is not None and not target.isEmpty():
            current = self._pet_rect()
            delta = target.topLeft() - current.topLeft()
            if delta.x() or delta.y():
                self._move_clamped(self.x() + delta.x(), self.y() + delta.y())
        self._remember_pet_anchor()

    def _move_clamped(self, x: int, y: int) -> None:
        """移动并夹到当前屏幕可用区域内，避免气泡超出屏幕后桌宠跑出可视范围。"""
        screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        width = self.frameGeometry().width()
        height = self.frameGeometry().height()
        # 窗口比屏幕还大时（小屏 + 长气泡）优先保住顶部/左侧
        x = max(area.left(), min(int(x), max(area.left(), area.right() - width)))
        y = max(area.top(), min(int(y), max(area.top(), area.bottom() - height)))
        self.move(x, y)

    # ---------------- 位置 ----------------
    def _apply_position(self) -> None:
        self.adjustSize()
        saved = self.config.get("pet.position", None)
        screen = QApplication.primaryScreen().availableGeometry()
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            x, y = int(saved[0]), int(saved[1])
        else:
            x = screen.right() - self.width() - 24
            y = screen.bottom() - self.height() - 24
        self._move_clamped(x, y)
        self._remember_pet_anchor()

    def _save_position(self) -> None:
        self._remember_pet_anchor()
        self.config.set("pet.position", [self.x(), self.y()])

    # ---------------- 交互 ----------------
    def mousePressEvent(self, event) -> None:      # noqa: N802 - Qt 命名惯例
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:       # noqa: N802 - Qt 命名惯例
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._dragged = True
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802 - Qt 命名惯例
        if event.button() == Qt.LeftButton:
            if self._dragged:
                self._save_position()
            else:
                self.toggle_bubble()
            self._drag_pos = None
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:            # noqa: N802 - Qt 命名惯例
        super().showEvent(event)
        self._remember_pet_anchor()
        self.canvas.start_animation()

    def hideEvent(self, event) -> None:            # noqa: N802 - Qt 命名惯例
        self.canvas.stop_animation()
        super().hideEvent(event)

    def _show_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setObjectName("petMenu")

        skins_menu = menu.addMenu(self.tr("pet.menu.skin"))
        current_skin = self._skins.current_id if self._skins else ""
        for item in (self._skins.available() if self._skins else []):
            action = skins_menu.addAction(item["name"],
                                          lambda sid=item["id"]: self._switch_skin(sid))
            action.setCheckable(True)
            action.setChecked(item["id"] == current_skin)

        # 插件快捷入口：各插件注册的动作一键直达（导航助手等）
        tools = self._tools_provider() if self._tools_provider else []
        if tools:
            tools_menu = menu.addMenu(self.tr("pet.menu.plugins"))
            for label, handler in tools:
                tools_menu.addAction(label, handler)

        menu.addSeparator()
        menu.addAction(self.tr("pet.menu.toggle_proofread"),
                       self.proofreadToggleRequested.emit)
        bubble_action = menu.addAction(self.tr("pet.menu.toggle_bubble"), self.toggle_bubble)
        bubble_action.setCheckable(True)
        bubble_action.setChecked(self.bubble.isVisible())
        menu.addAction(self.tr("pet.menu.settings"), self.settingsRequested.emit)
        menu.exec(self.mapToGlobal(pos))

    def _switch_skin(self, skin_id: str) -> None:
        if self._skins is None:
            return
        self._skins.set_skin(skin_id)
        self.set_services(skins=self._skins)

    # ---------------- 建议回调 ----------------
    def _on_accepted(self, corrected, start, end, original) -> None:
        if self._correction_handler is not None:
            self._correction_handler(corrected, start=start, end=end)
        self._discard_suggestion()

    def _on_ignored(self, text, start, end, original) -> None:
        if self.ignore_mgr is not None:
            self.ignore_mgr.add_ignore(original)
        if start >= 0 and end > start:
            from plugins.proofread.word_monitor import clear_error_marks

            try:
                clear_error_marks(start, end)
            except Exception:
                pass      # Word 可能已关闭，忽略 COM 异常
        self._discard_suggestion()

    def refresh_visibility(self) -> None:
        """由插件在「显示悬浮按钮」配置变化后调用。"""
        visible = bool(self.config.get("show_floating_button", True))
        self.setVisible(visible)
