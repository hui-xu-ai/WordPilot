# tests/test_reference_notes.py
"""总库/子库备注解析规则（按 2026-10-26 需求基线重写）。

新规则（REQUIREMENTS.md）：
- 子库显示自己**已保存**的内容（自行创建或采用后复制进来的）；
- 子库为空时，总库有内容也**不自动填充**，只返回 available 让界面提示"N 条可选"；
- 总库唯一一条同样必须用户选择，不存在 auto/default；
- 归档的备注不参与。
"""
from __future__ import annotations

import pytest

from plugins.reference.notes import (SOURCE_AVAILABLE, SOURCE_CHILD, SOURCE_NONE,
                                     Note, NoteStore, pick_default_note, resolve_note)


def _note(note_id: str, text: str, label: str = "", pinned: bool = False,
          updated: str = "2026-01-01 00:00:00", archived: bool = False) -> Note:
    return Note(note_id=note_id, ref_key="doi:10.1/x", text=text, label=label,
                pinned=pinned, updated=updated, archived=archived)


def test_child_note_wins_and_reports_adopted_source():
    text, source, hit = resolve_note("子库写的", "n1", [_note("n1", "总库的")])
    assert (text, source) == ("子库写的", SOURCE_CHILD)
    assert hit is not None and hit.note_id == "n1", "命中的总库 ID 用于差异提示"


def test_master_alone_is_only_available_not_adopted():
    """总库只有一条也**不能**自动采用（C2）。"""
    text, source, hit = resolve_note("", "", [_note("n1", "唯一一条")])
    assert text == "", "未选择时子库必须为空"
    assert source == SOURCE_AVAILABLE
    assert hit is None


def test_master_multiple_without_pick_is_available():
    text, source, hit = resolve_note("", "", [_note("n1", "a"), _note("n2", "b", pinned=True)])
    assert (text, source, hit) == ("", SOURCE_AVAILABLE, None)


def test_adopted_text_does_not_follow_master_changes():
    """采用后子库保存的是独立内容：总库再改也不影响显示文本。"""
    master = _note("n1", "采用时的文本")
    adopted = "采用时的文本"                      # 采用 = 复制文本进子库
    master.text = "总库后来改了"
    text, source, hit = resolve_note(adopted, "n1", [master])
    assert text == adopted and source == SOURCE_CHILD
    assert hit is not None and hit.text == "总库后来改了", "外界变化只通过 hit 暴露给差异提示"


def test_archived_notes_are_ignored():
    text, source, _ = resolve_note("", "", [_note("n1", "已归档", archived=True)])
    assert (text, source) == ("", SOURCE_NONE)


def test_pick_default_note_still_prefers_pinned():
    """挑选器里仍可以有一个"默认高亮"的建议，但它不等于自动采用。"""
    best = pick_default_note([_note("n1", "a"), _note("n2", "b", pinned=True)])
    assert best is not None and best.note_id == "n2"


def test_store_roundtrip(tmp_path):
    store = NoteStore(directory=tmp_path)
    note = store.add("doi:1", "内容", label="标签")
    again = NoteStore(directory=tmp_path)
    loaded = again.get(note.note_id)
    assert loaded is not None and loaded.text == "内容" and loaded.label == "标签"
