# tests/test_reference_publish_identity.py
"""发布到总库要按**身份码（ref_key）唯一定位**：同内容不新增，只刷新文献信息快照。

用户口径（2026-10-27）：发布时记下的身份码是固定不变的，更新时按身份码唯一更新总库。
"""
from __future__ import annotations


def _service(tmp_path, title="Smart Film Actuators", note="备注A"):
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_store import Reference, ReferenceStore

    child = ReferenceStore(directory=tmp_path / "子库")
    child.upsert(Reference(ref_key="zotero:1", title=title, authors="Zhang Z H",
                           year="2022", note=note))
    master = NoteStore(directory=tmp_path)
    return NoteService(child, master), child, master


def test_second_publish_updates_instead_of_adding(tmp_path):
    service, child, master = _service(tmp_path)

    first = service.publish_to_master("zotero:1")
    second = service.publish_to_master("zotero:1")

    assert first is not None and second is not None
    assert first.note_id == second.note_id, "同一身份码 + 同内容 → 应是同一条备注"
    assert len(master.notes_all()) == 1, "不能新增重复条目"


def test_republish_refreshes_metadata_snapshot(tmp_path):
    """子库把标题改对后再发布：总库那条的元数据要跟着更新。"""
    service, child, master = _service(tmp_path)
    note = service.publish_to_master("zotero:1")
    assert note.title == "Smart Film Actuators"

    reference = child.get("zotero:1")
    reference.title = "薄膜致动器（更正后的中文标题）"
    reference.year = "2023"
    child.upsert(reference)

    updated = service.publish_to_master("zotero:1")

    assert len(master.notes_all()) == 1
    assert updated.note_id == note.note_id
    assert updated.title == "薄膜致动器（更正后的中文标题）"
    assert updated.year == "2023"


def test_publish_all_also_refreshes_existing(tmp_path):
    service, child, master = _service(tmp_path)
    service.publish_to_master("zotero:1")
    reference = child.get("zotero:1")
    reference.authors = "Zhang Z H; Wang Y"
    child.upsert(reference)

    published, duplicate, empty = service.publish_all_to_master("子库")

    assert (published, duplicate, empty) == (0, 1, 0)
    assert len(master.notes_all()) == 1, "整库发布也不该新增重复"
    assert master.notes_all()[0].authors == "Zhang Z H; Wang Y", "整库发布同样刷新元数据"


def test_different_text_is_still_a_separate_note(tmp_path):
    """不同内容的备注仍然可以并存（总库"一篇多备注"的能力不能被去重误伤）。"""
    service, child, master = _service(tmp_path)
    service.publish_to_master("zotero:1")

    reference = child.get("zotero:1")
    reference.note = "备注B（另一个角度）"
    child.upsert(reference)
    service.publish_to_master("zotero:1")

    assert len(master.notes_all()) == 2
