# plugins/proofread/pet_window.py
"""桌宠窗口：桌宠本体 + 建议气泡合并为单个受管窗口。

为什么合并：
    旧实现里「悬浮按钮」和「建议卡片」是两个各自置顶的顶层窗口，
    谁抢到焦点、谁被盖住全看运气；合并后只有一个窗口，层级由 WindowManager
    统一仲裁，气泡作为子部件随窗口一起显隐、一起移动，不会再互相打架。

外观：
    造型来自皮肤包（assets/skins/<id>/skin.json + SVG 分部件），
    新皮肤保持角色本色，用绿色勾/珊瑚红叹号、光环与表情区分关键状态；
    休息为中性，配合眨眼、呼吸和新建议的一次轻跳。

交互：
    左键单击   切换建议气泡
    右键       菜单（换肤 / 暂停校对 / 隐藏气泡 / 设置）
    拖拽       移动，位置写入 config 的 pet.position
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (QEasingCurve, QPoint, QRect, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from core.widgets.pet_events import PetEventMixin
from core.widgets.pet_menu import PetMenuMixin
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMenu, QVBoxLayout, QWidget

from core.services.skin_service import STATE_COLORS, STATE_TOKENS

if TYPE_CHECKING:
    from collections.abc import Callable

WINDOW_ID = "proofread.pet"

#: 状态 -> 回退色（主题服务不可用时兜底，保证桌宠永远看得见）
_FALLBACK_COLORS = STATE_COLORS


class PetCanvas(QWidget):
    """桌宠画布：按皮肤包把分部件 SVG 画出来，并驱动眨眼/呼吸动画。"""

    def __init__(self, parent=None, skins=None, theme=None) -> None:
        super().__init__(parent)
        self._skins = skins
        self._theme = theme
        self._state = "idle"
        self._breathe = 0.0          # 0~1，呼吸相位强度
        self._attention = 0.0
        self._blink = 1.0            # 眼睛纵向缩放，1=睁开
        self.setObjectName("petCanvas")

        width, height = (skins.size() if skins else (64, 64))
        self.setFixedSize(width, height)
        self._hook_pet_service()

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
        self._attention_anim = QVariantAnimation(self)
        self._attention_anim.setDuration(480)
        self._attention_anim.setKeyValues([(0, 0.0), (0.35, 1.0), (0.7, 0.35), (1, 0.0)])
        self._attention_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._attention_anim.valueChanged.connect(self._set_attention)

    # ---------------- 状态 ----------------
    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state if state in STATE_TOKENS else "idle"
        if self._state != "error":
            self._attention_anim.stop()
            self._attention = 0.0
        self.update()

    def state(self) -> str:
        return self._state

    # ---------------- 内核状态联动 ----------------
    def _base_size(self) -> tuple[int, int]:
        if self._skins is not None:
            try:
                return self._skins.size()
            except Exception:              # noqa: BLE001
                pass
        return 64, 64

    def set_pet_size(self, size: int) -> None:
        """按内核设定的像素尺寸等比缩放（皮肤基准 64 px）。"""
        width, height = self._base_size()
        factor = max(0.4, min(4.0, float(size) / 64.0))
        anchor = self.pos()                       # 缩放时保持左上角不动（避免位置漂移）
        self.setFixedSize(max(24, int(width * factor)), max(24, int(height * factor)))
        self.move(anchor)
        self._scale = factor
        self.update()

    def _hook_pet_service(self) -> None:
        try:
            from core.services.pet_service import PetService

            service = PetService.instance()
            service.visibilityChanged.connect(lambda visible: self.setVisible(bool(visible)))
            self.set_pet_size(service.size)
        except Exception:                  # noqa: BLE001
            pass

    def set_services(self, skins=None, theme=None) -> None:
        """主题/皮肤切换后刷新引用并立即重绘。"""
        if skins is not None:
            self._skins = skins
            self._anim = skins.animation()
            self._breathe_anim.setDuration(int(self._anim["breathe_interval_ms"]))
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
        self._attention_anim.stop()
        self._attention = 0.0

    def notify_suggestion(self) -> None:
        """仅在新建议到达时轻跳一次；动画只影响角色绘制，不移动窗口。"""
        if self._state != "disabled" and self._skins is not None and self._skins.status_overlay_enabled():
            self._attention_anim.stop()
            self._attention_anim.start()

    def _set_attention(self, value) -> None:
        self._attention = float(value)
        self.update()

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
        if self._skins is not None and self._skins.status_overlay_enabled():
            return QColor(STATE_COLORS[self._state])
        token = STATE_TOKENS.get(self._state, STATE_TOKENS["idle"])
        fallback = _FALLBACK_COLORS.get(self._state, STATE_COLORS["idle"])
        value = self._theme.token(token, fallback) if self._theme is not None else fallback
        return QColor(value)

    def _colors(self) -> dict[str, str]:
        theme = self._theme
        body = theme.token("color.bg.card", "#ffffff") if theme else "#ffffff"
        accent = theme.token("color.accent", "#0078d4") if theme else "#0078d4"
        return {"STATE": QColor(self.state_color()).name(), "BODY": body, "ACCENT": accent}

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt 命名惯例
        from PySide6.QtGui import QPainter

        from core.widgets.pet_render import paint_scaled

        painter = QPainter(self)
        try:
            paint_scaled(self, painter)
        finally:
            painter.end()
    def _paint(self, painter: QPainter) -> None:
        from core.widgets.pet_render import paint_pet

        paint_pet(self, painter)

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


def _pet_service():
    """宠物共享服务（内核），拿不到就返回 None（不阻塞渲染）。"""
    try:
        from core.services.pet_service import PetService

        return PetService.instance()
    except Exception:                              # noqa: BLE001
        return None


class PetWindow(PetEventMixin, PetMenuMixin, QWidget):
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

        self.bubble = SuggestionCard(None, theme=theme, i18n=i18n, embedded=False)
        self.bubble.setParent(None)            # 强制成为顶层窗口（内部构造可能又挂回父级）
        self.bubble.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        try:
            self._remember_pet_anchor()
        except Exception:                      # noqa: BLE001
            pass
        self.bubble.accepted.connect(self._on_accepted)
        self.bubble.ignored.connect(self._on_ignored)
        self.bubble.setVisible(False)
        # 气泡是独立顶层窗口，不加入桌宠窗口布局（否则会被裁切/挤动宠物）
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

    def set_pet_size(self, size: int) -> None:
        """只缩放画布；窗口尺寸交给布局（窗口里还有纠错气泡，写死尺寸会把它裁掉）。"""
        factor = max(0.4, min(4.0, float(size) / 64.0))
        side = max(16, int(64 * factor))
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.setFixedSize(side, side)
            canvas.update()
            canvas.repaint()
        self.adjustSize()
        self._reflow_keeping_pet()          # 按气泡大小重排，同时保持宠物位置不动
        self.update()

    def set_services(self, skins=None, theme=None) -> None:
        if skins is not None:
            self._skins = skins
        if theme is not None:
            self._theme = theme
            self.bubble._theme = theme
        self.canvas.set_services(skins, theme)
        self.canvas.setFixedSize(self.width(), self.height())   # 画布跟随窗口
        try:
            from core.services.pet_service import PetService

            self.set_pet_size(PetService.instance().size)       # 皮肤基准会重置，这里恢复尺寸
        except Exception:                      # noqa: BLE001
            pass
        self._reflow_keeping_pet()

    # ---------------- 气泡 ----------------
    def has_pending_suggestion(self) -> bool:
        return self._pending_suggestion is not None

    def set_suggestion(self, original, corrected, start=-1, end=-1) -> None:
        """收到校对结果：无差异则记 idle，有差异则记 error 并按配置弹出气泡。"""
        anchor = self._canvas_global_pos()          # 显示气泡前的宠物位置快照
        if not original or not corrected or original.strip() == corrected.strip():
            self._pending_suggestion = None
            self.bubble.clear_diff()
            self.bubble.setVisible(False)
            self._bubble_anchor = anchor
            self._after_bubble_change()
            return
        previous = self._pending_suggestion
        self._pending_suggestion = (original, corrected, start, end)
        canvas = getattr(self, "canvas", None)
        if canvas is not None and self._pending_suggestion != previous:
            canvas.notify_suggestion()
        self.bubble.show_suggestion(original, corrected, None, self.config,
                                    start=start, end=end)
        if not self.config.get("auto_show_suggestion", True):
            self.bubble.setVisible(False)
        self._bubble_anchor = anchor
        self._after_bubble_change()

    def _discard_suggestion(self) -> None:
        """建议被采纳/忽略后清现场：不清的话旧建议会"复活"并按陈旧偏移改写正文。"""
        self._pending_suggestion = None
        self.bubble.clear_diff()
        self._bubble_anchor = self._canvas_global_pos()
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
        anchor = getattr(self, "_bubble_anchor", None)
        self._bubble_anchor = None
        """气泡显隐后重新布局，让**桌宠本体**在屏幕上的位置保持不变。

        单一事实源是「宠物矩形」（`_pet_anchor`）：布局变化后用"宠物应该在哪"反推窗口左上角，
        而不是像旧实现那样补偿"上一次的窗口底边"——后者会随每次补偿累积误差，
        表现为显隐几次之后宠物慢慢漂移。

        另外整段重排**关闭重绘**后再一次性打开：分层窗口（半透明）在"先按新尺寸画一帧、再 move"
        的中间态会被用户看成"闪一下"，用一个几何变更代替两次重绘即可消掉。
        """
        self._place_bubble()          # 窗口不再为气泡改变尺寸：既不裁切也不位移
        self.update()
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

    def _canvas_global_pos(self):
        """画布左上角的全局坐标（气泡伸缩前后用它判断宠物是否移动）。"""
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return None
        return canvas.mapToGlobal(canvas.rect().topLeft())

    def _reflow_keeping_pet(self, anchor=None) -> None:
        """重新布局，并让**宠物本身**停在原处（用全局坐标快照，锚点不会过期）。"""
        if anchor is None:
            anchor = self._canvas_global_pos()
        self.adjustSize()
        layout = self.layout()
        if layout is not None:
            layout.activate()
        layout = self.layout()
        if layout is not None:
            layout.activate()
        current = self._canvas_global_pos()
        if anchor is not None and current is not None:
            delta = anchor - current
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

    def _report_geometry(self) -> None:
        """把当前位置/尺寸上报给共享服务，供其它插件（如参考文献悬浮窗）锚定。"""
        service = _pet_service()
        if service is None:
            return
        try:
            service.update_geometry(self.x(), self.y(), self.width())
        except Exception:                      # noqa: BLE001
            pass

    def moveEvent(self, event) -> None:        # noqa: N802 - Qt 命名惯例
        super().moveEvent(event)
        self._report_geometry()

    def resizeEvent(self, event) -> None:      # noqa: N802
        super().resizeEvent(event)
        self._report_geometry()

    def showEvent(self, event) -> None:        # noqa: N802
        super().showEvent(event)
        self._report_geometry()

    def hideEvent(self, event) -> None:        # noqa: N802
        super().hideEvent(event)
        self._report_geometry()
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

    _proofread_on: bool = True        # 默认在校对状态，插件启动后会同步真实值

    def set_proofread_enabled(self, enabled: bool) -> None:
        """由校对插件把真实状态推过来（菜单按它显示「暂停校对/继续校对」）。"""
        self._proofread_on = bool(enabled)

    def _proofread_enabled(self) -> bool:
        """当前是否在"校对中"（右键菜单据此显示勾选状态）。"""
        try:
            provider = getattr(self, "plugin_provider", None)
            if callable(provider):
                target = provider("proofread")
                if target is not None and hasattr(target, "is_enabled"):
                    return bool(target.is_enabled())
        except Exception:                      # noqa: BLE001
            pass
        return bool(getattr(self, "_proofread_on", True))

    def _switch_skin(self, skin_id: str) -> None:
        if self._skins is None:
            return
        self._skins.set_skin(skin_id)
        self.set_services(skins=self._skins)
        # 皮肤切换会按 skin.json 的 64px 重设窗口；这里按内核里记住的尺寸再套一次，
        # 否则"切一次皮肤，尺寸就回到默认"（用户反馈的第 3 个问题）。
        try:
            from core.services.pet_service import PetService

            self.set_pet_size(PetService.instance().size)
        except Exception:                      # noqa: BLE001
            pass

    # ---------------- 建议回调 ----------------
    def _on_accepted(self, corrected, start, end, original) -> None:
        if self._correction_handler is not None:
            # G1：把生成建议时的原句一起交出去 —— 否则写回无法判断"这段文字是否已被改动"，
            # 在 A 得到的建议可能把 B 文档的内容覆盖掉。
            self._correction_handler(corrected, start=start, end=end, original_text=original)
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
        visible = True                      # 显隐由内核 PetService 决定
        self.setVisible(visible)
