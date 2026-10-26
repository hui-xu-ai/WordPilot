# tests/test_reference_toolbar_v3.py
"""用户第三轮反馈：详情可开关、备注列可直接编辑、更多用文字、监听信号走 GUI 线程中转。"""
from __future__ import annotations

import pytest


class _Ctx:
    TEMPLATES = {
        "toolbar.current_doc": "当前文件：{name}", "filter.menu": "筛选",
        "library.new": "新建仓库", "library.delete": "删除仓库", "action.reload": "刷新",
        "action.reload.tip": "重新读取当前仓库的 CSV", "action.more": "更多",
        "action.open_csv": "打开CSV", "action.sync_doc": "同步本文",
        "action.scan.tip": "扫描当前 Word 文档", "action.import": "导入",
        "action.export": "导出", "action.open_dir": "目录", "action.edit": "编辑",
        "action.bind_orphan": "绑定", "search.placeholder": "搜索…",
        "panel.toggle": "详情", "panel.toggle.tip": "显示/隐藏右侧详情",
        "library.new.tip": "", "library.delete.tip": "", "col.note.edit_tip": "双击编辑",
    }

    def tr(self, key, /, **params):
        text = self.TEMPLATES.get(key, key)
        return text.format(**params) if params else text

    def get_service(self, name):
        return None


@pytest.fixture
def page(tmp_path):
    from PySide6.QtWidgets import QApplication
    from plugins.reference.library_page import LibraryPage
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import Reference, ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "库A")
    store.upsert(Reference(ref_key="doi:1", title="T", note="原备注"))
    index = ReferenceIndex(db_path=tmp_path / "库A" / "index.db")
    widget = LibraryPage(_Ctx(), store, index,
                         note_service=NoteService(store, NoteStore(directory=tmp_path)))
    widget.resize(1000, 600)
    widget.show()
    widget.reload()
    yield widget, store
    index.close()
    widget.deleteLater()


def test_more_button_uses_words_not_ellipsis(page):
    widget, _store = page
    assert widget.more_btn.text() == "更多"


def test_refresh_and_sync_have_tooltips(page):
    widget, _store = page
    assert widget.refresh_btn.toolTip() != ""
    assert widget.scan_btn.toolTip() != ""


def test_details_toggle_hides_and_shows_panel(page):
    widget, _store = page
    assert widget.panel_stack.isVisible()

    widget.panel_toggle.setChecked(False)
    widget.toggle_panel()
    assert not widget.panel_stack.isVisible(), "关掉详情后右栏应隐藏（表格更宽）"

    widget.panel_toggle.setChecked(True)
    widget.toggle_panel()
    assert widget.panel_stack.isVisible()


def test_note_column_is_editable_and_saves(page):
    widget, store = page
    item = widget.table.item(0, 3)
    assert item.text() == "原备注"
    from PySide6.QtCore import Qt
    assert item.flags() & Qt.ItemIsEditable, "备注列应可编辑"

    item.setText("改过的备注")          # 模拟用户直接编辑
    assert store.get("doi:1").note == "改过的备注"


def test_filling_table_does_not_write_notes(page):
    """填充表格时不能被 itemChanged 误触发保存（曾经把显示摘要写回备注）。"""
    widget, store = page
    widget.reload()
    assert store.get("doi:1").note == "原备注"


def test_watch_signals_go_through_relay():
    """监听线程 → QObject 中转（否则槽在工作线程执行，弹窗/文件标签不生效）。"""
    from plugins.reference.plugin import ReferencePlugin
    from plugins.reference.watch_bridge import WatchRelay

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._page = None
    plugin._overlay = None

    class _Signal:
        def __init__(self):
            self.slots = []

        def connect(self, slot):
            self.slots.append(slot)

    class _Watch:
        citationFound = _Signal()
        citationCleared = _Signal()
        statusChanged = _Signal()
        documentChanged = _Signal()

    plugin._watch = _Watch()
    plugin._connect_doc_signal()

    relay = plugin._watch_relay
    assert isinstance(relay, WatchRelay)
    assert relay.on_citation in plugin._watch.citationFound.slots
    assert relay.on_document in plugin._watch.documentChanged.slots
    assert relay.on_status in plugin._watch.statusChanged.slots
