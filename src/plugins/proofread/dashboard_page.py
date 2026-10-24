# plugins/proofread/dashboard_page.py
"""「智能纠错」插件页：主区只放校对状态，其余进右侧「次要界面」抽屉。

由主窗口内置仪表盘下沉而来（P9 解耦）：
- 页面随插件注册/注销，禁用插件后侧边栏入口随之消失；
- 状态区用「状态点 + 文字 + Switch」表达，不再用大号文字标题；
- ADR-002：**校对参数 / 缓存清洗 / API 监控**属于次要界面，经
  `ctx.ui.add_side_panel(...)` 注册到内容区右侧的可折叠抽屉，默认收起；
  主区只留状态与开关，主次分离。

词条来源：通用词条直接用内核语言包（app.proof.* / app.cleanup.* / monitor.*），
本页专属词条在插件语言包（proofread.dashboard.*）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)
from core.services.api_logger import clear_api_log
from core.widgets import MonitorPanel, Switch

if TYPE_CHECKING:
    from plugins.proofread.plugin import ProofreadPlugin


class DashboardPage(QWidget):
    """智能纠错页。构造入参是 ProofreadPlugin 实例（页面生命周期短于插件）。"""

    def __init__(self, plugin: "ProofreadPlugin", parent=None) -> None:
        super().__init__(parent)
        self._plugin = plugin
        self._ctx = plugin.ctx
        self._config = self._ctx.get_service("config")
        self._i18n = self._ctx.get_service("i18n")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        page = QWidget()
        scroll.setWidget(page)
        columns = QVBoxLayout(page)
        columns.setContentsMargins(24, 20, 24, 20)
        columns.setSpacing(14)

        # 主区只放状态卡（参数/清洗/监控已下沉到右侧抽屉，见 build_secondary_panel）
        columns.addWidget(self._build_status_card())
        columns.addStretch(1)

        # 初始状态 + 外部开关（桌宠/热键）同步
        self.proof_switch.setChecked(self._plugin.is_enabled())
        self._sync_state(self._plugin.is_enabled())
        self._plugin.subscribe("proofread.toggled",
                               lambda _e, on: self._sync_state(bool(on)))

        self.retranslateUi()

    # ---------------- 词条 ----------------
    def tr(self, key: str, /, **params) -> str:
        """插件命名空间词条（proofread.*）。"""
        return self._ctx.tr(key, **params)

    def _app_tr(self, key: str, /, **params) -> str:
        """内核通用词条（app.* / monitor.*）。"""
        if self._i18n is None:
            return key
        return self._i18n.tr(key, **params)

    # ---------------- 卡片骨架 ----------------
    def _card(self) -> tuple[QWidget, QVBoxLayout]:
        card = QWidget()
        card.setObjectName("contentCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        return card, lay

    def _card_title(self, lay: QVBoxLayout) -> QLabel:
        label = QLabel()
        label.setObjectName("cardTitle")
        lay.addWidget(label)
        return label

    # ---------------- 左列：校对状态 ----------------
    def _build_status_card(self) -> QWidget:
        card, lay = self._card()
        self.status_card_title = self._card_title(lay)

        # 状态行：状态点 + 文字（左） / Switch（右）
        row = QHBoxLayout()
        row.setSpacing(8)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("proofStatusDot")
        row.addWidget(self.status_dot)
        self.status_text = QLabel()
        self.status_text.setObjectName("proofStatusText")
        row.addWidget(self.status_text)
        row.addStretch(1)
        self.proof_switch = Switch()
        self._apply_switch_colors()
        self.proof_switch.toggled.connect(self._on_switch_toggled)
        row.addWidget(self.proof_switch)
        lay.addLayout(row)

        # 桌宠颜色说明（次要文本）
        self.pet_hint = QLabel()
        self.pet_hint.setObjectName("hintLabel")
        self.pet_hint.setWordWrap(True)
        lay.addWidget(self.pet_hint)
        return card

    # ---------------- 次要界面（右侧抽屉） ----------------
    def build_secondary_panel(self) -> QWidget:
        """校对参数 + 缓存清洗 + API 监控；由外壳放进右侧抽屉，默认收起。

        幂等：外壳按插件缓存一次，重复调用返回同一控件。
        """
        existing = getattr(self, "_secondary_panel", None)
        if existing is not None:
            return existing
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(self._build_params_card())
        lay.addWidget(self._build_cleanup_card())
        lay.addWidget(self._build_monitor_card())
        lay.addStretch(1)
        self._secondary_panel = panel
        self.retranslateUi()
        return panel

    def _build_params_card(self) -> QWidget:
        card, lay = self._card()
        self.params_card_title = self._card_title(lay)
        params = QFormLayout()
        params.setSpacing(10)
        params.setHorizontalSpacing(16)
        debounce_row = QHBoxLayout()
        self.debounce_slider = QSlider(Qt.Horizontal)
        self.debounce_slider.setRange(200, 5000)
        self.debounce_slider.setValue(int(self._config.get("debounce_ms", 500)))
        self.debounce_value = QLabel(f"{self.debounce_slider.value()} ms")
        self.debounce_slider.valueChanged.connect(self._on_debounce_changed)
        debounce_row.addWidget(self.debounce_slider, 1)
        debounce_row.addWidget(self.debounce_value)
        self.debounce_label = QLabel()
        params.addRow(self.debounce_label, debounce_row)

        self.context_spin = QSpinBox()
        self.context_spin.setRange(0, 5)
        self.context_spin.setValue(int(self._config.get("context_before", 0)))
        self.context_spin.valueChanged.connect(self._on_context_changed)
        self.context_label = QLabel()
        params.addRow(self.context_label, self.context_spin)
        lay.addLayout(params)
        return card

    def _apply_switch_colors(self) -> None:
        theme = self._ctx.get_service("theme")
        def token(key: str, default: str) -> str:
            return theme.token(key, default) if theme is not None else default
        self.proof_switch.set_colors(token("color.accent", "#0078d4"),
                                     token("color.border.strong", "#c8c8c8"),
                                     token("color.on_accent", "#ffffff"))

    # ---------------- 左列：缓存清洗 ----------------
    def _build_cleanup_card(self) -> QWidget:
        card, lay = self._card()
        self.cleanup_card_title = self._card_title(lay)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.cleanup_ignore_btn = QPushButton()
        self.cleanup_log_btn = QPushButton()
        self.cleanup_all_btn = QPushButton()
        self.cleanup_ignore_btn.clicked.connect(self.clear_ignore_only)
        self.cleanup_log_btn.clicked.connect(self.clear_api_log_only)
        self.cleanup_all_btn.clicked.connect(self.clear_all_cache)
        for btn in (self.cleanup_ignore_btn, self.cleanup_log_btn, self.cleanup_all_btn):
            row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        return card

    # ---------------- 右列：API 监控 ----------------
    def _build_monitor_card(self) -> QWidget:
        card, lay = self._card()
        self.monitor_card_title = self._card_title(lay)
        self.monitor_widget = MonitorPanel(i18n=self._i18n)
        lay.addWidget(self.monitor_widget, 1)
        return card

    # ---------------- 状态同步 ----------------
    def _on_switch_toggled(self, on: bool) -> None:
        self._plugin.set_enabled(on)

    def _sync_state(self, on: bool) -> None:
        """只更新外观，不回环触发 toggled。"""
        self.proof_switch.blockSignals(True)
        self.proof_switch.setChecked(on)
        self.proof_switch.blockSignals(False)
        for widget in (self.status_dot, self.status_text):
            widget.setProperty("state", "ok" if on else "off")
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.status_text.setText(self._app_tr("app.proof.on") if on
                                 else self._app_tr("app.proof.off"))

    # ---------------- 参数 ----------------
    def _on_debounce_changed(self, value: int) -> None:
        self._config.set("debounce_ms", value)
        self.debounce_value.setText(f"{value} ms")

    def _on_context_changed(self, value: int) -> None:
        self._config.set("context_before", value)
        self._config.set("context_after", value)

    # ---------------- 缓存清洗 ----------------
    def _notify_done(self, body_key: str, status_key: str) -> None:
        QMessageBox.information(self, self._app_tr("app.cleanup.title_done"),
                                self._app_tr(body_key))
        self._ctx.ui.status(self._app_tr(status_key), 3000)

    def clear_ignore_only(self) -> None:
        ignore_mgr = self._ctx.get_service("ignore")
        if ignore_mgr is not None:
            ignore_mgr.clear_ignore()
        self._notify_done("app.cleanup.done_ignore", "app.cleanup.status_ignore")

    def clear_api_log_only(self) -> None:
        clear_api_log()
        self._notify_done("app.cleanup.done_log", "app.cleanup.status_log")

    def clear_all_cache(self) -> None:
        ignore_mgr = self._ctx.get_service("ignore")
        if ignore_mgr is not None:
            ignore_mgr.clear_ignore()
        clear_api_log()
        self._notify_done("app.cleanup.done_all", "app.cleanup.status_all")

    # ---------------- 多语言 / 主题 ----------------
    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.status_card_title.setText(self._app_tr("app.proof.group"))
        self.pet_hint.setText(self.tr("dashboard.pet_hint"))
        self.proof_switch.setToolTip(self.tr("dashboard.toggle_tip"))
        # 以下控件在次要面板里，面板未构造时跳过
        if getattr(self, "params_card_title", None) is not None:
            self.params_card_title.setText(self.tr("dashboard.params"))
            self.debounce_label.setText(self._app_tr("app.proof.debounce_label"))
            self.context_label.setText(self._app_tr("app.proof.context_label"))
            self.cleanup_card_title.setText(self._app_tr("app.cleanup.group"))
            self.cleanup_ignore_btn.setText(self._app_tr("app.cleanup.ignore_short"))
            self.cleanup_log_btn.setText(self._app_tr("app.cleanup.log_short"))
            self.cleanup_all_btn.setText(self._app_tr("app.cleanup.all_short"))
            self.monitor_card_title.setText(self._app_tr("app.nav.monitor"))
            self.monitor_widget.retranslateUi()
        self._sync_state(self._plugin.is_enabled())

    def refresh_theme(self) -> None:
        """主题切换后 Switch 配色随令牌更新。"""
        self._apply_switch_colors()
