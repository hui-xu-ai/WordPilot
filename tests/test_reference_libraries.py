# tests/test_reference_libraries.py
"""仓库（子库）注册表：首次迁移旧数据、建库、切换当前仓库。"""
from __future__ import annotations

import json

import pytest

from plugins.reference.libraries import DEFAULT_NAME, LibraryRegistry


@pytest.fixture
def root(tmp_path):
    return tmp_path / "references"


def test_ensure_default_migrates_legacy_files(root):
    """旧版把四个文件放在根目录：首次启动要**移动**进默认库，内容不丢。"""
    root.mkdir(parents=True)
    (root / "references.csv").write_text("ref_key\ndoi:1\n", encoding="utf-8-sig")
    (root / "anchors.csv").write_text("doc_name\n论文.docx\n", encoding="utf-8-sig")
    (root / "notes.csv").write_text("note_id\nn1\n", encoding="utf-8-sig")

    registry = LibraryRegistry(root=root)
    name = registry.ensure_default()

    assert name == DEFAULT_NAME
    target = root / "libraries" / DEFAULT_NAME
    assert (target / "references.csv").read_text(encoding="utf-8-sig") == "ref_key\ndoi:1\n"
    assert (target / "notes.csv").exists()
    assert not (root / "references.csv").exists(), "旧文件应被移动而不是复制"
    assert registry.active_name() == DEFAULT_NAME


def test_ensure_default_is_idempotent(root):
    registry = LibraryRegistry(root=root)
    registry.ensure_default()
    first = registry.registry_path.read_text(encoding="utf-8")
    registry.ensure_default()
    assert registry.registry_path.read_text(encoding="utf-8") == first


def test_create_and_switch(root):
    registry = LibraryRegistry(root=root)
    registry.ensure_default()

    path = registry.create("论文A")
    assert path.is_dir()
    assert registry.active_name() == "论文A"
    assert set(registry.names()) == {DEFAULT_NAME, "论文A"}

    registry.set_active(DEFAULT_NAME)
    assert registry.active_name() == DEFAULT_NAME
    assert registry.active_path().name == DEFAULT_NAME


def test_switch_to_unknown_library_raises(root):
    registry = LibraryRegistry(root=root)
    registry.ensure_default()
    with pytest.raises(KeyError):
        registry.set_active("不存在的库")


def test_registry_json_shape(root):
    registry = LibraryRegistry(root=root)
    registry.ensure_default()
    data = json.loads(registry.registry_path.read_text(encoding="utf-8"))
    assert data["active"] == DEFAULT_NAME
    assert DEFAULT_NAME in data["libraries"]
    assert "last_used" in data["libraries"][DEFAULT_NAME]
