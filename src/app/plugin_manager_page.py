# app/plugin_manager_page.py
"""插件管理面板（内置页）。

规则（与微内核约定一致）：
- 这里列出所有「已发现」的插件（内置 + 用户目录安装的）；
- 勾选 = 启用 + 显示在左侧导航轨 + 上桌宠右键菜单（同一开关）；
- 用户安装的插件与默认关闭的插件，必须勾选后才开始工作；
- 上下箭头调整侧边栏顺序；
- 支持从文件夹或 zip 安装新插件（进 plugins_user/，安装后默认未勾选）。

面板本身只调 PluginManager 的公开 API，不碰插件内部。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from core.plugin_manager import PluginState


class PluginManagerPage(QWidget):
    def __init__(self, kernel, parent=None, i18n=None) -> None:
        super().__init__(parent)
        self.kernel = kernel
        self.i18n = i18n
        self._building = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # 顶部：提示 + 安装/刷新
        top_row = QHBoxLayout()
        self.hint_label = QLabel()
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setWordWrap(True)
        top_row.addWidget(self.hint_label, 1)
        self.install_btn = QPushButton()
        self.install_btn.setProperty("accent", True)
        self.install_btn.clicked.connect(self.install_plugin)
        top_row.addWidget(self.install_btn)
        layout.addLayout(top_row)

        # 插件列表（可滚动，插件多了也不撑破窗口）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        layout.addWidget(self.scroll, 1)

        # 插件启停/安装后重建列表；页面构造早于插件加载，
        # 所以每次显示时也刷新一次，保证列表永远是最新的
        self.kernel.events.subscribe("plugins.changed", lambda _e, _p: self.rebuild())
        self.retranslateUi()

    def showEvent(self, event) -> None:      # noqa: N802 - Qt 命名惯例
        super().showEvent(event)
        self.kernel.plugins.rescan()
        self.rebuild()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self.i18n is None:
            return key
        return self.i18n.tr(key, **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.hint_label.setText(self.tr("plugins.hint"))
        self.install_btn.setText(self.tr("plugins.install"))
        self.rebuild()

    # ---------------- 列表 ----------------
    def rebuild(self) -> None:
        if self._building:
            return
        self._building = True
        try:
            while self.list_layout.count() > 1:      # 保住末尾 stretch
                item = self.list_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()

            records = self.kernel.plugins.all()
            order = self._order()
            records.sort(key=lambda r: (order.index(r.manifest.id)
                                        if r.manifest.id in order else len(order),
                                        r.manifest.id))
            if not records:
                empty = QLabel(self.tr("plugins.empty"))
                empty.setObjectName("hintLabel")
                self.list_layout.insertWidget(0, empty)
                return

            for index, record in enumerate(records):
                self.list_layout.insertWidget(
                    index, self._make_row(record, index, len(records)))
        finally:
            self._building = False

    def _make_row(self, record, index: int, total: int) -> QWidget:
        manifest = record.manifest
        row = QWidget()
        row.setObjectName("pluginRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 8, 10, 8)

        enabled_box = QCheckBox(manifest.name or manifest.id)
        enabled_box.setChecked(record.state == PluginState.ACTIVE)
        enabled_box.toggled.connect(
            lambda on, pid=manifest.id: self._toggle(pid, on))
        row_layout.addWidget(enabled_box)

        info = QLabel(f"v{manifest.version}  {manifest.description}")
        info.setObjectName("hintLabel")
        row_layout.addWidget(info, 1)

        state_text = {
            PluginState.ACTIVE: self.tr("plugins.state.active"),
            PluginState.DISABLED: self.tr("plugins.state.disabled"),
            PluginState.FAILED: self.tr("plugins.state.failed"),
        }.get(record.state, "")
        state = QLabel(state_text)
        state.setObjectName("hintLabel")
        if record.state == PluginState.FAILED and record.error:
            state.setToolTip(record.error)
        row_layout.addWidget(state)

        up_btn = QToolButton()
        up_btn.setText("↑")
        up_btn.setEnabled(index > 0)
        up_btn.clicked.connect(lambda _c=False, i=index: self._move(i, -1))
        down_btn = QToolButton()
        down_btn.setText("↓")
        down_btn.setEnabled(index < total - 1)
        down_btn.clicked.connect(lambda _c=False, i=index: self._move(i, 1))
        row_layout.addWidget(up_btn)
        row_layout.addWidget(down_btn)
        return row

    # ---------------- 操作 ----------------
    def _order(self) -> list[str]:
        value = self.kernel.config.get("plugins", {}) or {}
        order = value.get("order", [])
        return order if isinstance(order, list) else []

    def _toggle(self, plugin_id: str, on: bool) -> None:
        if self._building:
            return
        self.kernel.plugins.set_enabled(plugin_id, on)
        # plugins.changed 事件会触发 rebuild；导航轨由主窗口监听同一事件重建

    def _move(self, index: int, delta: int) -> None:
        records = self.kernel.plugins.all()
        order = self._order()
        records.sort(key=lambda r: (order.index(r.manifest.id)
                                    if r.manifest.id in order else len(order),
                                    r.manifest.id))
        ids = [r.manifest.id for r in records]
        target = index + delta
        if 0 <= target < len(ids):
            ids[index], ids[target] = ids[target], ids[index]
            self.kernel.plugins.set_order(ids)

    def install_plugin(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, self.tr("plugins.install_title"), "",
            "Plugin package (*.zip);;All files (*)")
        if not path:
            # 也支持直接选一个包含 plugin.json 的文件夹
            path = QFileDialog.getExistingDirectory(self, self.tr("plugins.install_dir"))
        if not path:
            return
        try:
            manifest = self.kernel.plugins.install(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, self.tr("plugins.install_fail_title"),
                                self.tr("plugins.install_fail", error=exc))
            return
        QMessageBox.information(self, self.tr("plugins.install_ok_title"),
                                self.tr("plugins.install_ok", name=manifest.name))
