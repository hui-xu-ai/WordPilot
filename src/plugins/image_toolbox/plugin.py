# plugins/image_toolbox/plugin.py
"""图片工具箱插件。

按页面版心批量裁剪并居中 Word 中的图片。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog, QMessageBox, QVBoxLayout

from core.sdk.plugin_base import PluginBase
from core.services.config_manager import CONFIG_DIR
from core.services.window_manager import Mode
from plugins.image_toolbox.image_reshaper_widget import ImageReshaperWidget

if TYPE_CHECKING:
    from core.sdk.context import PluginContext

CONFIG_FILE = CONFIG_DIR / "Image.docx"
WINDOW_ID = "image_toolbox.main"


class ImageToolboxPlugin(PluginBase):
    """图片工具箱：模态对话框，关闭后立即释放文档。"""

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        ctx.ui.add_tool("🖼️ 图片工具箱", self.open, order=20, plugin_id="image_toolbox")
        ctx.ui.add_tray_action("图片工具箱", self.open, order=20, plugin_id="image_toolbox")
        ctx.logger.info("图片工具箱插件已激活")

    def deactivate(self) -> None:
        super().deactivate()

    def open(self) -> None:
        # 检查配置文件是否存在
        if not CONFIG_FILE.exists():
            QMessageBox.critical(
                None, "配置缺失",
                f"找不到必要文件：{CONFIG_FILE}\n请确保 config 文件夹包含 Image.docx。"
            )
            return

        windows = self.ctx.windows
        if windows is None:
            self.ctx.logger.error("窗口服务不可用，无法打开图片工具箱")
            return

        holder = {}

        def _factory() -> QDialog:
            dialog = QDialog()
            dialog.setWindowTitle("图片工具箱")
            dialog.setMinimumSize(420, 480)
            dialog.resize(420, 480)
            layout = QVBoxLayout(dialog)
            tool = ImageReshaperWidget(config_dir=str(CONFIG_DIR))
            layout.addWidget(tool)
            dialog.setLayout(layout)
            holder["tool"] = tool
            return dialog

        windows.open(WINDOW_ID, _factory, owner="image_toolbox",
                     mode=Mode.MODAL, remember_geometry=True)
        # 对话框关闭后立即清理，确保文档释放
        tool = holder.get("tool")
        if tool is not None:
            tool.cleanup()
