# plugins/reference/page_toolbar.py
"""参考文献页的工具栏（两行：文件/文献集/动作，搜索/筛选/更多）。

从 library_page 抽出：那个文件已接近行数阈值，而这里只是界面拼装。
宿主需提供：doc_label 相关属性、build_library_combo / build_filter_menu、
prompt_new_library / confirm_delete_library / reload_now / scan_document / toggle_panel。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QToolButton)


class ToolbarMixin:
    """构建两行工具栏。"""

    def build_toolbar(self, layout, filters) -> None:
            # ---- 第一行：当前文件 + 文献集 + 动作（用户要求：四个按钮并排、无竖线）----
            row_top = QHBoxLayout()
            row_top.setSpacing(6)
            self.doc_label = QLabel("")
            self.doc_label.setObjectName("hintLabel")
            self.doc_label.setMinimumWidth(220)
            self.doc_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.current_document = ""
            row_top.addWidget(self.doc_label)

            self.build_library_combo(row_top)

            self.new_lib_btn = QPushButton()
            self.new_lib_btn.clicked.connect(self.prompt_new_library)
            row_top.addWidget(self.new_lib_btn)

            self.del_lib_btn = QPushButton()
            self.del_lib_btn.clicked.connect(self.confirm_delete_library)
            row_top.addWidget(self.del_lib_btn)

            self.refresh_btn = QPushButton()
            self.refresh_btn.clicked.connect(self.reload_now)
            row_top.addWidget(self.refresh_btn)

            self.scan_btn = QPushButton()
            self.scan_btn.clicked.connect(self.scan_document)
            row_top.addWidget(self.scan_btn)

            # 「详情」开/关：关掉右栏后表格能占更宽（用户要求可显隐，而不是常驻）
            # 折叠按钮：小号、带文字说明（原来大按钮写「详情」，用户不知道点它会隐藏）
            self.panel_toggle = QToolButton()
            self.panel_toggle.setCheckable(True)
            self.panel_toggle.setChecked(True)
            self.panel_toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
            # 只用样式压小内边距：写死高度会让「隐藏详情」被裁掉（用户反馈显示不全）
            self.panel_toggle.setStyleSheet("QToolButton { padding: 1px 6px; }")
            self.panel_toggle.setMinimumWidth(84)
            self.panel_toggle.setObjectName("panelToggle")
            self.panel_toggle.clicked.connect(self.toggle_panel)
            row_top.addWidget(self.panel_toggle, 0, Qt.AlignVCenter)
            row_top.addStretch(1)
            layout.addLayout(row_top)
            self._update_panel_toggle()   # 初始文案：隐藏详情

            # ---- 第二行：搜索 + 筛选 + 更多 ----
            toolbar = QHBoxLayout()
            toolbar.setSpacing(6)
            self.search_edit = QLineEdit()
            self.search_edit.textChanged.connect(self._apply_filter)
            toolbar.addWidget(self.search_edit, 1)

            self.build_filter_menu(toolbar, filters)
            self.filter_btn.setFixedWidth(84)

            self.more_btn = QToolButton()
            self.more_btn.setPopupMode(QToolButton.InstantPopup)
            self.menu = QMenu(self.more_btn)
            self.excel_action = self.menu.addAction("", self.open_csv)
            self.menu.addSeparator()
            self.import_action = self.menu.addAction("", self.import_csv)
            self.export_action = self.menu.addAction("", self.export_csv)
            self.dir_action = self.menu.addAction("", self.open_directory)
            self.menu.addSeparator()
            self.clear_master_action = self.menu.addAction("", self.confirm_clear_master)
            self.more_btn.setMenu(self.menu)
            toolbar.addWidget(self.more_btn)

            layout.addLayout(toolbar)
