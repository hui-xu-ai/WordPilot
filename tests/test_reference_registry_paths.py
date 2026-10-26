# tests/test_reference_registry_paths.py
"""registry 只存相对库名、绑定独立成文件（数据架构收敛第一步）。

改动动机（实测）：原来的 libraries.json 存**绝对路径**，把数据目录复制到别处后
registry 仍指向原路径 —— 用副本测"删除仓库"时被真实目录的句柄锁住，
用户侧看到的就是"删不掉"。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _registry(tmp_path: Path):
    from plugins.reference.libraries import LibraryRegistry

    return LibraryRegistry(root=tmp_path / "references")


def test_registry_stores_relative_name_only(tmp_path):
    registry = _registry(tmp_path)
    registry.create("论文甲")

    data = json.loads(registry.registry_path.read_text(encoding="utf-8"))
    stored = data["libraries"]["论文甲"]["path"]

    assert stored == "论文甲", f"应只存目录名，实际 {stored}"
    assert ":" not in stored and "references" not in stored


def test_active_path_resolves_under_libraries_dir(tmp_path):
    registry = _registry(tmp_path)
    registry.create("论文乙")

    path = registry.active_path()

    assert path == registry.libraries_dir / "论文乙"
    assert path.is_dir()


def test_legacy_absolute_path_is_tolerated(tmp_path):
    """历史数据里存的是绝对路径：读取时只取末级目录名。"""
    root = tmp_path / "references"
    root.mkdir(parents=True)
    (root / "libraries" / "旧库").mkdir(parents=True)
    (root / "libraries.json").write_text(json.dumps({
        "active": "旧库",
        "libraries": {"旧库": {"path": "D:/somewhere/else/libraries/旧库",
                              "last_used": 0}},
    }), encoding="utf-8")

    registry = _registry(tmp_path)

    assert registry.active_path() == root / "libraries" / "旧库"


def test_bindings_live_in_own_file(tmp_path):
    registry = _registry(tmp_path)
    registry.create("甲")
    registry.bind_document("C:/docs/论文.docx", "甲")

    assert registry.bindings_path.exists()
    data = json.loads(registry.registry_path.read_text(encoding="utf-8"))
    assert "doc_bindings" not in data, "绑定不该再写进 libraries.json"
    assert registry.doc_bindings()["C:/docs/论文.docx"] == "甲"


def test_legacy_bindings_are_migrated(tmp_path):
    """老版本把绑定存在 libraries.json 里：读到后搬到独立文件。"""
    root = tmp_path / "references"
    root.mkdir(parents=True)
    (root / "libraries" / "旧库").mkdir(parents=True)
    (root / "libraries.json").write_text(json.dumps({
        "active": "旧库",
        "libraries": {"旧库": {"path": "旧库", "last_used": 0}},
        "doc_bindings": {"C:/docs/a.docx": "旧库"},
    }), encoding="utf-8")

    registry = _registry(tmp_path)
    bindings = registry.doc_bindings()

    assert bindings == {"C:/docs/a.docx": "旧库"}
    assert registry.bindings_path.exists(), "迁移后应该有独立文件"
    data = json.loads(registry.registry_path.read_text(encoding="utf-8"))
    assert "doc_bindings" not in data, "迁移后应从 libraries.json 移除"


def test_delete_uses_libraries_dir_not_stored_path(tmp_path):
    """删除时按 libraries_dir 解析（副本数据也能删），不再被旧绝对路径带偏。"""
    registry = _registry(tmp_path)
    registry.create("甲")
    registry.create("乙")
    registry.set_active("甲")

    registry.delete("乙")

    assert not (registry.libraries_dir / "乙").exists()
    assert registry.names() == ["甲"]
