# tests/test_reference_edit_and_cache.py
"""R7/R10/R12：总库按钮、人工编辑可置空、同文档编辑后位置缓存失效。

审查复现：
- R7 右栏"总库·N条"按钮抛 AttributeError（`_selected_key` 未初始化）；
- R10 人工编辑清空备注/年份、取消星标后保存，值仍在（upsert 是"补全"语义）；
- R12 同一文档插入/删除引用后，锚点位置整体偏移，但缓存仍命中。
"""
from __future__ import annotations

import pytest


class _Ctx:
    def tr(self, key, /, **params):
        return key

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
    store.upsert(Reference(ref_key="doi:1", title="T", note="原备注", year="2020",
                           tags=["a"], starred=True))
    index = ReferenceIndex(db_path=tmp_path / "库A" / "index.db")
    widget = LibraryPage(_Ctx(), store, index,
                         note_service=NoteService(store, NoteStore(directory=tmp_path)))
    yield widget, store
    index.close()
    widget.deleteLater()


def test_open_notes_without_selection_does_not_crash(page, monkeypatch):
    """R7：`_selected_key` 初始为空，点按钮应安全返回而不是 AttributeError。"""
    widget, _store = page
    widget.open_notes()          # 未选中任何条目


def test_manual_edit_can_clear_fields(page):
    """R10：人工编辑允许清空备注/年份、取消星标（不是"非空补全"）。"""
    widget, store = page
    widget._current_key = "doi:1"
    widget.title_edit.setText("T")
    widget.note_edit.setPlainText("")
    widget.year_edit.setText("")
    widget.tags_edit.setText("")
    widget.starred_check.setChecked(False)

    widget.save_current()

    saved = store.get("doi:1")
    assert saved.note == "", "清空备注应生效"
    assert saved.year == ""
    assert saved.tags == []
    assert saved.starred is False


def test_reimport_still_merges(page):
    """反向保护：导入补全语义不变（空值不覆盖、标签并集、星标取或）。"""
    from plugins.reference.reference_store import Reference

    widget, store = page
    store.upsert(Reference(ref_key="doi:1", title="新标题", note="", tags=["b"], starred=False))

    saved = store.get("doi:1")
    assert saved.note == "原备注", "导入不能清掉用户备注"
    assert set(saved.tags) == {"a", "b"}
    assert saved.starred is True


def test_doc_index_invalidates_on_content_change():
    """R12：同一文档内容变了（域数/长度变化）→ 必须重扫。"""
    from plugins.reference.doc_index import DocIndex

    index = DocIndex()
    index.doc_identity = "C:/docs/论文.docx"
    index.scanned = True
    index.signature = (5, 1200)

    assert index.needs_rescan("C:/docs/论文.docx", (5, 1200)) is False
    assert index.needs_rescan("C:/docs/论文.docx", (6, 1250)) is True, "插入引用后要重扫"
    assert index.needs_rescan("C:/docs/另一篇.docx", (5, 1200)) is True
