# plugins/image_toolbox/plugin.py
"""图片工具箱插件。

按页面版心批量裁剪并居中 Word 中的图片。

GUI 重设计后，本插件从「模态对话框」改为「主界面导航页」：
经 ctx.ui.add_page 直接嵌入内容区，点左侧图标轨直达，
不再弹出独立窗口；页面控件常驻，卸载插件时才释放隐藏工作文档。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from core.sdk.plugin_base import PluginBase
from core.services.config_manager import CONFIG_DIR
from plugins.image_toolbox.image_reshaper_widget import ImageReshaperWidget

if TYPE_CHECKING:
    from core.sdk.context import PluginContext

CONFIG_FILE = CONFIG_DIR / "Image.docx"


class _MissingNotice(QWidget):
    """工作文档缺失时的占位页（实现 retranslateUi，切语言时一起刷新）。"""

    def __init__(self, tr) -> None:
        super().__init__()
        from PySide6.QtWidgets import QLabel, QVBoxLayout

        self._tr = tr
        layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setObjectName("hintLabel")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.retranslateUi()

    def retranslateUi(self) -> None:        # noqa: N802 - Qt 命名惯例
        self.label.setText(self._tr("error.config_missing_body", path=CONFIG_FILE))


class ImageToolboxPlugin(PluginBase):
    def assets(self):
        """图片工具箱的用户资产：可以无损保存图片的 `Image.docx` 工作文档。"""
        return [CONFIG_FILE]

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        self._widget: ImageReshaperWidget | None = None
        # 传 i18n key 而非已翻译文案：导航条目在切换语言时现场查词重建
        ctx.ui.add_page("page_label", self._build_page, order=20,
                        icon="image", plugin_id="image_toolbox")
        ctx.logger.info("图片工具箱插件已激活")

    def deactivate(self) -> None:
        # 页面销毁前释放隐藏 Word 工作文档
        if self._widget is not None:
            try:
                self._widget.cleanup()
            except Exception:
                pass
            self._widget = None
        super().deactivate()

    def _build_page(self) -> QWidget:
        # 先确保工作文档存在（安装包自带模板 → 直接复制，不需要 Word），
        # 只有真的没法创建时才提示用户；否则会"先报错，创建代码永远不执行"。
        from plugins.image_toolbox.word_image_processor import ensure_workspace_file

        ensure_workspace_file(CONFIG_FILE)
        if not CONFIG_FILE.exists():
            return _MissingNotice(self.tr)
        self._widget = ImageReshaperWidget(config_dir=str(CONFIG_DIR),
                                           theme=self.ctx.theme,
                                           i18n=self.ctx.get_service("i18n"))
        return self._widget
