# tests/test_reference_notes.py
"""总库多条备注 + 子库单条备注的解析规则（用户定稿的模型）。

要点：
- 子库：每条文献 1 条备注，文中多处引用共用；
- 总库：多条，子库"挑选"的是 **id（跟随）**，不是复制文本；
- 总库只有 1 条时自动填充；多条时需用户挑选；
- 优先级：子库内容 > 挑选的总库备注 > 唯一自动填充 > 空。
"""
from __future__ import annotations

import pytest

from plugins.reference.notes import (SOURCE_AUTO, SOURCE_CHILD, SOURCE_INHERIT, SOURCE_NONE,
                                     Note, NoteStore, pick_default_note, resolve_note)


@pytest.fixture
def store(tmp_path):
    return NoteStore(directory=tmp_path)


def _note(note_id: str, text: str, label: str = "", pinned: bool = False,
          updated: str = "2026-01-01 00:00:00", archived: bool = False) -> Note:
    return Note(note_id=note_id, ref_key="doi:10.1/x", text=text, label=label,
                pinned=pinned, updated=updated, archived=archived)


def test_resolve_prefers_child_note():
    text, source, hit = resolve_note("子库写的", "n1", [_note("n1", "总库的")])
    assert (text, source, hit) == ("子库写的", SOURCE_CHILD, None)


def test_resolve_follows_picked_master_note():
    """挑选是"跟随"：总库那条改了，子库显示立刻跟着变。"""
    master = _note("n1", "第一版")
    assert resolve_note("", "n1", [master])[:2] == ("第一版", SOURCE_INHERIT)
    master.text = "总库改过"
    assert resolve_note("", "n1", [master])[0] == "总库改过"


def test_resolve_auto_fills_when_master_has_single_note():
    text, source, hit = resolve_note("", "", [_note("n1", "唯一一条")])
    assert (text, source) == ("唯一一条", SOURCE_AUTO)
    assert hit is not None and hit.note_id == "n1"


def test_resolve_defaults_to_best_note_when_master_has_many():
    """多条且未挑选 → 默认显示"钉住/最近更新"那条，不留空白（用户反馈）。"""
    text, source, hit = resolve_note("", "", [_note("n1", "a", updated="2026-01-01 00:00:00"),
                                               _note("n2", "b", pinned=True)])
    assert (text, source) == ("b", "default")
    assert hit is not None and hit.note_id == "n2"


def test_resolve_ignores_archived_notes():
    text, source, _ = resolve_note("", "", [_note("n1", "已归档", archived=True)])
    assert (text, source) == ("", SOURCE_NONE)


def test_resolve_falls_back_when_picked_note_archived():
    """挑选的那条被归档后不能继续显示它，回落到唯一可用条目。"""
    text, source, hit = resolve_note("", "n1", [_note("n1", "旧", archived=True),
                                                _note("n2", "新")])
    assert (text, source) == ("新", SOURCE_AUTO)
    assert hit is not None and hit.note_id == "n2"


def test_pick_default_prefers_pinned_then_recent():
    notes = [_note("n1", "a", updated="2026-01-03 00:00:00"),
             _note("n2", "b", pinned=True, updated="2026-01-01 00:00:00")]
    assert pick_default_note(notes).note_id == "n2"
    assert pick_default_note([_note("n3", "c")]).note_id == "n3"
    assert pick_default_note([]) is None


def test_store_add_update_archive_roundtrip(store):
    note = store.add("doi:10.1/x", "第一次笔记", label="引用理由")
    assert note.note_id and note.text == "第一次笔记"

    store.update(note.note_id, text="改过", pinned=True)
    reloaded = NoteStore(directory=store.directory)
    fetched = reloaded.get(note.note_id)
    assert fetched is not None
    assert fetched.text == "改过" and fetched.pinned is True
    assert fetched.label == "引用理由"

    reloaded.archive(note.note_id)
    assert reloaded.notes_for("doi:10.1/x") == []
    assert len(reloaded.notes_for("doi:10.1/x", include_archived=True)) == 1


def test_store_keeps_multiple_notes_and_orders_them(store):
    first = store.add("doi:10.1/x", "早", label="A")
    second = store.add("doi:10.1/x", "晚", label="B")
    store.update(second.note_id, pinned=True)      # 钉住的排前面

    notes = store.notes_for("doi:10.1/x")
    assert [note.note_id for note in notes] == [second.note_id, first.note_id]
    assert notes[0].label == "B"


def test_notes_are_per_reference_not_per_citation(store):
    """同一文献在文中多处引用 → 共用同一条子库备注/同一组总库备注。"""
    store.add("doi:10.1/x", "文献级备注")
    assert len(store.notes_for("doi:10.1/x")) == 1
    assert store.notes_for("doi:10.1/other") == []


def test_unnamed_notes_get_readable_labels(store):
    first = store.add("doi:10.1/x", "a")
    second = store.add("doi:10.1/x", "b")
    assert first.label == "备注 1"
    assert second.label == "备注 2"


def test_seed_from_old_single_note_migration(store):
    """旧版单条备注 → 总库备注（每篇一条），重复执行不会重复插入。"""
    created = store.seed_from_reference_notes([("doi:10.1/x", "旧笔记"), ("doi:10.1/y", "  ")])
    assert created == 1
    assert store.notes_for("doi:10.1/x")[0].text == "旧笔记"
    assert store.seed_from_reference_notes([("doi:10.1/x", "旧笔记")]) == 0


def test_csv_escapes_multiline_text(store):
    note = store.add("doi:10.1/x", "第一行\n第二行，带逗号","标签,带逗号")
    reloaded = NoteStore(directory=store.directory)
    fetched = reloaded.get(note.note_id)
    assert fetched is not None
    assert fetched.text == "第一行\n第二行，带逗号"
    assert fetched.label == "标签,带逗号"
