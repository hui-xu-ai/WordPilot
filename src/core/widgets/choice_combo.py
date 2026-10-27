# core/widgets/choice_combo.py
"""带「自定义…」选项的下拉框：真正的下拉，用户看得见能点开选。

为什么不用可编辑 QComboBox：外观就是个输入框（没有下拉箭头），用户不知道能选；
而完全不可编辑又不能填自定义值。折中：预设项 + 末尾「自定义…」，选了就弹输入框。
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QInputDialog, QWidget

CUSTOM_MARK = "__custom__"


class ChoiceCombo(QComboBox):
    """预设选项下拉；末项「自定义…」会弹窗让用户填值。"""

    def __init__(self, parent: QWidget | None = None, custom_label: str = "自定义…") -> None:
        super().__init__(parent)
        self._custom_label = custom_label
        self._tr = lambda key, **kwargs: key
        self.setEditable(False)                 # 必须有可见的下拉箭头
        self.setMinimumWidth(180)

    def retranslate(self, tr, custom_label: str = "") -> None:
        """更新文案（语言切换时调用）；`tr` 用来翻译选项 label。"""
        self._tr = tr
        if custom_label:
            self._custom_label = custom_label
        if self.count():
            self.setItemText(self.count() - 1, self._custom_label)

    def set_choices(self, choices: list[tuple[str, str]]) -> None:
        """choices = [(显示文案, 配置值), …]；末尾自动追加「自定义…」。"""
        current = self.current_value()
        self.blockSignals(True)
        try:
            self.clear()
            for label, value in choices:
                self.addItem(label, value)
            self.addItem(self._custom_label, CUSTOM_MARK)
            if current:
                self.set_value(current)
        finally:
            self.blockSignals(False)
        self.currentIndexChanged.connect(self._maybe_custom)

    def current_value(self) -> str:
        data = self.currentData()
        if data == CUSTOM_MARK:
            return ""
        return str(data or self.currentText())

    def set_value(self, value: str) -> None:
        """选中某个配置值；不在预设里就当作自定义项（插在「自定义…」之前）。"""
        for index in range(self.count()):
            if self.itemData(index) == value:
                self.setCurrentIndex(index)
                return
        if not value:
            self.setCurrentIndex(0)
            return
        custom_index = self.count() - 1                 # 「自定义…」在末尾
        self.insertItem(max(0, custom_index), value, value)
        self.setCurrentIndex(max(0, custom_index))

    def _maybe_custom(self, _index: int) -> None:
        if self.currentData() != CUSTOM_MARK:
            return
        text, ok = QInputDialog.getText(self, self._tr("choice.title"),
                                        self._tr("choice.prompt"))
        if ok and text.strip():
            self.set_value(text.strip())
        else:
            self.setCurrentIndex(0)
