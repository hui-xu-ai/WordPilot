# tests/test_reference_scan_routing.py
"""扫描入库前必须先把库路由到"该去的仓库"，否则两个文档会合并进同一个库。

复核用户现场：默认库 307 条 = Zotero 文档 294 + EndNote 文档 13；
`snapshots.json` 里两份文档的引用集合完全不同，却都写进了默认库。
原因是旧实现先 upsert 再判定，且 `_library_snapshots()` 排除当前库，
只有默认库时永远返回空 → 每次都是「新建」却已写进去了。
"""
from __future__ import annotations

from pathlib import Path

import pytest


class _FakeLibraries:
    def __init__(self, root: Path, active: str = "默认库", names=None):
        self.root = root
        self._active = active
        self._names = list(names or ["默认库"])
        self._bindings: dict[str, str] = {}

    def active_name(self) -> str:
        return self._active

    def names(self) -> list[str]:
        return list(self._names)

    @property
    def libraries_dir(self) -> Path:
        return self.root

    def create(self, name: str) -> Path:
        self._names.append(name)
        self._active = name
        return self.root / name

    def set_active(self, name: str) -> Path:
        if name not in self._names:
            raise KeyError(name)
        self._active = name
        return self.root / name

    def doc_bindings(self) -> dict:
        return dict(self._bindings)

    def bind_document(self, doc_path: str, library: str) -> None:
        self._bindings[doc_path] = library


def _plugin(tmp_path, monkeypatch, names=None):
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._libraries = _FakeLibraries(tmp_path, names=names)
    plugin._active_name = plugin._libraries.active_name()
    plugin._data_dir = tmp_path / "默认库"
    plugin._page = None
    plugin._overlay = None
    plugin._watch = None
    plugin._index = None
    plugin._scan_cache = None
    plugin._bib_cache = {}

    class _Store:
        def __init__(self, directory=None):
            self.directory = directory

    monkeypatch.setattr("plugins.reference.plugin.ReferenceStore", _Store)
    monkeypatch.setattr("plugins.reference.plugin.NoteStore", lambda directory=None: None)
    monkeypatch.setattr("plugins.reference.plugin.ReferenceIndex", lambda db_path=None: None)
    monkeypatch.setattr("plugins.reference.plugin.ScanCache", lambda db_path=None: None)
    monkeypatch.setattr("plugins.reference.plugin.SnapshotStore",
                        lambda directory=None: type("S", (), {"library_snapshot": lambda self: set()})())
    monkeypatch.setattr(ReferencePlugin, "tr", lambda self, key, **kw: key, raising=False)
    return plugin


def test_unknown_document_does_not_create_library(tmp_path, monkeypatch):
    """用户明确要求：不替用户建库 —— 未知文档只报告，不写库、不切库。"""
    plugin = _plugin(tmp_path, monkeypatch)
    stats: dict = {}

    allowed = plugin._route_before_import("C:/docs/论文甲.docx", "论文甲.docx",
                                          {"endnote:1", "endnote:2"}, stats)

    assert allowed is False, "未知文档不能直接入库"
    assert plugin._libraries.active_name() == "默认库", "不得自动切库"
    assert plugin._libraries.names() == ["默认库"], "不得自动建库"
    assert stats.get("pending") is True
    assert stats.get("suggested") == "论文甲", "只给建议名，由界面弹窗确认"
    assert "C:/docs/论文甲.docx" not in plugin._libraries.doc_bindings()


def test_bound_document_imports_into_bound_library(tmp_path, monkeypatch):
    """用户点过"关联到某库"之后：再次扫描直接入库，不再弹窗。"""
    plugin = _plugin(tmp_path, monkeypatch, names=["默认库", "论文乙"])
    plugin._libraries.bind_document("C:/docs/论文乙.docx", "论文乙")
    stats: dict = {}

    allowed = plugin._route_before_import("C:/docs/论文乙.docx", "论文乙.docx",
                                          {"endnote:5"}, stats)

    assert allowed is True
    assert plugin._libraries.active_name() == "论文乙"


def test_bind_document_helper(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch, names=["默认库", "论文丙"])

    assert plugin.bind_document("C:/docs/丙.docx", "论文丙") is True
    assert plugin._libraries.doc_bindings()["C:/docs/丙.docx"] == "论文丙"
    assert plugin.bind_document("", "论文丙") is False


