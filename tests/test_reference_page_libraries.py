"""页面 rebind：切换仓库后就地换数据层、下拉回显、状态栏提示。"""
from __future__ import annotations

import pytest

from plugins.reference.library_page import LibraryPage
from plugins.reference.notes import NoteService, NoteStore
from plugins.reference.reference_index import ReferenceIndex
from plugins.reference.reference_store import Reference, ReferenceStore
from tests.test_reference_library_page import _Ctx


@pytest.fixture
def pages(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    first = tmp_path / "库A"
    second = tmp_path / "库B"
    first.mkdir()
    second.mkdir()
    store_a = ReferenceStore(directory=first)
    store_a.upsert(Reference(ref_key="doi:a", title="A 的文献"))
    store_b = ReferenceStore(directory=second)
    store_b.upsert(Reference(ref_key="doi:b", title="B 的文献"))
    store_b.upsert(Reference(ref_key="doi:b2", title="B 的第二条"))

    index_a = ReferenceIndex(db_path=first / "index.db")
    index_b = ReferenceIndex(db_path=second / "index.db")
    widget = LibraryPage(_Ctx(), store_a, index_a, note_service=NoteService(store_a, NoteStore(directory=first)),
                         libraries=["库A", "库B"], active_library="库A")
    yield widget, (store_a, index_a, first), (store_b, index_b, second)
    index_a.close()
    index_b.close()
    widget.deleteLater()


def test_combo_lists_libraries(pages):
    widget, _a, _b = pages
    assert [widget.library_combo.itemText(i) for i in range(widget.library_combo.count())] == ["库A", "库B"]
    assert widget.library_combo.currentText() == "库A"


def test_rebind_swaps_rows_and_status(pages):
    widget, _a, (store_b, index_b, directory_b) = pages
    widget.reload()
    assert widget.table.rowCount() == 1

    widget.rebind(store_b, index_b, NoteService(store_b, NoteStore(directory=directory_b)), "库B")

    assert widget.table.rowCount() == 2, "切换仓库后应显示新库的数据"
    assert widget.library_combo.currentText() == "库B"
    assert "库B" in widget.footer_label.text()
    assert widget._selected_key == "", "切库后清空选中，避免指向旧库条目"


def test_switching_signal_only_fires_for_new_library(tmp_path):
    from PySide6.QtWidgets import QApplication

    from plugins.reference.reference_store import ReferenceStore

    app = QApplication.instance() or QApplication([])
    calls: list[str] = []
    store = ReferenceStore(directory=tmp_path)
    index = ReferenceIndex(db_path=tmp_path / "index.db")
    widget = LibraryPage(_Ctx(), store, index, libraries=["库A", "库B"],
                         active_library="库A", on_switch_library=calls.append)
    widget.library_combo.setCurrentText("库B")
    widget.library_combo.setCurrentText("库B")
    assert calls == ["库B"], "同一个库不应重复触发切换"
    index.close()
    widget.deleteLater()


def test_new_library_action_exists_and_prompts(pages, monkeypatch):
    """用户最初的要求：界面上有「新建仓库」按钮，点了能命名并切过去。"""
    widget, _a, _b = pages
    assert hasattr(widget, "new_lib_action"), "缺少「新建文献集」入口"
    assert widget.new_lib_action.text(), "按钮要有文案"

    created: list[str] = []
    monkeypatch.setattr(widget, "_on_create_library", created.append, raising=False)

    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("新仓库", True)))
    widget.prompt_new_library()

    assert created == ["新仓库"], f"应把名字交给建库回调，实际 {created}"
