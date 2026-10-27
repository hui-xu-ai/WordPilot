# tests/test_reference_publish_all.py
"""手动"更新到总库"：整库一次性推送 + 单条推送（用户要求：不自动同步）。

语义（用户 2026-10-27 定）：
- 平时只对**单篇文献**更新到总库；
- 认为当前子库完善了，再从下拉菜单**一次性**把整库备注推到总库；
- 重复点不会堆出重复条目（总库里已有完全同文就跳过）。
"""
from __future__ import annotations

import pytest


def _make(tmp_path):
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_store import Reference, ReferenceStore

    child = ReferenceStore(directory=tmp_path / "子库")
    master = NoteStore(directory=tmp_path)          # notes.csv 落在 tmp_path
    service = NoteService(child, master)
    for key, note in (("zotero:1", "第一条备注"), ("zotero:2", "第二条备注"),
                      ("zotero:3", "")):
        child.upsert(Reference(ref_key=key, title=f"文献{key[-1]}", note=note))
    return service, child, master


def test_publish_all_pushes_only_non_empty_and_is_idempotent(tmp_path):
    service, child, master = _make(tmp_path)

    published, duplicate, empty = service.publish_all_to_master("子库")

    assert (published, duplicate, empty) == (2, 0, 1), "只推非空备注"
    assert len(master.notes_all()) == 2
    assert child.get("zotero:1").inherit_note_id, "推送后要记下来源，便于日后提示差异"

    again = service.publish_all_to_master("子库")

    assert again[0] == 0 and again[1] == 2, "再点一次不应产生重复条目"
    assert len(master.notes_all()) == 2


def test_single_publish_to_master(tmp_path):
    service, child, master = _make(tmp_path)

    note = service.publish_to_master("zotero:1")

    assert note is not None and note.text == "第一条备注"
    assert len(master.notes_all()) == 1, "单条只推这一篇"
    assert child.get("zotero:1").inherit_note_id == note.note_id


def test_page_menu_triggers_publish_all(tmp_path, monkeypatch):
    """下拉菜单里的「全部备注 → 总库」要能真的调到插件（并写进底部状态栏）。"""
    from PySide6.QtWidgets import QApplication, QMessageBox

    from plugins.reference.library_page import LibraryPage
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "子库")
    store.upsert(__import__("plugins.reference.reference_store", fromlist=["Reference"])
                 .Reference(ref_key="zotero:1", title="文献1", note="备注A"))
    index = ReferenceIndex(db_path=tmp_path / "子库" / "index.db")
    service = NoteService(store, NoteStore(directory=tmp_path))

    class _Ctx:
        def tr(self, key, /, **params):
            return key.format(**params) if params else key

        def get_service(self, name):
            return None

    calls = []
    widget = LibraryPage(_Ctx(), store, index, note_service=service,
                         libraries=["子库"], active_library="子库",
                         on_publish_all_notes=lambda: (calls.append(1), (1, 0, 0))[1])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    widget.confirm_publish_all_master()

    assert calls, "确认后应调用插件的整库推送"
    assert "master.publish_all_done" in widget.footer_label.text()
    index.close()
    widget.deleteLater()
