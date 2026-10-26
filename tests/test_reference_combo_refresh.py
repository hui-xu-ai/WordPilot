# tests/test_reference_combo_refresh.py
"""新建/删除仓库后，切换下拉必须立刻反映新列表。

现场：新建或删除后下拉还是旧内容（页面初始化时拿到的是库名快照，
`rebuild_library_combo()` 又把这份旧快照填回去；新建分支还调了不存在的方法）。
"""
from __future__ import annotations

import pytest


class _Ctx:
    def tr(self, key, /, **params):
        text = {"library.new": "新建仓库", "library.delete": "删除仓库",
                "action.reload": "刷新", "action.more": "更多", "filter.menu": "筛选",
                "toolbar.current_doc": "当前文件：{name}", "action.open_csv": "打开CSV",
                "action.sync_doc": "同步本文", "search.placeholder": "搜索…"}.get(key, key)
        return text.format(**params) if params else text

    def get_service(self, name):
        return None


class _FakeRegistry:
    def __init__(self):
        self._names = ["甲", "乙"]

    def names(self):
        return list(self._names)

    def create(self, name):
        if name in self._names:
            raise ValueError("已存在")
        self._names.append(name)

    def delete(self, name):
        self._names.remove(name)

    def active_name(self):
        return self._names[0] if self._names else ""


class _FakePlugin:
    def __init__(self):
        self._libraries = _FakeRegistry()
        self._active_name = "甲"


@pytest.fixture
def page(tmp_path):
    from PySide6.QtWidgets import QApplication
    from plugins.reference.library_page import LibraryPage
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "库A")
    index = ReferenceIndex(db_path=tmp_path / "库A" / "index.db")
    plugin = _FakePlugin()
    widget = LibraryPage(_Ctx(), store, index,
                         note_service=NoteService(store, NoteStore(directory=tmp_path)),
                         plugin=plugin, libraries=plugin._libraries.names(),
                         active_library="甲")
    yield widget, plugin
    index.close()
    widget.deleteLater()


def test_new_library_appears_in_combo(page, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    widget, plugin = page
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("丙", True)))

    def _create(name):
        plugin._libraries.create(name)
        plugin._active_name = name

    widget._on_create_library = _create
    widget.prompt_new_library()

    items = [widget.library_combo.itemText(i) for i in range(widget.library_combo.count())]
    assert "丙" in items, f"新建后下拉应包含新库，实际 {items}"
    assert widget.library_combo.currentText() == "丙", "应自动选中新建的库"


def test_deleted_library_disappears_from_combo(page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    widget, plugin = page
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))

    def _delete(name):
        plugin._libraries.delete(name)
        plugin._active_name = "甲"
        return True

    widget._on_delete_library = _delete
    widget.library_combo.setCurrentText("乙")
    widget.confirm_delete_library()

    items = [widget.library_combo.itemText(i) for i in range(widget.library_combo.count())]
    assert "乙" not in items, f"删除后下拉不应再有它，实际 {items}"
    assert "甲" in items
