# plugins/reference/switch_dialog.py
"""扫描后"该用哪个仓库"的确认框：显示匹配度与差异清单，给三种选择。

- `切到这个仓库`（匹配 ≥0.5 时才出现）；
- `新建仓库并命名`（新文档/想分开管理时）；
- `保持当前不动`。
"""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QPushButton, QVBoxLayout)

MAX_ITEMS = 40          # 差异清单最多列这么多条


class SwitchDialog(QDialog):
    """返回 `choice`：`switch` / `new` / `stay`；新建时 `new_name` 为用户输入。"""

    def __init__(self, plan, tr=None, existing=(), parent=None) -> None:
        super().__init__(parent)
        self._tr = tr or (lambda key, **kwargs: key)
        self._plan = plan
        self._existing = set(existing or ())
        self.choice = "stay"
        self.new_name = ""
        self._name_shown = False

        self.setWindowTitle(self._tr("switch.title"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        percent = int(getattr(plan, "score", 0.0) * 100)
        if getattr(plan, "action", "") == "prompt":
            headline = self._tr("switch.dialog_prompt", library=plan.library, percent=percent)
        else:
            headline = self._tr("switch.dialog_new")
        self.headline = QLabel(headline)
        self.headline.setWordWrap(True)
        self.headline.setObjectName("cardTitle")
        layout.addWidget(self.headline)

        added = list(getattr(plan, "added", []) or [])
        removed = list(getattr(plan, "removed", []) or [])
        if added or removed:
            self.diff_list = QListWidget()
            self.diff_list.setFixedHeight(120)
            for key in added[:MAX_ITEMS]:
                self.diff_list.addItem(self._tr("switch.diff_added", ref=key))
            for key in removed[:MAX_ITEMS]:
                self.diff_list.addItem(self._tr("switch.diff_removed", ref=key))
            layout.addWidget(self.diff_list)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self._tr("switch.new_placeholder"))
        self.name_edit.setVisible(False)
        layout.addWidget(self.name_edit)

        actions = QHBoxLayout()
        self.stay_btn = QPushButton(self._tr("switch.stay"))
        self.stay_btn.clicked.connect(self._on_stay)
        self.new_btn = QPushButton(self._tr("switch.new_library"))
        self.new_btn.clicked.connect(self._on_new)
        self.switch_btn = QPushButton(self._tr("switch.confirm"))
        self.switch_btn.setDefault(True)
        self.switch_btn.clicked.connect(self._on_switch)
        self.switch_btn.setVisible(getattr(plan, "action", "") == "prompt")
        actions.addWidget(self.new_btn)
        actions.addStretch(1)
        actions.addWidget(self.stay_btn)
        actions.addWidget(self.switch_btn)
        layout.addLayout(actions)

    # ---------------- 选择 ----------------
    def _on_switch(self) -> None:
        self.choice = "switch"
        self.accept()

    def _on_stay(self) -> None:
        self.choice = "stay"
        self.accept()

    def _on_new(self) -> None:
        """命名框还没出现时先展开；已经出现（含页面预填了建议名的情况）就直接确认新建。

        以前判断用的是内部标志位，而 `handle_switch_plan()` 会先 `setVisible(True)` 预填建议名，
        于是第一次点"新建"什么都不变（看起来"没反应"），第二次才真正创建（用户现场）。
        """
        if not self.name_edit.isVisibleTo(self):
            self._name_shown = True
            self.name_edit.setVisible(True)
            self.name_edit.setFocus()
            return
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setPlaceholderText(self._tr("switch.name_required"))
            return
        if name in self._existing:
            self.name_edit.setText("")
            self.name_edit.setPlaceholderText(self._tr("switch.name_taken"))
            return
        self.new_name = name
        self.choice = "new"
        self.accept()
