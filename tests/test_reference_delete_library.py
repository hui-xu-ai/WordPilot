# tests/test_reference_delete_library.py
"""删除仓库：移到 _trash（可回滚）、至少保留一个、删当前库后自动切换。

用户现场：默认库里被合并进两份文档的文献（294 + 13 = 307），需要能安全删掉重来。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _registry(tmp_path: Path):
    from plugins.reference.libraries import LibraryRegistry

    return LibraryRegistry(root=tmp_path / "references")


def test_delete_moves_directory_to_trash(tmp_path):
    registry = _registry(tmp_path)
    registry.create("废弃库")
    created = registry.create("要删的库")

    trash = registry.delete("要删的库")

    assert not created.exists(), "原目录应已移走"
    moved = [p for p in Path(trash).iterdir()]
    assert moved, "应该可以在 _trash 里找回"
    data = json.loads(registry.registry_path.read_text(encoding="utf-8"))
    assert "要删的库" not in data["libraries"]


def test_delete_refuses_last_library(tmp_path):
    registry = _registry(tmp_path)
    registry.create("唯一库")
    with pytest.raises(ValueError):
        registry.delete("唯一库")


def test_delete_active_switches_to_remaining(tmp_path):
    registry = _registry(tmp_path)
    registry.create("甲")
    registry.create("乙")                       # active = 乙
    registry.delete("乙")

    assert registry.active_name() == "甲"


def test_delete_clears_document_bindings(tmp_path):
    registry = _registry(tmp_path)
    registry.create("甲")
    registry.create("乙")
    registry.bind_document("C:/docs/论文.docx", "乙")

    registry.delete("乙")

    assert "C:/docs/论文.docx" not in registry.doc_bindings()
