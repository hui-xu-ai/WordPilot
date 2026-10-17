# plugins/reference/plugin.py
"""参考文献助手插件（开发中，默认不启用）。

目标：识别 EndNote / Zotero 等文献管理软件插入的域代码，
为每条参考文献建立「身份笔记」——记住它是谁、来自哪个库、
用户此前对它做过什么处理，从而避免重复分析同一条文献。

验证基准（fixture）：
    work/EndNote文献论文测试.docx    约 1973 个域代码
    work/Zotero文献论文测试.docx

启用方式：在 config/MWindow_config.json 中把 reference 从
plugins.disabled 列表里移除即可（本插件 enabled_by_default 为 false）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from core.sdk.plugin_base import PluginBase

if TYPE_CHECKING:
    from core.sdk.context import PluginContext


class ReferencePlugin(PluginBase):
    """参考文献助手：当前只提供入口占位，具体实现 TODO。"""

    def activate(self, ctx: "PluginContext") -> None:
        super().activate(ctx)
        ctx.ui.add_tool("tool_label", self.open, order=60, plugin_id="reference")
        ctx.logger.info(self.tr("log.activated"))

    def deactivate(self) -> None:
        super().deactivate()

    def open(self) -> None:
        QMessageBox.information(None, self.tr("window.title"), self.tr("wip.body"))
