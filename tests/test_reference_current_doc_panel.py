# tests/test_reference_current_doc_panel.py
"""方案阶段 2：界面显示"当前文件"，右栏可折叠。

对应用户验收任务 1/3：打开文档要知道"这是哪份文件、属于哪个文献集"，
窄窗口时能收起右栏只看列表。
"""
from __future__ import annotations

import pytest


class _Ctx:
    """最小 i18n 替身：模板必须带 {name} 占位符，否则测不出替换。"""

    TEMPLATES = {"toolbar.current_doc": "当前文件：{name}",
                 "panel.toggle": "详情",
                 "panel.toggle.tip": "显示/隐藏详情栏"}

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
    widget.resize(1100, 500)
    widget.show()
    yield widget
    index.close()
    widget.deleteLater()


def test_current_document_is_shown(page):
    page.set_current_document("论文A-归档.docx")
    assert "论文A-归档.docx" in page.doc_label.text()


def test_current_document_defaults_to_placeholder(page):
    assert page.doc_label.text() != ""
    assert "—" in page.doc_label.text()


def test_document_signal_is_forwarded(monkeypatch, tmp_path):
    """监听线程的 documentChanged 应更新页面标签（插件侧接线）。"""
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)

    class _Page:
        def __init__(self):
            self.names = []

        def set_current_document(self, name):
            self.names.append(name)

    plugin._page = _Page()
    plugin._on_document_changed("论文B.docx")

    assert plugin._page.names == ["论文B.docx"]
