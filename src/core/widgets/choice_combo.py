# core/widgets/choice_combo.py
"""预设选项下拉：只有预定义项，不允许用户输入自由文本。

设计约束（用户 2026-10-27）：
- 语言/强度这类取值必须**预先定义**，用户不能自己填 —— 填错的数据会一路传到接口；
- 需要特殊行为就去改提示词本身（提示词里的占位符可以删掉，写死自己的要求）；
- 所以下拉不可编辑，也没有「自定义…」项。
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QWidget


class ChoiceCombo(QComboBox):
    """不可编辑的下拉：`set_choices([(显示文案, 配置值), …])`。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(False)                  # 有可见下拉箭头，且不能手输
        self.setMinimumWidth(180)

    def set_choices(self, choices: list[tuple[str, str]]) -> None:
        current = self.current_value()
        self.blockSignals(True)
        try:
            self.clear()
            for label, value in choices:
                self.addItem(label, value)
            if current:
                self.set_value(current)
        finally:
            self.blockSignals(False)

    def current_value(self) -> str:
        data = self.currentData()
        return str(data) if data else ""

    def set_value(self, value: str) -> None:
        """选中某个配置值；不在预设列表里就退回第一项（绝不把脏值写进配置）。"""
        for index in range(self.count()):
            if self.itemData(index) == value:
                self.setCurrentIndex(index)
                return
        if self.count():
            self.setCurrentIndex(0)
