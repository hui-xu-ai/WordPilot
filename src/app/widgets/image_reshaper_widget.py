import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (QWidget, QGridLayout, QLabel, QComboBox,
                               QSpinBox, QDoubleSpinBox, QPushButton,
                               QTextEdit, QGroupBox, QHBoxLayout,
                               QVBoxLayout, QColorDialog, QFrame,
                               QMessageBox)

from plugins.image_toolbox.word_image_processor import WordImageProcessor


def _format_bytes(size):
    if size is None:
        return "未知"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class ColorButton(QPushButton):
    def __init__(self, default_color=(255, 255, 255)):
        super().__init__()
        self._color = QColor(*default_color)
        self.clicked.connect(self._choose_color)
        self.setFixedSize(40, 24)
        self._update_style()

    def _choose_color(self):
        init = QColor(*self.rgb) if self._color.isValid() else QColor(255, 255, 255)
        color = QColorDialog.getColor(initial=init, title="选择背景色")
        if color.isValid():
            self._color = color
            self._update_style()

    @property
    def rgb(self):
        return (self._color.red(), self._color.green(), self._color.blue())

    def _update_style(self):
        self.setStyleSheet(
            f"background-color: rgb({self._color.red()},{self._color.green()},{self._color.blue()});"
            f"border: 1px solid gray; border-radius: 4px;"
        )


class ImageReshaperWidget(QWidget):
    def __init__(self, parent=None, config_dir=None):
        super().__init__(parent)
        self.config_dir = config_dir or os.path.join(os.path.dirname(__file__), "config")
        os.makedirs(self.config_dir, exist_ok=True)
        self.processor = None
        self._init_processor()
        self._setup_ui()
        self._apply_stylesheet()

    def _init_processor(self):
        try:
            self.processor = WordImageProcessor(self.config_dir)
        except Exception as e:
            QMessageBox.warning(self, "初始化失败",
                                f"无法初始化图片处理器：{e}\n请确保 Word 已运行且 Image.docx 存在。")
            self.processor = None

    def cleanup(self):
        """释放工作文档，应在对话框关闭后调用"""
        if self.processor:
            self.processor.close()
            self.processor = None

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        title = QLabel("Word 图片裁剪与居中工具")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        main_layout.addWidget(title)

        # ----- 宽度设置 -----
        width_group = QGroupBox("宽度方向设置")
        width_layout = QGridLayout(width_group)

        self.width_mode_combo = QComboBox()
        self.width_mode_combo.addItems(["不操作", "裁剪", "居中"])
        self.width_mode_combo.setCurrentIndex(2)          # 默认居中

        # 边缘距离：单位 + 数值
        self.width_unit_combo = QComboBox()
        self.width_unit_combo.addItems(["百分比", "像素"])
        self.width_unit_combo.setCurrentIndex(0)          # 默认百分比

        self.width_px_spin = QSpinBox()
        self.width_px_spin.setRange(0, 10000)
        self.width_px_spin.setValue(4)                      #  默认像素边缘间距
        self.width_px_spin.setSuffix(" 像素")
        self.width_px_spin.setVisible(False)               # 初始为百分比模式

        self.width_pct_spin = QDoubleSpinBox()
        self.width_pct_spin.setRange(0.00, 100.00)
        self.width_pct_spin.setSingleStep(0.01)
        self.width_pct_spin.setDecimals(2)
        self.width_pct_spin.setValue(0.50)                  #  默认百分比边缘间距
        self.width_pct_spin.setSuffix(" %")
        self.width_pct_spin.setVisible(True)

        self.width_unit_combo.currentIndexChanged.connect(self._on_width_unit_changed)

        width_layout.addWidget(QLabel("操作模式："), 0, 0)
        width_layout.addWidget(self.width_mode_combo, 0, 1)
        width_layout.addWidget(QLabel("边缘距离："), 0, 2)
        # 单位组合框
        width_layout.addWidget(self.width_unit_combo, 0, 3)
        # 像素输入框
        width_layout.addWidget(self.width_px_spin, 0, 4)
        # 百分比输入框
        width_layout.addWidget(self.width_pct_spin, 0, 5)

        main_layout.addWidget(width_group)

        # ----- 高度设置 -----
        height_group = QGroupBox("高度方向设置")
        height_layout = QGridLayout(height_group)

        self.height_mode_combo = QComboBox()
        self.height_mode_combo.addItems(["不操作", "裁剪", "居中"])
        self.height_mode_combo.setCurrentIndex(1)          # 默认裁剪

        self.height_unit_combo = QComboBox()
        self.height_unit_combo.addItems(["百分比", "像素"])
        self.height_unit_combo.setCurrentIndex(0)

        self.height_px_spin = QSpinBox()
        self.height_px_spin.setRange(0, 10000)
        self.height_px_spin.setValue(4)             #  默认像素边缘间距
        self.height_px_spin.setSuffix(" 像素")
        self.height_px_spin.setVisible(False)

        self.height_pct_spin = QDoubleSpinBox()
        self.height_pct_spin.setRange(0.00, 100.00)
        self.height_pct_spin.setSingleStep(0.01)
        self.height_pct_spin.setDecimals(2)
        self.height_pct_spin.setValue(0.50)             #  默认百分比边缘间距
        self.height_pct_spin.setSuffix(" %")
        self.height_pct_spin.setVisible(True)

        self.height_unit_combo.currentIndexChanged.connect(self._on_height_unit_changed)

        height_layout.addWidget(QLabel("操作模式："), 0, 0)
        height_layout.addWidget(self.height_mode_combo, 0, 1)
        height_layout.addWidget(QLabel("边缘距离："), 0, 2)
        height_layout.addWidget(self.height_unit_combo, 0, 3)
        height_layout.addWidget(self.height_px_spin, 0, 4)
        height_layout.addWidget(self.height_pct_spin, 0, 5)

        main_layout.addWidget(height_group)

        # 仅在裁剪模式下启用边缘距离控件
        self.width_mode_combo.currentTextChanged.connect(
            lambda t: self._set_width_margin_enabled(t == "裁剪"))
        self.height_mode_combo.currentTextChanged.connect(
            lambda t: self._set_height_margin_enabled(t == "裁剪"))

        # 初始状态：宽度居中（禁用），高度裁剪（启用）
        self._set_width_margin_enabled(False)
        self._set_height_margin_enabled(True)

        # ----- 背景色 -----
        bg_group = QGroupBox("背景颜色（用于空白检测与填充）")
        bg_layout = QHBoxLayout(bg_group)
        self.bg_color_btn = ColorButton(default_color=(255, 255, 255))
        bg_layout.addWidget(QLabel("当前背景色："))
        bg_layout.addWidget(self.bg_color_btn)
        bg_layout.addStretch()
        main_layout.addWidget(bg_group)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(line)

        # ----- 应用按钮 -----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setMinimumWidth(120)
        self.apply_btn.setMinimumHeight(30)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.clicked.connect(self.on_apply)
        btn_layout.addWidget(self.apply_btn)
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        # ----- 信息输出 -----
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setMaximumHeight(150)
        self.info_text.setPlaceholderText("操作信息将显示在此处...")
        main_layout.addWidget(self.info_text)

    def _on_width_unit_changed(self, index):
        """切换宽度单位时，显示对应的输入框"""
        if self.width_unit_combo.currentText() == "百分比":
            self.width_px_spin.setVisible(False)
            self.width_pct_spin.setVisible(True)
        else:
            self.width_px_spin.setVisible(True)
            self.width_pct_spin.setVisible(False)

    def _on_height_unit_changed(self, index):
        if self.height_unit_combo.currentText() == "百分比":
            self.height_px_spin.setVisible(False)
            self.height_pct_spin.setVisible(True)
        else:
            self.height_px_spin.setVisible(True)
            self.height_pct_spin.setVisible(False)

    def _set_width_margin_enabled(self, enabled):
        self.width_unit_combo.setEnabled(enabled)
        self.width_px_spin.setEnabled(enabled)
        self.width_pct_spin.setEnabled(enabled)

    def _set_height_margin_enabled(self, enabled):
        self.height_unit_combo.setEnabled(enabled)
        self.height_px_spin.setEnabled(enabled)
        self.height_pct_spin.setEnabled(enabled)

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold; border: 1px solid #a0a0a0;
                border-radius: 6px; margin-top: 12px; padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
            }
            QComboBox, QSpinBox, QDoubleSpinBox { min-height: 24px; }
            QPushButton#apply_btn {
                background-color: #0078d4; color: white; border: none;
                border-radius: 4px; font-weight: bold;
            }
            QPushButton#apply_btn:hover { background-color: #106ebe; }
            QPushButton#apply_btn:pressed { background-color: #005a9e; }
            QPushButton#apply_btn:disabled {
                background-color: #c0c0c0; color: #606060;
            }
            QTextEdit { border: 1px solid #b0b0b0; border-radius: 4px; }
        """)
        self.apply_btn.setObjectName("apply_btn")

    def log(self, msg, error=False):
        color = "red" if error else "#2e7d32"
        self.info_text.append(f'<span style="color:{color};">{msg}</span>')

    def on_apply(self):
        if not self.processor:
            self.log("处理器未初始化，无法操作", error=True)
            return

        mode_map = {"不操作": "none", "裁剪": "crop", "居中": "center"}
        width_mode = mode_map[self.width_mode_combo.currentText()]
        height_mode = mode_map[self.height_mode_combo.currentText()]

        # 读取边缘距离及其单位
        if width_mode == "crop":
            if self.width_unit_combo.currentText() == "百分比":
                margin_w = self.width_pct_spin.value()
                margin_w_unit = 'pct'
            else:
                margin_w = self.width_px_spin.value()
                margin_w_unit = 'px'
        else:
            margin_w = 0
            margin_w_unit = 'px'

        if height_mode == "crop":
            if self.height_unit_combo.currentText() == "百分比":
                margin_h = self.height_pct_spin.value()
                margin_h_unit = 'pct'
            else:
                margin_h = self.height_px_spin.value()
                margin_h_unit = 'px'
        else:
            margin_h = 0
            margin_h_unit = 'px'

        bg_color = self.bg_color_btn.rgb

        self.apply_btn.setEnabled(False)
        self.info_text.clear()
        self.log("处理中，请稍候...")
        self.repaint()

        try:
            success, msg, orig_bytes, new_bytes = self.processor.process_selected(
                width_mode, height_mode,
                margin_w, margin_h, bg_color,
                margin_w_unit, margin_h_unit)
            if success:
                self.log(msg)
                self.log(f"原始图片大小：{_format_bytes(orig_bytes)}")
                self.log(f"处理后大小：{_format_bytes(new_bytes)}")
            else:
                self.log(msg, error=True)
                if orig_bytes is not None:
                    self.log(f"提取图片大小：{_format_bytes(orig_bytes)}")
        except Exception as e:
            self.log(f"意外错误: {e}", error=True)
        finally:
            self.apply_btn.setEnabled(True)