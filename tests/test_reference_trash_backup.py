# tests/test_reference_trash_backup.py
"""回收区（_trash）只留用户数据；清理时整个 _trash 都归"回收站"组。

现场：点「清空回收站」后还剩 6 个 `index.db-wal`/`index.db-shm` ——
分类时 `-wal` 规则先命中，把它们收进 `wal` 组，"清空回收站"永远碰不到。
"""
from __future__ import annotations

import json


def test_trash_files_go_to_trash_group_regardless_of_suffix(tmp_path):
    from core.services import housekeeping

    trash = tmp_path / "references" / "libraries" / "_trash" / "旧库-20260101-000000"
    trash.mkdir(parents=True)
    for name in ("index.db-wal", "index.db-shm", "references.csv", "manifest.json"):
        (trash / name).write_text("x", encoding="utf-8")

    groups = {group.key: group for group in housekeeping.inventory(tmp_path)}

    names = sorted(path.name for path in groups["trash"].files)
    assert names == ["index.db-shm", "index.db-wal", "manifest.json", "references.csv"]
    assert groups["wal"].count == 0, "回收区里的 -wal 不能再算进 wal 组"


def test_empty_trash_removes_everything_and_drops_empty_dirs(tmp_path):
    from core.services import housekeeping

    trash_root = tmp_path / "references" / "libraries" / "_trash"
    backup = trash_root / "旧库-20260101-000000"
    backup.mkdir(parents=True)
    (backup / "index.db-wal").write_text("x", encoding="utf-8")
    (backup / "references.csv").write_text("ref_key\n", encoding="utf-8")

    result = housekeeping.clean(["trash"], root=tmp_path, keep_days=0)

    assert result["trash"][0] == 2, "两个文件都要清掉"
    assert result["trash"][2] == 0, "不该有被占用的"
    assert not backup.exists(), "备份目录应被整个清掉"
    assert trash_root.exists(), "_trash 本身保留"


def test_library_delete_keeps_only_user_data(tmp_path):
    """删除仓库时，回收区只放 references.csv / notes.csv + 清单，不再整目录搬家。"""
    from plugins.reference.libraries import LibraryRegistry
    from plugins.reference.reference_store import Reference, ReferenceStore

    registry = LibraryRegistry(root=tmp_path)
    registry.ensure_default()
    name = registry.names()[0]
    directory = registry.library_dir(name)
    ReferenceStore(directory=directory).upsert(Reference(ref_key="zotero:1", title="文献1"))
    (directory / "index.db").write_text("index", encoding="utf-8")
    (directory / "doc_scan.db").write_text("scan", encoding="utf-8")
    registry.create("备用库")

    backup = registry.delete(name)

    assert backup is not None and backup.exists()
    files = sorted(path.name for path in backup.iterdir())
    assert "references.csv" in files and "manifest.json" in files
    assert "index.db" not in files and "doc_scan.db" not in files, "缓存不该进回收区"
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["library"] == name and manifest["deleted_at"]
    assert not directory.exists(), "原库目录应被删除"
