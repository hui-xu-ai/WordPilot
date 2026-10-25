# tests/test_reference_note_service.py
"""NoteService：子库单条备注 + 总库多条 + 跟随挑选的门面（界面只跟它打交道）。"""
from __future__ import annotations

import pytest

from plugins.reference.notes import (SOURCE_AUTO, SOURCE_CHILD, SOURCE_INHERIT, SOURCE_NONE,
                                     NoteService, NoteStore)
from plugins.reference.reference_store import Reference, ReferenceStore


@pytest.fixture
def service(tmp_path):
    store = ReferenceStore(directory=tmp_path)
    notes = NoteStore(directory=tmp_path)
    return NoteService(store, notes), store, notes


def test_master_single_note_is_auto_filled(service):
    svc, store, notes = service
    store.upsert(Reference(ref_key="doi:1", title="T"))
    notes.add("doi:1", "唯一一条")
    text, source, hit = svc.effective("doi:1")
    assert (text, source) == ("唯一一条", SOURCE_AUTO)
    assert hit is not None


def test_picking_master_note_follows_updates(service):
    svc, store, notes = service
    store.upsert(Reference(ref_key="doi:1", title="T"))
    first = notes.add("doi:1", "第一条")
    notes.add("doi:1", "第二条")

    svc.set_inherit("doi:1", first.note_id)
    assert svc.effective("doi:1")[1] == SOURCE_INHERIT

    notes.update(first.note_id, text="总库改过")        # 跟随：子库显示跟着变
    assert svc.effective("doi:1")[0] == "总库改过"


def test_child_note_wins_over_master(service):
    svc, store, notes = service
    store.upsert(Reference(ref_key="doi:1", title="T"))
    notes.add("doi:1", "总库内容")
    svc.save_child_note("doi:1", "子库自己写的")
    text, source, _hit = svc.effective("doi:1")
    assert (text, source) == ("子库自己写的", SOURCE_CHILD)


def test_master_multiple_without_pick_uses_default(service):
    """多条未挑选时默认显示一条（钉住/最近更新），来源标记为 default。"""
    svc, store, notes = service
    store.upsert(Reference(ref_key="doi:1", title="T"))
    notes.add("doi:1", "a")
    newest = notes.add("doi:1", "b")
    text, source, hit = svc.effective("doi:1")
    assert text in {"a", "b"} and source == "default"
    assert hit is not None


def test_note_is_per_reference_not_per_citation(service):
    """同一文献在文中多处引用 → 共用一条子库备注。"""
    svc, store, _notes = service
    store.upsert(Reference(ref_key="doi:1", title="T"))
    svc.save_child_note("doi:1", "一处写的")
    assert svc.effective("doi:1")[0] == "一处写的"
    assert svc.effective("doi:2")[0] == ""


def test_inherit_survives_reload(tmp_path):
    store = ReferenceStore(directory=tmp_path)
    notes = NoteStore(directory=tmp_path)
    store.upsert(Reference(ref_key="doi:1", title="T"))
    note = notes.add("doi:1", "总库那条")
    NoteService(store, notes).set_inherit("doi:1", note.note_id)

    reopened = ReferenceStore(directory=tmp_path, )
    assert reopened.get("doi:1") and NoteService(reopened, NoteStore(directory=tmp_path)) \
        .inherit_id("doi:1") == note.note_id
