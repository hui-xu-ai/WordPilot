# settings_dialog.py
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


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, theme=None):
        super().__init__(parent)
        self.config = config
        self.theme = theme
        self.setWindowTitle("设置")
        self.resize(550, 500)

        self.init_ui()
        self.load_settings()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # ---------- API 页 ----------
        api_page = QWidget()
        api_layout = QFormLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("输入 DeepSeek API Key")
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("如 deepseek-v4-flash")
        api_layout.addRow("API Key:", self.api_key_edit)
        api_layout.addRow("模型:", self.model_edit)
# --修改16 增加模型温度和token限制        
        self.temp_edit = QDoubleSpinBox()
        self.temp_edit.setRange(0, 2.0)
        self.temp_edit.setSingleStep(0.1)
        self.temp_edit.setValue(self.config.get("temperature", 0.4))
        api_layout.addRow("温度:", self.temp_edit)

        self.max_tokens_edit = QSpinBox()
        self.max_tokens_edit.setRange(1, 8192)
        self.max_tokens_edit.setValue(self.config.get("max_tokens", 2000))
        api_layout.addRow("最大Token:", self.max_tokens_edit)        
# --修改16 增加模型温度和token限制  

        # 注：校对缓存已改为 SQLite 主键查询（core/services/proof_cache.py），
        # 原「缓存查询条数」设置（线性扫日志末尾 N 行）随之废弃。

        self.test_btn = QPushButton("🔗 测试连接")
        self.test_btn.clicked.connect(self.test_connection)
        api_layout.addRow(self.test_btn)
        self.test_status = QLabel("")
        api_layout.addRow(self.test_status)
        api_page.setLayout(api_layout)
        tabs.addTab(api_page, "API")

        # ---------- 提示词页 ----------
        prompt_page = QWidget()
        prompt_layout = QVBoxLayout()
        # 顶部警告标签（新增）        #  --修改18
        warning_label = QLabel(
            "⚠️ 请勿在提示词中要求修改 [IMG0]、[EQ1] 等占位符，"
            "它们在原文中代表公式/图片，校对时会自动保留。"
        )
        warning_label.setWordWrap(True)
        warning_label.setObjectName("warningLabel")   # 样式见主题 base.qss
        prompt_layout.addWidget(warning_label)  
 #  --修改18
        
        prompt_layout.addWidget(QLabel("校对提示词，{text}代表原文占位符，禁止修改"))
        self.proof_prompt_edit = QTextEdit()
        self.proof_prompt_edit.setPlaceholderText(
            "你是中文科技论文校对专家。修正错别字、语病、标点、专业词汇拼写。校对所有单位的空格：数字与℃、%、°之间不加空格（例：2℃、5%、9°）；数字与其他单位（如m）之间加半角空格（例：10 m）。\n原文：{text}"
        )
        prompt_layout.addWidget(self.proof_prompt_edit)

        prompt_layout.addWidget(QLabel("翻译提示词，{text}代表原文占位符，{target_lang}代表翻译结果占位符，禁止修改"))
        self.translate_prompt_edit = QTextEdit()
        self.translate_prompt_edit.setPlaceholderText(
            "将以下文本翻译为{target_lang}，只输出译文：\n{text}"
        )
        prompt_layout.addWidget(self.translate_prompt_edit)

        prompt_layout.addWidget(QLabel("改写提示词，{style}代表改写风格要求，{text}代表改写结果占位符，禁止修改"))
        self.rewrite_prompt_edit = QTextEdit()
        self.rewrite_prompt_edit.setPlaceholderText(
            "按{style}风格改写以下文本，只输出改写后的内容：\n{text}"
        )
        prompt_layout.addWidget(self.rewrite_prompt_edit)
        prompt_page.setLayout(prompt_layout)
        tabs.addTab(prompt_page, "提示词")

        # ---------- 界面页 ----------
        ui_page = QWidget()
        ui_layout = QVBoxLayout()

        # 动态窗口样式组
        dynamic_group = QGroupBox("动态纠错窗口样式")
        dynamic_layout = QFormLayout()

        self.show_floating_btn = QCheckBox("启用悬浮按钮")
        dynamic_layout.addRow(self.show_floating_btn)

        self.auto_show_check = QCheckBox("检测到建议自动弹出")
        dynamic_layout.addRow(self.auto_show_check)

        # 配色已统一由主题令牌控制（见「外观」页），此处不再单独调色

        # 透明度滑动条
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)   # 30% ~ 100%
        dynamic_layout.addRow("窗口透明度:", self.opacity_slider)

        # 字体大小
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 36)
        dynamic_layout.addRow("字体大小:", self.font_size_spin)

        dynamic_group.setLayout(dynamic_layout)
        ui_layout.addWidget(dynamic_group)
        ui_layout.addStretch()
        ui_page.setLayout(ui_layout)
        tabs.addTab(ui_page, "界面")

        # ---------- 外观页（主题 / 自定义 CSS） ----------
        appearance_page = QWidget()
        appearance_layout = QVBoxLayout()

        appearance_group = QGroupBox("主题")
        appearance_form = QFormLayout()

        self.theme_combo = QComboBox()
        self._theme_names = self.theme.available() if self.theme is not None else ["light"]
        for name in self._theme_names:
            self.theme_combo.addItem({"light": "浅色", "dark": "深色"}.get(name, name), name)
        appearance_form.addRow("配色方案:", self.theme_combo)

        appearance_group.setLayout(appearance_form)
        appearance_layout.addWidget(appearance_group)

        custom_group = QGroupBox("自定义样式")
        custom_layout = QVBoxLayout()
        hint = QLabel(
            f"编辑 {USER_QSS} 可覆盖内置样式，保存后自动生效（无需重启）。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("hintLabel")
        custom_layout.addWidget(hint)

        open_qss_btn = QPushButton("打开自定义样式文件")
        open_qss_btn.clicked.connect(self.open_user_qss)
        custom_layout.addWidget(open_qss_btn)
        custom_group.setLayout(custom_layout)
        appearance_layout.addWidget(custom_group)
        appearance_layout.addStretch()
        appearance_page.setLayout(appearance_layout)
        tabs.addTab(appearance_page, "外观")

        main_layout.addWidget(tabs)

        # 底部按钮
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.save_settings)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        main_layout.addLayout(btn_layout)

    def open_user_qss(self):
        """用系统默认程序打开用户自定义样式文件。"""
        if not USER_QSS.exists():
            QMessageBox.information(self, "提示", f"尚未生成 {USER_QSS}，重启程序后会自动创建。")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(USER_QSS))          # noqa: S606 - 用户主动打开自己的配置文件
            else:
                subprocess.Popen(["xdg-open", str(USER_QSS)])
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def load_settings(self):
        self.api_key_edit.setText(self.config.get_api_key())
        self.model_edit.setText(self.config.get("model", "deepseek-v4-flash"))
#--修改16        
        self.temp_edit.setValue(self.config.get("temperature", 0.4))
        self.max_tokens_edit.setValue(self.config.get("max_tokens", 2000))        
#--修改16                  
        self.proof_prompt_edit.setPlainText(
            self.config.get("proof_prompt",
                "你是中文科技论文校对专家。修正错别字、语病、标点、专业词汇拼写。校对所有单位的空格：数字与℃、%、°之间不加空格（例：2℃、5%、9°）；数字与其他单位（如m）之间加半角空格（例：10 m）。\n原文：{text}")
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

    def save_settings(self):
        self.config.set_api_key(self.api_key_edit.text().strip())
        self.config.set("model", self.model_edit.text())
#--修改16           
        self.config.set("temperature", self.temp_edit.value())
        self.config.set("max_tokens", self.max_tokens_edit.value())        
#--修改16   
        
        self.config.set("proof_prompt", self.proof_prompt_edit.toPlainText())
        self.config.set("translate_prompt", self.translate_prompt_edit.toPlainText())
        self.config.set("rewrite_prompt", self.rewrite_prompt_edit.toPlainText())

        # 动态窗口样式（配色改由主题令牌统一控制）
        self.config.set("show_floating_button", self.show_floating_btn.isChecked())
        self.config.set("auto_show_suggestion", self.auto_show_check.isChecked())
        self.config.set("suggestion_opacity", self.opacity_slider.value() / 100.0)
        self.config.set("suggestion_font_size", self.font_size_spin.value())

        # 主题：保存到配置并立即应用（服务会广播 theme.changed）
        if self.theme is not None:
            selected = self.theme_combo.currentData()
            if selected and selected != self.theme.current:
                self.theme.set_theme(selected)
                self.theme_changed = True

        self.accept()

    def test_connection(self):
        key = self.api_key_edit.text().strip()
        model = self.model_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "提示", "请先输入 API Key")
            return

        self.test_btn.setEnabled(False)
        self.test_status.setText("正在测试...")
        client = DeepSeekClient({"api_key": key, "model": model})
        try:
            ok = client.test_connection()
            self.test_status.setText("✅ 连接成功" if ok else "❌ 连接失败")
        except Exception as e:
            self.test_status.setText(f"❌ 异常：{e}")
        self.test_btn.setEnabled(True)