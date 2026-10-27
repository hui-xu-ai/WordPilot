# tests/test_reference_recovery.py
"""S6/S7：注册表损坏恢复不得覆盖已有数据；库名不得跳出库根。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _registry(tmp_path: Path):
    from plugins.reference.libraries import LibraryRegistry

    return LibraryRegistry(root=tmp_path / "references")


def test_recovery_backs_up_existing_file_instead_of_deleting(tmp_path):
    """S6：默认库已有 references.csv，根目录又有遗留 CSV —— 两份都要保留。"""
    root = tmp_path / "references"
    from plugins.reference.libraries import default_library_name

    default = root / "libraries" / default_library_name()   # 与 LibraryRegistry 保持一致
    default.mkdir(parents=True)
    (default / "references.csv").write_text("旧的库内容\n", encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    (root / "references.csv").write_text("遗留数据\n", encoding="utf-8")

    registry = _registry(tmp_path)
    registry.ensure_default()

    current = (default / "references.csv").read_text(encoding="utf-8")
    backups = list(default.glob("references.csv.*.bak"))

    assert "遗留数据" in current or "旧的库内容" in current, "目标位置必须有文件"
    assert backups, "被替换的旧文件必须留备份，而不是直接删除"
    assert any("旧的库内容" in path.read_text(encoding="utf-8") for path in backups), \
        "备份里要能找到原内容"


@pytest.mark.parametrize("name", ["../outside", "..", "a/b", "a\\b"])
def test_library_name_cannot_escape_directory(tmp_path, name):
    """S7：库名会变成目录名，不能跳出库根。"""
    registry = _registry(tmp_path)
    with pytest.raises(ValueError):
        registry.create(name)


def test_normal_library_name_still_works(tmp_path):
    registry = _registry(tmp_path)
    path = registry.create("我的库")

    assert path.name == "我的库"
    assert path.parent == tmp_path / "references" / "libraries"
    assert json.loads(registry.registry_path.read_text(encoding="utf-8"))["active"] == "我的库"
