# tests/test_reference_note_service.py
"""NoteService：子库单条备注 + **全局**总库多条（按 2026-10-26 需求基线）。

C1：总库跨子库共享 —— 两个库的 NoteService 共用同一个总库存储。
C2：未选择不采用；采用后子库保存独立文本；总库变化只通过 adopted_diff 暴露。
"""
from __future__ import annotations

import pytest

from plugins.reference.notes import SOURCE_AVAILABLE, SOURCE_CHILD, NoteService, NoteStore
from plugins.reference.reference_store import Reference, ReferenceStore


@pytest.fixture
def service(tmp_path):
    child_store = ReferenceStore(directory=tmp_path / "子库A")
    master = NoteStore(directory=tmp_path / "总库")
    return NoteService(child_store, master), child_store, master


def _add_ref(store, key="doi:1"):
    store.upsert(Reference(ref_key=key, title="T"))


def test_master_alone_is_not_auto_adopted(service):
    svc, store, master = service
    _add_ref(store)
    master.add("doi:1", "唯一一条")

    text, source, hit = svc.effective("doi:1")

    assert (text, source, hit) == ("", SOURCE_AVAILABLE, None)
    assert svc.available_count("doi:1") == 1


def test_adopt_copies_text_and_remembers_source(service):
    """采用总库备注 → 子库保存独立文本，并记录来源 ID（供差异提示）。"""
    svc, store, master = service
    _add_ref(store)
    note = master.add("doi:1", "总库内容")

    svc.adopt_master_note("doi:1", note.note_id)

    text, source, _hit = svc.effective("doi:1")
    assert (text, source) == ("总库内容", SOURCE_CHILD)
    assert svc.inherit_id("doi:1") == note.note_id


def test_adopted_text_does_not_change_with_master(service):
    """采用后总库改内容：子库文本不变，只通过 adopted_diff 暴露差异。"""
    svc, store, master = service
    _add_ref(store)
    note = master.add("doi:1", "采用时的文本")
    svc.adopt_master_note("doi:1", note.note_id)

    master.update(note.note_id, text="总库后来改了")

    assert svc.effective("doi:1")[0] == "采用时的文本"
    assert svc.adopted_diff("doi:1") == "总库后来改了"
    assert svc.available_count("doi:1") == 0, "子库已有内容时不再提示可选"


def test_publish_to_master_only_on_explicit_call(service):
    """只有 publish_to_master 才写总库；保存子库不会。"""
    svc, store, master = service
    _add_ref(store)

    svc.save_child_note("doi:1", "子库写的内容")
    assert master.notes_for("doi:1") == [], "保存子库不应发布到总库"

    published = svc.publish_to_master("doi:1")

    assert published is not None and published.text == "子库写的内容"
    assert [note.text for note in master.notes_for("doi:1")] == ["子库写的内容"]


def test_master_is_shared_across_child_libraries(tmp_path):
    """C1：两个子库共用同一个总库文件（A 发布后 B 能看到并采用）。"""
    master = NoteStore(directory=tmp_path / "总库")
    store_a = ReferenceStore(directory=tmp_path / "子库A")
    store_b = ReferenceStore(directory=tmp_path / "子库B")
    _add_ref(store_a)
    _add_ref(store_b)

    svc_a = NoteService(store_a, master)
    svc_a.save_child_note("doi:1", "A 的项目备注")
    svc_a.publish_to_master("doi:1")

    svc_b = NoteService(store_b, master)
    assert svc_b.available_count("doi:1") == 1, "B 应能看到 A 发布的总库备注"
    svc_b.adopt_master_note("doi:1", svc_b.master_notes("doi:1")[0].note_id)

    assert svc_b.effective("doi:1")[0] == "A 的项目备注"
    assert svc_a.effective("doi:1")[0] == "A 的项目备注", "A 的项目备注不被覆盖"
