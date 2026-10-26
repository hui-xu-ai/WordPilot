# tests/test_reference_panel_toggle.py
"""详情栏折叠：必须隐藏**整块**右栏、按钮要小且有说明文字。

用户反馈：右栏那一列始终在（只隐藏了里面的一层），按钮又大又只写「详情」，
点了没反应也看不懂。
"""
from __future__ import annotations

import pytest


class _Ctx:
    TEMPLATES = {
        "panel.hide": "隐藏详情", "panel.show": "显示详情",
        "panel.toggle.tip": "隐藏右侧详情后，表格可以占满更宽的空间",
        "toolbar.current_doc": "当前文件：{name}", "library.new": "新建仓库",
        "library.delete": "删除仓库", "action.reload": "刷新", "action.more": "更多",
        "action.open_csv": "打开CSV", "action.sync_doc": "同步本文",
        "search.placeholder": "搜索…", "filter.menu": "筛选",
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
    from plugins.reference.reference_store import ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "库A")
    index = ReferenceIndex(db_path=tmp_path / "库A" / "index.db")
    widget = LibraryPage(_Ctx(), store, index,
                         note_service=NoteService(store, NoteStore(directory=tmp_path)))
    widget.resize(1100, 600)
    widget.show()
    yield widget
    index.close()
    widget.deleteLater()


def test_toggle_hides_whole_panel(page):
    widget = page
    assert widget.panel_widget.isVisible()

    widget.panel_toggle.setChecked(False)
    widget.toggle_panel()

    assert not widget.panel_widget.isVisible(), "整块右栏都要隐藏（不是只藏里面的 stack）"

    widget.panel_toggle.setChecked(True)
    widget.toggle_panel()
    assert widget.panel_widget.isVisible()


def test_toggle_text_says_what_it_does(page):
    widget = page
    assert widget.panel_toggle.text() == "隐藏详情", "默认应说明点了会隐藏"

    widget.panel_toggle.setChecked(False)
    widget.toggle_panel()
    assert widget.panel_toggle.text() == "显示详情"

    widget.panel_toggle.setChecked(True)
    widget.toggle_panel()
    assert widget.panel_toggle.text() == "隐藏详情"


def test_toggle_button_is_small(page):
    widget = page
    assert widget.panel_toggle.maximumHeight() <= 28 or widget.panel_toggle.height() <= 30, \
        "按钮应是小号的，不能和普通按钮一样高"


def test_table_gets_wider_when_panel_hidden(page):
    from PySide6.QtWidgets import QApplication

    widget = page
    QApplication.processEvents()
    before = widget._splitter.sizes()[0]

    widget.panel_toggle.setChecked(False)
    widget.toggle_panel()
    QApplication.processEvents()

    assert widget._splitter.sizes()[0] > before, "隐藏详情后表格应更宽"
