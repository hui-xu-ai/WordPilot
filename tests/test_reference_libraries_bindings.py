# tests/test_reference_libraries_bindings.py
"""文档↔仓库绑定记忆：手动改绑/自动判定后，同路径文档下次直接命中，不再算相似度。"""
from __future__ import annotations

import pytest

from plugins.reference.libraries import LibraryRegistry


@pytest.fixture
def registry(tmp_path):
    instance = LibraryRegistry(root=tmp_path / "references")
    instance.ensure_default()
    return instance


def test_bind_and_read_document(registry):
    registry.bind_document("C:/论文.docx", "默认库")
    assert registry.doc_bindings() == {"C:/论文.docx": "默认库"}


def test_rebind_overwrites(registry):
    registry.bind_document("C:/论文.docx", "默认库")
    registry.bind_document("C:/论文.docx", "论文A")
    assert registry.doc_bindings()["C:/论文.docx"] == "论文A"


def test_binding_survives_reload(registry):
    registry.bind_document("C:/论文.docx", "默认库")
    assert LibraryRegistry(root=registry.root).doc_bindings() == {"C:/论文.docx": "默认库"}


def test_empty_path_is_ignored(registry):
    registry.bind_document("", "默认库")
    assert registry.doc_bindings() == {}
