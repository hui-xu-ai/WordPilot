# tests/test_reference_master_meta.py
"""总库备注要能独立认出是哪篇文献（方案 A：notes.csv 冗余存元数据）。

用户现场：删掉子库后，总库里只剩 `zotero:906` 这种键，完全不知道对应哪篇文献。
"""
from __future__ import annotations

import csv

import pytest


def _service(tmp_path, notes=()):
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_store import Reference, ReferenceStore

    child = ReferenceStore(directory=tmp_path / "子库")
    for key, note, title in notes:
        child.upsert(Reference(ref_key=key, title=title, authors="Zhang Z H", year="2022",
                               container="Small", doi="10.1002/1", cite_label="2", note=note))
    master = NoteStore(directory=tmp_path)
    return NoteService(child, master), child, master


def test_publish_carries_metadata(tmp_path):
    service, child, master = _service(tmp_path, [("zotero:1", "备注A", "Smart Film Actuators")])

    note = service.publish_to_master("zotero:1")

    assert note is not None
    assert note.title == "Smart Film Actuators", "发布时必须带上文献信息"
    assert note.authors and note.year and note.container and note.doi
    assert note.meta_line().startswith("Smart Film Actuators（2022）")


def test_metadata_survives_child_library_deletion(tmp_path):
    """总库文件自包含：子库（references.csv）没了，重新加载总库仍能认出是哪篇。"""
    service, child, master = _service(tmp_path, [("zotero:1", "备注A", "Smart Film Actuators")])
    service.publish_to_master("zotero:1")

    from plugins.reference.notes import NoteStore

    reloaded = NoteStore(directory=tmp_path)          # 只读总库，不碰子库
    note = reloaded.notes_all()[0]

    assert note.ref_key == "zotero:1"
    assert note.title == "Smart Film Actuators", "删子库后仍应有标题快照"


def test_old_notes_csv_without_meta_columns_still_loads(tmp_path):
    """旧版 notes.csv（只有 8 列）必须照旧可读，新字段留空。"""
    path = tmp_path / "notes.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["note_id", "ref_key", "label", "text", "pinned", "created",
                         "updated", "archived"])
        writer.writerow(["n1", "zotero:1", "备注 1", "旧备注", "0", "", "", "0"])

    from plugins.reference.notes import NoteStore

    store = NoteStore(directory=tmp_path)
    note = store.notes_all()[0]

    assert note.text == "旧备注"
    assert note.title == "" and note.doi == ""
    assert note.meta_line() == "zotero:1", "没有元数据时退回 ref_key，便于发现失忆条目"


def test_backfill_fills_blank_meta(tmp_path):
    """历史总库备注（元数据为空）能按当前子库补齐；已有元数据的不动。"""
    from plugins.reference.notes import NoteStore

    master = NoteStore(directory=tmp_path)
    master.add("zotero:1", "旧备注", label="备注 1")           # 没有元数据
    master.add("zotero:1", "另一条", label="备注 2",
               meta={"title": "已存在标题"})                    # 已有元数据

    service, child, _ = _service(tmp_path, [("zotero:1", "旧备注", "Smart Film Actuators")])
    filled = service.backfill_master_meta()

    assert filled == 1, "只补空白的那条"
    again = NoteStore(directory=tmp_path)            # 重新读盘，验证落盘结果
    titles = [note.title for note in again.notes_all()]
    assert "Smart Film Actuators" in titles
    assert "已存在标题" in titles
