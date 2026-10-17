# settings_dialog.py
"""设置对话框。

- 所有文案走 i18n（构造时可传入 i18n 服务）；
- 「界面语言」与「提示词语言」是两个独立配置：
  前者只影响 UI，后者决定送 AI 的提示词语种，互不影响。
"""
import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QTextEdit, QSpinBox, QTabWidget, QWidget,
    QMessageBox, QSlider, QGroupBox, QCheckBox, QComboBox, QDoubleSpinBox
)

from core.services.api_client import DeepSeekClient
from core.services.theme_service import USER_QSS

#: 界面语言与提示词语言共用的可选语种
PROMPT_LANGUAGE_NAMES = {
    "zh_CN": "中文",
    "en_US": "English",
}


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, theme=None, i18n=None):
        super().__init__(parent)
        self.config = config
        self.theme = theme
        self.i18n = i18n
        self.setWindowTitle(self.tr("settings.title"))
        self.resize(560, 540)

        self.init_ui()
        self.load_settings()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self.i18n is None:
            return key
        return self.i18n.tr(key, **params)

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        self.tabs = QTabWidget()

        # ---------- API 页 ----------
        api_page = QWidget()
        api_layout = QFormLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText(self.tr("settings.api.key_placeholder"))
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText(self.tr("settings.api.model_placeholder"))
        self.label_api_key = QLabel(self.tr("settings.api.key"))
        self.label_model = QLabel(self.tr("settings.api.model"))
        api_layout.addRow(self.label_api_key, self.api_key_edit)
        api_layout.addRow(self.label_model, self.model_edit)

        self.temp_edit = QDoubleSpinBox()
        self.temp_edit.setRange(0, 2.0)
        self.temp_edit.setSingleStep(0.1)
        self.temp_edit.setValue(self.config.get("temperature", 0.4))
        self.label_temperature = QLabel(self.tr("settings.api.temperature"))
        api_layout.addRow(self.label_temperature, self.temp_edit)

        self.max_tokens_edit = QSpinBox()
        self.max_tokens_edit.setRange(1, 8192)
        self.max_tokens_edit.setValue(self.config.get("max_tokens", 2000))
        self.label_max_tokens = QLabel(self.tr("settings.api.max_tokens"))
        api_layout.addRow(self.label_max_tokens, self.max_tokens_edit)

        self.test_btn = QPushButton(self.tr("settings.api.test"))
        self.test_btn.clicked.connect(self.test_connection)
        api_layout.addRow(self.test_btn)
        self.test_status = QLabel("")
        api_layout.addRow(self.test_status)
        api_page.setLayout(api_layout)
        self.tabs.addTab(api_page, self.tr("settings.tab.api"))

        # ---------- 提示词页 ----------
        prompt_page = QWidget()
        prompt_layout = QVBoxLayout()

        self.warning_label = QLabel(self.tr("settings.prompt.warning"))
        self.warning_label.setWordWrap(True)
        self.warning_label.setObjectName("warningLabel")   # 样式见主题 base.qss
        prompt_layout.addWidget(self.warning_label)

        # 提示词语言：与界面语言解耦，决定送 AI 的提示词语种
        prompt_lang_layout = QHBoxLayout()
        self.label_prompt_language = QLabel(self.tr("settings.prompt.language"))
        self.prompt_language_combo = QComboBox()
        for code, name in PROMPT_LANGUAGE_NAMES.items():
            self.prompt_language_combo.addItem(name, code)
        prompt_lang_layout.addWidget(self.label_prompt_language)
        prompt_lang_layout.addWidget(self.prompt_language_combo)
        prompt_lang_layout.addStretch()
        prompt_layout.addLayout(prompt_lang_layout)

        self.label_proof_prompt = QLabel(self.tr("settings.prompt.proof"))
        self.label_proof_prompt.setWordWrap(True)
        prompt_layout.addWidget(self.label_proof_prompt)
        self.proof_prompt_edit = QTextEdit()
        self.proof_prompt_edit.setPlaceholderText(
            self.tr("settings.prompt.proof_placeholder"))
        prompt_layout.addWidget(self.proof_prompt_edit)

        self.label_translate_prompt = QLabel(self.tr("settings.prompt.translate"))
        self.label_translate_prompt.setWordWrap(True)
        prompt_layout.addWidget(self.label_translate_prompt)
        self.translate_prompt_edit = QTextEdit()
        self.translate_prompt_edit.setPlaceholderText(
            "将以下文本翻译为{target_lang}，只输出译文：\n{text}")
        prompt_layout.addWidget(self.translate_prompt_edit)

        self.label_rewrite_prompt = QLabel(self.tr("settings.prompt.rewrite"))
        self.label_rewrite_prompt.setWordWrap(True)
        prompt_layout.addWidget(self.label_rewrite_prompt)
        self.rewrite_prompt_edit = QTextEdit()
        self.rewrite_prompt_edit.setPlaceholderText(
            "按{style}风格改写以下文本，只输出改写后的内容：\n{text}")
        prompt_layout.addWidget(self.rewrite_prompt_edit)
        prompt_page.setLayout(prompt_layout)
        self.tabs.addTab(prompt_page, self.tr("settings.tab.prompt"))

        # ---------- 界面页 ----------
        ui_page = QWidget()
        ui_layout = QVBoxLayout()

        self.dynamic_group = QGroupBox(self.tr("settings.ui.floating_group"))
        dynamic_layout = QFormLayout()

        self.show_floating_btn = QCheckBox(self.tr("settings.ui.enable_floating"))
        dynamic_layout.addRow(self.show_floating_btn)

        self.auto_show_check = QCheckBox(self.tr("settings.ui.auto_show"))
        dynamic_layout.addRow(self.auto_show_check)

        # 配色已统一由主题令牌控制（见「外观」页），此处不再单独调色

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)   # 30% ~ 100%
        self.label_opacity = QLabel(self.tr("settings.ui.opacity"))
        dynamic_layout.addRow(self.label_opacity, self.opacity_slider)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 36)
        self.label_font_size = QLabel(self.tr("settings.ui.font_size"))
        dynamic_layout.addRow(self.label_font_size, self.font_size_spin)

        self.dynamic_group.setLayout(dynamic_layout)
        ui_layout.addWidget(self.dynamic_group)
        ui_layout.addStretch()
        ui_page.setLayout(ui_layout)
        self.tabs.addTab(ui_page, self.tr("settings.tab.ui"))

        # ---------- 外观页（主题 / 界面语言 / 自定义 CSS） ----------
        appearance_page = QWidget()
        appearance_layout = QVBoxLayout()

        self.theme_group = QGroupBox(self.tr("settings.appearance.theme_group"))
        appearance_form = QFormLayout()

        self.theme_combo = QComboBox()
        self._theme_names = self.theme.available() if self.theme is not None else ["light"]
        for name in self._theme_names:
            self.theme_combo.addItem(self.tr(f"theme.{name}"), name)
        self.label_theme = QLabel(self.tr("settings.appearance.theme"))
        appearance_form.addRow(self.label_theme, self.theme_combo)

        # 界面语言：与提示词语言相互独立
        self.ui_language_combo = QComboBox()
        if self.i18n is not None:
            for code in self.i18n.available():
                self.ui_language_combo.addItem(self.i18n.display_name(code), code)
        self.label_ui_language = QLabel(self.tr("settings.appearance.language"))
        appearance_form.addRow(self.label_ui_language, self.ui_language_combo)

        self.theme_group.setLayout(appearance_form)
        appearance_layout.addWidget(self.theme_group)

        self.custom_group = QGroupBox(self.tr("settings.appearance.custom_group"))
        custom_layout = QVBoxLayout()
        self.qss_hint = QLabel(self.tr("settings.appearance.custom_hint", path=USER_QSS))
        self.qss_hint.setWordWrap(True)
        self.qss_hint.setObjectName("hintLabel")
        custom_layout.addWidget(self.qss_hint)

        self.open_qss_btn = QPushButton(self.tr("settings.appearance.open_qss"))
        self.open_qss_btn.clicked.connect(self.open_user_qss)
        custom_layout.addWidget(self.open_qss_btn)
        self.custom_group.setLayout(custom_layout)
        appearance_layout.addWidget(self.custom_group)
        appearance_layout.addStretch()
        appearance_page.setLayout(appearance_layout)
        self.tabs.addTab(appearance_page, self.tr("settings.tab.appearance"))

        main_layout.addWidget(self.tabs)

        # 底部按钮
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton(self.tr("settings.btn.save"))
        self.save_btn.setProperty("accent", True)
        self.save_btn.clicked.connect(self.save_settings)
        self.cancel_btn = QPushButton(self.tr("settings.btn.cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.cancel_btn)
        main_layout.addLayout(btn_layout)

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
        self.translate_prompt_edit.setPlainText(
            self.config.get("translate_prompt",
                            "将以下文本翻译为{target_lang}，只输出译文：\n{text}")
        )
        self.rewrite_prompt_edit.setPlainText(
            self.config.get("rewrite_prompt",
                            "按{style}风格改写以下文本，只输出改写后的内容：\n{text}")
        )
        self.show_floating_btn.setChecked(self.config.get("show_floating_button", True))
        self.auto_show_check.setChecked(self.config.get("auto_show_suggestion", True))
        self.opacity_slider.setValue(int(self.config.get("suggestion_opacity", 0.95) * 100))
        self.font_size_spin.setValue(self.config.get("suggestion_font_size", 12))
        if self.theme is not None:
            index = self.theme_combo.findData(self.theme.current)
            if index >= 0:
                self.theme_combo.setCurrentIndex(index)
        self._select_data(self.ui_language_combo,
                          self.i18n.current if self.i18n is not None else "zh_CN")
        self._select_data(self.prompt_language_combo, self.config.get("prompt_language", "zh_CN"))

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

        # 界面语言：保存后会由主窗口拉起全局重翻译
        self.config.set("ui_language", self.ui_language_combo.currentData())

        # 主题：保存到配置并立即应用（服务会广播 theme.changed）
        if self.theme is not None:
            selected = self.theme_combo.currentData()
            if selected and selected != self.theme.current:
                self.theme.set_theme(selected)

        self.accept()

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
