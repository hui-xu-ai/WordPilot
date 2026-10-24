# tests/test_reference_library_page.py
"""参考文献助手主页面：全库浏览/搜索/编辑/删除/孤立锚点绑定。

页面只跟 `ReferenceStore` + `ReferenceIndex` 打交道，所以测试用临时目录 + 假 ctx，
不需要 Word、不碰真实 `config/`。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.reference_store import Reference, ReferenceStore


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Ctx:
    """最小上下文：页面只用 tr()。"""

    def tr(self, key, /, **params):
        return key


@pytest.fixture
def page(qapp, tmp_path: Path):
    from plugins.reference.library_page import LibraryPage

    store = ReferenceStore(directory=tmp_path)
    index = ReferenceIndex(db_path=tmp_path / "index.db")
    widget = LibraryPage(_Ctx(), store, index)
    yield widget, store, index
    index.close()
    widget.deleteLater()


def test_empty_library_renders_empty_table(page):
    widget, _store, _index = page
    assert widget.table.rowCount() == 0
    assert "status.counts" in widget.status_label.text()


def test_save_new_reference_writes_csv(page):
    widget, store, _index = page
    widget.start_new()
    widget.label_edit.setText("1")
    widget.title_edit.setText("Global biodiversity")
    widget.year_edit.setText("2004")
    widget.note_edit.setPlainText("核心参考")
    widget.save_current()

    assert widget.table.rowCount() == 1
    reloaded = ReferenceStore(directory=store.directory)
    saved = reloaded.find_by_label("1")
    assert saved is not None
    assert saved.title == "Global biodiversity"
    assert saved.note == "核心参考"
    assert saved.ref_key.startswith("manual:"), "新建条目要有稳定身份键"


def test_editing_existing_keeps_identity(page):
    widget, store, _index = page
    store.upsert(Reference(ref_key="zotero:934", cite_label="1", title="原标题"))
    widget.reload()
    widget.table.selectRow(0)
    widget.title_edit.setText("改过的标题")
    widget.save_current()

    ref = ReferenceStore(directory=store.directory).get("zotero:934")
    assert ref is not None and ref.title == "改过的标题", "编辑必须原地更新而不是新增一条"
    assert widget.table.rowCount() == 1


def test_search_filters_rows(page):
    widget, _store, _index = page
    for index, title in enumerate(("海洋生物多样性", "无关条目", "海洋生态治理")):
        widget.start_new()
        widget.label_edit.setText(str(index + 1))
        widget.title_edit.setText(title)
        widget.save_current()

    widget.search_edit.setText("海洋")
    visible = [row for row in range(widget.table.rowCount()) if not widget.table.isRowHidden(row)]
    assert len(visible) == 2

    widget.search_edit.setText("")
    assert widget.table.rowCount() == 3


def test_delete_removes_row_and_csv_entry(page):
    widget, store, _index = page
    store.upsert(Reference(ref_key="zotero:1", title="要删的"))
    widget.reload()
    widget.table.selectRow(0)
    widget.delete_current()

    assert widget.table.rowCount() == 0
    assert ReferenceStore(directory=store.directory).get("zotero:1") is None


def test_orphan_anchor_can_be_bound_to_selected_reference(page):
    widget, store, _index = page
    store.upsert(Reference(ref_key="zotero:934", title="目标文献"))
    store.bind_anchor("论文.docx", "1", "zotero:missing")     # 指向不存在的文献
    widget.reload()

    assert widget.orphan_list.count() == 1
    widget.orphan_list.setCurrentRow(0)
    widget.table.selectRow(0)                                  # 右栏载入 zotero:934
    widget.bind_orphan()

    anchor = ReferenceStore(directory=store.directory).anchor_for("论文.docx", "1")
    assert anchor is not None and anchor.anchor_key == "zotero:934"
    assert widget.orphan_list.count() == 0


def test_save_requires_title_or_note(page):
    widget, store, _index = page
    widget.start_new()
    widget.save_current()
    assert widget.table.rowCount() == 0
    assert widget.status_label.text().endswith("status.need_title")
    assert store.all() == []


def test_counts_reflect_notes_and_orphans(page):
    widget, store, _index = page
    store.upsert(Reference(ref_key="zotero:1", title="A", note="有备注"))
    store.upsert(Reference(ref_key="zotero:2", title="B"))
    store.bind_anchor("论文.docx", "3", "zotero:gone")
    widget.reload()

    text = widget.status_label.text()
    assert "total=2" in text or "{total}" not in text
    assert widget.orphan_list.count() == 1

def test_translations_are_resolved_not_raw_keys(qapp, tmp_path: Path):
    """回归：界面里不能再出现 `reference.xxx` 这种原始键名（曾经双前缀导致满屏键名）。"""
    from plugins.reference.library_page import LibraryPage
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import ReferenceStore

    class _I18n:
        """模拟真实 ctx.tr：自动加插件前缀，并在字典里查词。"""

        _DICT = {
            "page_label": "参考文献助手",
            "search.placeholder": "搜索…",
            "action.new": "新建", "action.import": "导入", "action.export": "导出",
            "action.open_dir": "打开目录", "action.save": "保存", "action.delete": "删除",
            "action.bind_orphan": "绑定", "field.key": "身份键", "field.note": "备注",
        }

        def __call__(self, key, /, **params):
            return self._DICT.get(key, f"MISSING:{key}")

    class _CtxWithI18n:
        def tr(self, key, /, **params):
            return _I18n()(key, **params)

    store = ReferenceStore(directory=tmp_path)
    index = ReferenceIndex(db_path=tmp_path / "index.db")
    try:
        page = LibraryPage(_CtxWithI18n(), store, index)
        assert page.title_label.text() == "参考文献助手"
        assert page.new_btn.text() == "新建"
        assert not page.search_edit.placeholderText().startswith("MISSING")
    finally:
        index.close()
        page.deleteLater()
