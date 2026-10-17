import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (QWidget, QGridLayout, QLabel, QComboBox,
                               QSpinBox, QDoubleSpinBox, QPushButton,
                               QTextEdit, QGroupBox, QHBoxLayout,
                               QVBoxLayout, QColorDialog, QFrame,
                               QMessageBox)

from plugins.image_toolbox.word_image_processor import WordImageProcessor

PLUGIN_ID = "image_toolbox"


def _format_bytes(size):
    if size is None:
        return "未知"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class ColorButton(QPushButton):
    def __init__(self, default_color=(255, 255, 255), i18n=None):
        super().__init__()
        self._i18n = i18n
        self._color = QColor(*default_color)
        self.clicked.connect(self._choose_color)
        self.setFixedSize(40, 24)
        self._update_style()

    def tr(self, key, /, **params):
        if self._i18n is None:
            return key
        return self._i18n.tr(f"{PLUGIN_ID}.{key}", **params)

    def _choose_color(self):
        init = QColor(*self.rgb) if self._color.isValid() else QColor(255, 255, 255)
        color = QColorDialog.getColor(initial=init, title=self.tr("dialog.color_title"))
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
    """图片裁剪/居中面板。

    多语言：下拉框的显示文本会随语言变化，因此逻辑判断一律走 itemData 里
    的稳定键（none/crop/center、pct/px），绝不依赖显示文本。
    """

    def __init__(self, parent=None, config_dir=None, theme=None, i18n=None):
        super().__init__(parent)
        self._theme = theme
        self._i18n = i18n
        self.config_dir = config_dir or os.path.join(os.path.dirname(__file__), "config")
        os.makedirs(self.config_dir, exist_ok=True)
        self.processor = None
        self._init_processor()
        self._setup_ui()
        # 样式统一由主题（base.qss）提供；这里只标记主操作按钮
        self.apply_btn.setObjectName("apply_btn")
        self.apply_btn.setProperty("accent", True)

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self._i18n is None:
            return key
        return self._i18n.tr(f"{PLUGIN_ID}.{key}", **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.title_label.setText(self.tr("widget.title"))
        self.width_group.setTitle(self.tr("group.width"))
        self.height_group.setTitle(self.tr("group.height"))
        self.bg_group.setTitle(self.tr("group.background"))
        self.width_mode_label.setText(self.tr("label.mode"))
        self.width_margin_label.setText(self.tr("label.margin"))
        self.height_mode_label.setText(self.tr("label.mode"))
        self.height_margin_label.setText(self.tr("label.margin"))
        self.bg_color_label.setText(self.tr("label.bg_color"))
        self.apply_btn.setText(self.tr("btn.apply"))
        self.info_text.setPlaceholderText(self.tr("info.placeholder"))

        for combo, keys in ((self.width_mode_combo, ("mode.none", "mode.crop", "mode.center")),
                            (self.height_mode_combo, ("mode.none", "mode.crop", "mode.center")),
                            (self.width_unit_combo, ("unit.percent", "unit.pixel")),
                            (self.height_unit_combo, ("unit.percent", "unit.pixel"))):
            for index, key in enumerate(keys):
                combo.setItemText(index, self.tr(key))

        self.width_px_spin.setSuffix(self.tr("suffix.pixel"))
        self.height_px_spin.setSuffix(self.tr("suffix.pixel"))

    # ---------------- 初始化 ----------------
    def _init_processor(self):
        try:
            self.processor = WordImageProcessor(self.config_dir)
        except Exception as e:
            QMessageBox.warning(
                self, self.tr("error.init_title"),
                self.tr("error.init_body", error=e)
            )
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

        self.title_label = QLabel(self.tr("widget.title"))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        main_layout.addWidget(self.title_label)

        # ----- 宽度设置 -----
        self.width_group = QGroupBox(self.tr("group.width"))
        width_layout = QGridLayout(self.width_group)

        self.width_mode_combo = QComboBox()
        for key, data in (("mode.none", "none"), ("mode.crop", "crop"), ("mode.center", "center")):
            self.width_mode_combo.addItem(self.tr(key), data)
        self.width_mode_combo.setCurrentIndex(2)          # 默认居中

        # 边缘距离：单位 + 数值
        self.width_unit_combo = QComboBox()
        self.width_unit_combo.addItem(self.tr("unit.percent"), "pct")
        self.width_unit_combo.addItem(self.tr("unit.pixel"), "px")
        self.width_unit_combo.setCurrentIndex(0)          # 默认百分比

        self.width_px_spin = QSpinBox()
        self.width_px_spin.setRange(0, 10000)
        self.width_px_spin.setValue(4)                      #  默认像素边缘间距
        self.width_px_spin.setSuffix(self.tr("suffix.pixel"))
        self.width_px_spin.setVisible(False)               # 初始为百分比模式

        self.width_pct_spin = QDoubleSpinBox()
        self.width_pct_spin.setRange(0.00, 100.00)
        self.width_pct_spin.setSingleStep(0.01)
        self.width_pct_spin.setDecimals(2)
        self.width_pct_spin.setValue(0.50)                  #  默认百分比边缘间距
        self.width_pct_spin.setSuffix(" %")
        self.width_pct_spin.setVisible(True)

        self.width_unit_combo.currentIndexChanged.connect(self._on_width_unit_changed)

        self.width_mode_label = QLabel(self.tr("label.mode"))
        self.width_margin_label = QLabel(self.tr("label.margin"))
        width_layout.addWidget(self.width_mode_label, 0, 0)
        width_layout.addWidget(self.width_mode_combo, 0, 1)
        width_layout.addWidget(self.width_margin_label, 0, 2)
        # 单位组合框
        width_layout.addWidget(self.width_unit_combo, 0, 3)
        # 像素输入框
        width_layout.addWidget(self.width_px_spin, 0, 4)
        # 百分比输入框
        width_layout.addWidget(self.width_pct_spin, 0, 5)

        main_layout.addWidget(self.width_group)

        # ----- 高度设置 -----
        self.height_group = QGroupBox(self.tr("group.height"))
        height_layout = QGridLayout(self.height_group)

        self.height_mode_combo = QComboBox()
        for key, data in (("mode.none", "none"), ("mode.crop", "crop"), ("mode.center", "center")):
            self.height_mode_combo.addItem(self.tr(key), data)
        self.height_mode_combo.setCurrentIndex(1)          # 默认裁剪

        self.height_unit_combo = QComboBox()
        self.height_unit_combo.addItem(self.tr("unit.percent"), "pct")
        self.height_unit_combo.addItem(self.tr("unit.pixel"), "px")
        self.height_unit_combo.setCurrentIndex(0)

        self.height_px_spin = QSpinBox()
        self.height_px_spin.setRange(0, 10000)
        self.height_px_spin.setValue(4)             #  默认像素边缘间距
        self.height_px_spin.setSuffix(self.tr("suffix.pixel"))
        self.height_px_spin.setVisible(False)

        self.height_pct_spin = QDoubleSpinBox()
        self.height_pct_spin.setRange(0.00, 100.00)
        self.height_pct_spin.setSingleStep(0.01)
        self.height_pct_spin.setDecimals(2)
        self.height_pct_spin.setValue(0.50)             #  默认百分比边缘间距
        self.height_pct_spin.setSuffix(" %")
        self.height_pct_spin.setVisible(True)

        self.height_unit_combo.currentIndexChanged.connect(self._on_height_unit_changed)

        self.height_mode_label = QLabel(self.tr("label.mode"))
        self.height_margin_label = QLabel(self.tr("label.margin"))
        height_layout.addWidget(self.height_mode_label, 0, 0)
        height_layout.addWidget(self.height_mode_combo, 0, 1)
        height_layout.addWidget(self.height_margin_label, 0, 2)
        height_layout.addWidget(self.height_unit_combo, 0, 3)
        height_layout.addWidget(self.height_px_spin, 0, 4)
        height_layout.addWidget(self.height_pct_spin, 0, 5)

        main_layout.addWidget(self.height_group)

        # 仅在裁剪模式下启用边缘距离控件（判断走 itemData，不受语言影响）
        self.width_mode_combo.currentIndexChanged.connect(
            lambda _i: self._set_width_margin_enabled(self.width_mode_combo.currentData() == "crop"))
        self.height_mode_combo.currentIndexChanged.connect(
            lambda _i: self._set_height_margin_enabled(self.height_mode_combo.currentData() == "crop"))

        # 初始状态：宽度居中（禁用），高度裁剪（启用）
        self._set_width_margin_enabled(False)
        self._set_height_margin_enabled(True)

        # ----- 背景色 -----
        self.bg_group = QGroupBox(self.tr("group.background"))
        bg_layout = QHBoxLayout(self.bg_group)
        self.bg_color_btn = ColorButton(default_color=(255, 255, 255), i18n=self._i18n)
        self.bg_color_label = QLabel(self.tr("label.bg_color"))
        bg_layout.addWidget(self.bg_color_label)
        bg_layout.addWidget(self.bg_color_btn)
        bg_layout.addStretch()
        main_layout.addWidget(self.bg_group)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(line)

        # ----- 应用按钮 -----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.apply_btn = QPushButton(self.tr("btn.apply"))
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
        self.info_text.setPlaceholderText(self.tr("info.placeholder"))
        main_layout.addWidget(self.info_text)

    def _on_width_unit_changed(self, index):
        """切换宽度单位时，显示对应的输入框"""
        self._apply_unit(self.width_unit_combo, self.width_px_spin, self.width_pct_spin)

    def _on_height_unit_changed(self, index):
        self._apply_unit(self.height_unit_combo, self.height_px_spin, self.height_pct_spin)

    @staticmethod
    def _apply_unit(unit_combo, px_spin, pct_spin):
        use_percent = unit_combo.currentData() == "pct"
        px_spin.setVisible(not use_percent)
        pct_spin.setVisible(use_percent)

    def _set_width_margin_enabled(self, enabled):
        self.width_unit_combo.setEnabled(enabled)
        self.width_px_spin.setEnabled(enabled)
        self.width_pct_spin.setEnabled(enabled)

    def _set_height_margin_enabled(self, enabled):
        self.height_unit_combo.setEnabled(enabled)
        self.height_px_spin.setEnabled(enabled)
        self.height_pct_spin.setEnabled(enabled)

    def log(self, msg, error=False):
        # HTML 富文本不受 QSS 管辖，颜色取自主题令牌（无主题服务时用兜底色）
        theme = getattr(self, "_theme", None)
        token = "color.state.error" if error else "color.state.ok"
        fallback = "#c42b1c" if error else "#107c10"
        color = theme.token(token, fallback) if theme is not None else fallback
        self.info_text.append(f'<span style="color:{color};">{msg}</span>')

    def on_apply(self):
        if not self.processor:
            self.log(self.tr("error.not_initialized"), error=True)
            return

        width_mode = self.width_mode_combo.currentData()
        height_mode = self.height_mode_combo.currentData()

        # 读取边缘距离及其单位
        if width_mode == "crop":
            if self.width_unit_combo.currentData() == "pct":
                margin_w = self.width_pct_spin.value()
                margin_w_unit = 'pct'
            else:
                margin_w = self.width_px_spin.value()
                margin_w_unit = 'px'
        else:
            margin_w = 0
            margin_w_unit = 'px'

        if height_mode == "crop":
            if self.height_unit_combo.currentData() == "pct":
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
        self.log(self.tr("status.processing"))
        self.repaint()

        try:
            success, msg, orig_bytes, new_bytes = self.processor.process_selected(
                width_mode, height_mode,
                margin_w, margin_h, bg_color,
                margin_w_unit, margin_h_unit)
            if success:
                self.log(msg)
                self.log(self.tr("status.orig_size", size=_format_bytes(orig_bytes)))
                self.log(self.tr("status.new_size", size=_format_bytes(new_bytes)))
            else:
                self.log(msg, error=True)
                if orig_bytes is not None:
                    self.log(self.tr("status.extracted_size", size=_format_bytes(orig_bytes)))
        except Exception as e:
            self.log(self.tr("error.unexpected", error=e), error=True)
        finally:
            self.apply_btn.setEnabled(True)
