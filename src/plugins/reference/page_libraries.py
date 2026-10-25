# plugins/reference/page_libraries.py
"""页面里的「仓库下拉 + 切换重绑」（从 library_page 拆出，保持单文件在阈值内）。"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox


class LibraryMixin:
    """宿主需提供 `_libraries / _active_library / _on_switch_library / _store / _index`。"""

    def build_library_combo(self, toolbar) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName("libraryCombo")
        combo.setMinimumWidth(120)
        combo.addItems(self._libraries or [getattr(self, "_active_library", "") or "默认库"])
        if getattr(self, "_active_library", ""):
            combo.setCurrentText(self._active_library)
        combo.currentTextChanged.connect(self._on_library_changed)
        self.library_combo = combo
        toolbar.addWidget(combo)
        return combo

    def _on_library_changed(self, name: str) -> None:
        if not name or name == self._active_library or self._on_switch_library is None:
            return
        self._active_library = name
        try:
            self._on_switch_library(name)
        except Exception as exc:               # noqa: BLE001 - 切换失败要能看见
            self.append_status(self.tr("status.scan_failed", error=str(exc)))

    def rebind(self, store, index, note_service, active_name: str = "") -> None:
        """切换到另一个仓库后就地重绑数据层（不重建页面）。"""
        self._store = store
        self._index = index
        self._note_service = note_service
        if active_name:
            self._active_library = active_name
            self.library_combo.blockSignals(True)
            self.library_combo.setCurrentText(active_name)
            self.library_combo.blockSignals(False)
        self._selected_key = ""
        self.reload()
        hint = self.tr("status.library", name=active_name).replace("{name}", active_name)
        self.append_status(hint)
