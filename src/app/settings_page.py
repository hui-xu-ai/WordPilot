# settings_page.py
"""设置页（主窗口内置页面，取代原先的模态对话框）。

形态：左侧子导航（API / 提示词 / 界面 / 外观）+ 右侧内容堆叠，
底部一条「保存」栏——点保存立即写配置并发 saved 信号，
后续动作（重建 AI 客户端、热键重绑、语言/皮肤热切换）由主窗口统一处理。

- 所有文案走 i18n（构造时可传入 i18n 服务）；
- 「界面语言」与「提示词语言」是两个独立配置：
  前者只影响 UI，后者决定送 AI 的提示词语种，互不影响。
"""
import os
import subprocess
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QTextEdit, QSpinBox, QStackedWidget, QWidget,
    QMessageBox, QSlider, QGroupBox, QCheckBox, QComboBox, QDoubleSpinBox,
    QListWidget, QScrollArea,
)

from core.services.api_client import DeepSeekClient
from core.services.theme_service import USER_QSS

#: 界面语言与提示词语言共用的可选语种
PROMPT_LANGUAGE_NAMES = {
    "zh_CN": "中文",
    "en_US": "English",
}

_NAV_KEYS = ("settings.tab.api", "settings.tab.prompt",
             "settings.tab.ui", "settings.tab.appearance")


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, config, parent=None, theme=None, i18n=None, skins=None):
        super().__init__(parent)
        self.config = config
        self.theme = theme
        self.i18n = i18n
        self.skins = skins

        self.init_ui()
        self.load_settings()
        self.retranslateUi()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self.i18n is None:
            return key
        return self.i18n.tr(key, **params)

    def prompt_tr(self, key: str) -> str:
        """默认提示词按提示词语言取，与界面语言解耦。"""
        if self.i18n is None:
            return key
        return self.i18n.prompt_tr(key)

    # ---------------- 界面 ----------------
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 16)
        main_layout.setSpacing(14)

        # 页头
        self.title_label = QLabel()
        self.title_label.setObjectName("pageTitle")
        main_layout.addWidget(self.title_label)

        body = QHBoxLayout()
        body.setSpacing(16)

        # 左侧子导航
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("settingsNav")
        self.nav_list.setFixedWidth(132)
        for _ in _NAV_KEYS:
            self.nav_list.addItem("")
        self.nav_list.currentRowChanged.connect(self._switch_section)
        body.addWidget(self.nav_list)

        # 右侧内容（可滚动，小窗口也不挤爆）
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_api_section())
        self.stack.addWidget(self._build_prompt_section())
        self.stack.addWidget(self._build_ui_section())
        self.stack.addWidget(self._build_appearance_section())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.stack)
        body.addWidget(scroll, 1)
        main_layout.addLayout(body, 1)

        self.nav_list.setCurrentRow(0)

        # 底部保存栏
        bottom = QHBoxLayout()
        self.save_status = QLabel("")
        self.save_status.setObjectName("hintLabel")
        bottom.addWidget(self.save_status, 1)
        self.save_btn = QPushButton()
        self.save_btn.setProperty("accent", True)
        self.save_btn.clicked.connect(self.save_settings)
        bottom.addWidget(self.save_btn)
        main_layout.addLayout(bottom)

    def _switch_section(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    def _card(self) -> tuple[QWidget, QVBoxLayout]:
        """一张通栏卡片（样式见 base.qss #contentCard）。"""
        card = QWidget()
        card.setObjectName("contentCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        return card, lay

    # ---------- 各分区 ----------
    def _build_api_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        card, form_host = self._card()
        form = QFormLayout()
        form.setSpacing(10)
        self.api_key_edit = QLineEdit()
        self.model_edit = QLineEdit()
        self.label_api_key = QLabel()
        self.label_model = QLabel()
        form.addRow(self.label_api_key, self.api_key_edit)
        form.addRow(self.label_model, self.model_edit)

        self.temp_edit = QDoubleSpinBox()
        self.temp_edit.setRange(0, 2.0)
        self.temp_edit.setSingleStep(0.1)
        self.label_temperature = QLabel()
        form.addRow(self.label_temperature, self.temp_edit)

        self.max_tokens_edit = QSpinBox()
        self.max_tokens_edit.setRange(1, 8192)
        self.label_max_tokens = QLabel()
        form.addRow(self.label_max_tokens, self.max_tokens_edit)
        form_host.addLayout(form)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton()
        self.test_btn.clicked.connect(self.test_connection)
        test_row.addWidget(self.test_btn)
        self.test_status = QLabel("")
        self.test_status.setObjectName("hintLabel")
        test_row.addWidget(self.test_status, 1)
        form_host.addLayout(test_row)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_prompt_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        card, lay = self._card()
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setObjectName("warningLabel")
        lay.addWidget(self.warning_label)

        lang_row = QHBoxLayout()
        self.label_prompt_language = QLabel()
        self.prompt_language_combo = QComboBox()
        for code, name in PROMPT_LANGUAGE_NAMES.items():
            self.prompt_language_combo.addItem(name, code)
        lang_row.addWidget(self.label_prompt_language)
        lang_row.addWidget(self.prompt_language_combo, 1)
        lay.addLayout(lang_row)

        self.label_proof_prompt = QLabel()
        self.label_proof_prompt.setWordWrap(True)
        lay.addWidget(self.label_proof_prompt)
        self.proof_prompt_edit = QTextEdit()
        self.proof_prompt_edit.setMinimumHeight(90)
        lay.addWidget(self.proof_prompt_edit)

        self.label_translate_prompt = QLabel()
        self.label_translate_prompt.setWordWrap(True)
        lay.addWidget(self.label_translate_prompt)
        self.translate_prompt_edit = QTextEdit()
        self.translate_prompt_edit.setMinimumHeight(70)
        lay.addWidget(self.translate_prompt_edit)

        self.label_rewrite_prompt = QLabel()
        self.label_rewrite_prompt.setWordWrap(True)
        lay.addWidget(self.label_rewrite_prompt)
        self.rewrite_prompt_edit = QTextEdit()
        self.rewrite_prompt_edit.setMinimumHeight(70)
        lay.addWidget(self.rewrite_prompt_edit)
        layout.addWidget(card)
        return page

    def _build_ui_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        card, lay = self._card()
        self.dynamic_group_title = QLabel()
        self.dynamic_group_title.setObjectName("cardTitle")
        lay.addWidget(self.dynamic_group_title)

        self.show_floating_btn = QCheckBox()
        lay.addWidget(self.show_floating_btn)
        self.auto_show_check = QCheckBox()
        lay.addWidget(self.auto_show_check)

        form = QFormLayout()
        form.setSpacing(10)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.label_opacity = QLabel()
        form.addRow(self.label_opacity, self.opacity_slider)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 36)
        self.label_font_size = QLabel()
        form.addRow(self.label_font_size, self.font_size_spin)
        lay.addLayout(form)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_appearance_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        card, lay = self._card()
        self.theme_group_title = QLabel()
        self.theme_group_title.setObjectName("cardTitle")
        lay.addWidget(self.theme_group_title)

        form = QFormLayout()
        form.setSpacing(10)
        self.theme_combo = QComboBox()
        self._theme_names = self.theme.available() if self.theme is not None else ["light"]
        for name in self._theme_names:
            self.theme_combo.addItem(name, name)   # 文本由 retranslateUi 填
        self.label_theme = QLabel()
        form.addRow(self.label_theme, self.theme_combo)

        self.ui_language_combo = QComboBox()
        if self.i18n is not None:
            for code in self.i18n.available():
                self.ui_language_combo.addItem(self.i18n.display_name(code), code)
        self.label_ui_language = QLabel()
        form.addRow(self.label_ui_language, self.ui_language_combo)

        self.skin_combo = QComboBox()
        if self.skins is not None:
            for item in self.skins.available():
                self.skin_combo.addItem(item["name"], item["id"])
        self.label_skin = QLabel()
        form.addRow(self.label_skin, self.skin_combo)
        lay.addLayout(form)

        self.skin_hint = QLabel()
        self.skin_hint.setWordWrap(True)
        self.skin_hint.setObjectName("hintLabel")
        lay.addWidget(self.skin_hint)
        layout.addWidget(card)

        card2, lay2 = self._card()
        self.custom_group_title = QLabel()
        self.custom_group_title.setObjectName("cardTitle")
        lay2.addWidget(self.custom_group_title)
        self.qss_hint = QLabel()
        self.qss_hint.setWordWrap(True)
        self.qss_hint.setObjectName("hintLabel")
        lay2.addWidget(self.qss_hint)
        self.open_qss_btn = QPushButton()
        self.open_qss_btn.clicked.connect(self.open_user_qss)
        lay2.addWidget(self.open_qss_btn, 0, Qt.AlignLeft)
        layout.addWidget(card2)
        layout.addStretch(1)
        return page

    # ---------------- 多语言（热切换） ----------------
    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """语言切换后刷新本页的全部可见文案。"""
        self.title_label.setText(self.tr("settings.title"))
        for index, key in enumerate(_NAV_KEYS):
            item = self.nav_list.item(index)
            if item is not None:
                item.setText(self.tr(key))

        self.label_api_key.setText(self.tr("settings.api.key"))
        self.label_model.setText(self.tr("settings.api.model"))
        self.label_temperature.setText(self.tr("settings.api.temperature"))
        self.label_max_tokens.setText(self.tr("settings.api.max_tokens"))
        self.api_key_edit.setPlaceholderText(self.tr("settings.api.key_placeholder"))
        self.model_edit.setPlaceholderText(self.tr("settings.api.model_placeholder"))
        self.test_btn.setText(self.tr("settings.api.test"))

        self.warning_label.setText(self.tr("settings.prompt.warning"))
        self.label_prompt_language.setText(self.tr("settings.prompt.language"))
        self.label_proof_prompt.setText(self.tr("settings.prompt.proof"))
        self.label_translate_prompt.setText(self.tr("settings.prompt.translate"))
        self.label_rewrite_prompt.setText(self.tr("settings.prompt.rewrite"))
        self.proof_prompt_edit.setPlaceholderText(
            self.tr("settings.prompt.proof_placeholder"))

        self.dynamic_group_title.setText(self.tr("settings.ui.floating_group"))
        self.show_floating_btn.setText(self.tr("settings.ui.enable_floating"))
        self.auto_show_check.setText(self.tr("settings.ui.auto_show"))
        self.label_opacity.setText(self.tr("settings.ui.opacity"))
        self.label_font_size.setText(self.tr("settings.ui.font_size"))

        self.theme_group_title.setText(self.tr("settings.appearance.theme_group"))
        self.label_theme.setText(self.tr("settings.appearance.theme"))
        self.label_ui_language.setText(self.tr("settings.appearance.language"))
        self.label_skin.setText(self.tr("settings.appearance.skin"))
        self.skin_hint.setText(self.tr("settings.appearance.skin_hint"))
        for index in range(self.theme_combo.count()):
            self.theme_combo.setItemText(index, self.tr(f"theme.{self.theme_combo.itemData(index)}"))
        self.custom_group_title.setText(self.tr("settings.appearance.custom_group"))
        self.qss_hint.setText(self.tr("settings.appearance.custom_hint", path=USER_QSS))
        self.open_qss_btn.setText(self.tr("settings.appearance.open_qss"))

        self.save_btn.setText(self.tr("settings.btn.save"))

    # ---------------- 数据 ----------------
    def open_user_qss(self):
        """用系统默认程序打开用户自定义样式文件。"""
        if not USER_QSS.exists():
            QMessageBox.information(self, self.tr("settings.appearance.qss_missing_title"),
                                    self.tr("settings.appearance.qss_missing", path=USER_QSS))
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(USER_QSS))          # noqa: S606 - 用户主动打开自己的配置文件
            else:
                subprocess.Popen(["xdg-open", str(USER_QSS)])
        except Exception as exc:
            QMessageBox.warning(self, self.tr("settings.appearance.open_fail_title"), str(exc))

    def load_settings(self):
        self.api_key_edit.setText(self.config.get_api_key())
        self.model_edit.setText(self.config.get("model", "deepseek-v4-flash"))
        self.temp_edit.setValue(self.config.get("temperature", 0.4))
        self.max_tokens_edit.setValue(self.config.get("max_tokens", 2000))
        self.proof_prompt_edit.setPlainText(
            self.config.get("proof_prompt",
                            self.tr("settings.prompt.proof_placeholder"))
        )
        # 默认提示词按「提示词语言」取，与界面语言无关
        self.translate_prompt_edit.setPlainText(
            self.config.get("translate_prompt",
                            self.prompt_tr("prompt.translate.default"))
        )
        self.rewrite_prompt_edit.setPlainText(
            self.config.get("rewrite_prompt",
                            self.prompt_tr("prompt.rewrite.default"))
        )
        self.show_floating_btn.setChecked(self.config.get("show_floating_button", True))
        self.auto_show_check.setChecked(self.config.get("auto_show_suggestion", True))
        self.opacity_slider.setValue(int(self.config.get("suggestion_opacity", 0.95) * 100))
        self.font_size_spin.setValue(self.config.get("suggestion_font_size", 12))
        if self.theme is not None:
            self._select_data(self.theme_combo, self.theme.current)
        self._select_data(self.ui_language_combo,
                          self.i18n.current if self.i18n is not None else "zh_CN")
        self._select_data(self.prompt_language_combo, self.config.get("prompt_language", "zh_CN"))
        self._select_data(self.skin_combo,
                          self.skins.current_id if self.skins is not None else "default")

    @staticmethod
    def _select_data(combo: QComboBox, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save_settings(self):
        self.config.set_api_key(self.api_key_edit.text().strip())
        self.config.set("model", self.model_edit.text())
        self.config.set("temperature", self.temp_edit.value())
        self.config.set("max_tokens", self.max_tokens_edit.value())

        self.config.set("proof_prompt", self.proof_prompt_edit.toPlainText())
        self.config.set("translate_prompt", self.translate_prompt_edit.toPlainText())
        self.config.set("rewrite_prompt", self.rewrite_prompt_edit.toPlainText())

        # 动态窗口样式（配色改由主题令牌统一控制）
        self.config.set("show_floating_button", self.show_floating_btn.isChecked())
        self.config.set("auto_show_suggestion", self.auto_show_check.isChecked())
        self.config.set("suggestion_opacity", self.opacity_slider.value() / 100.0)
        self.config.set("suggestion_font_size", self.font_size_spin.value())

        # 提示词语言（与界面语言解耦，由调用方决定是否重建客户端）
        self.config.set("prompt_language", self.prompt_language_combo.currentData())

        # 界面语言：保存后由主窗口拉起全局重翻译
        self.config.set("ui_language", self.ui_language_combo.currentData())

        # 桌宠皮肤：写入配置，由主窗口调用 kernel.set_skin 广播 skin.changed
        if self.skins is not None and self.skin_combo.currentData():
            self.config.set("skin", self.skin_combo.currentData())

        # 主题：保存到配置并立即应用（服务会广播 theme.changed）
        if self.theme is not None:
            selected = self.theme_combo.currentData()
            if selected and selected != self.theme.current:
                self.theme.set_theme(selected)

        self.save_status.setText(self.tr("settings.saved_hint"))
        self.saved.emit()

    def test_connection(self):
        key = self.api_key_edit.text().strip()
        model = self.model_edit.text().strip()
        if not key:
            QMessageBox.warning(self, self.tr("settings.title"),
                                self.tr("settings.api.test_empty"))
            return

        self.test_btn.setEnabled(False)
        self.test_status.setText(self.tr("settings.api.testing"))
        client = DeepSeekClient({"api_key": key, "model": model})
        try:
            ok = client.test_connection()
            self.test_status.setText(self.tr("settings.api.test_ok") if ok
                                     else self.tr("settings.api.test_fail"))
        except Exception as e:
            self.test_status.setText(self.tr("settings.api.test_fail") + f"：{e}")
        self.test_btn.setEnabled(True)
