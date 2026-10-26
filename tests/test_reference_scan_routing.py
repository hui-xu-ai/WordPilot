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


def test_new_document_gets_its_own_library(tmp_path, monkeypatch):
    """只有默认库时，新文档应按文档名新建库再导入，而不是塞进默认库。"""
    plugin = _plugin(tmp_path, monkeypatch)
    stats: dict = {}

    plugin._route_before_import("C:/docs/论文甲.docx", "论文甲.docx",
                                {"endnote:1", "endnote:2"}, stats)

    assert plugin._libraries.active_name() == "论文甲", \
        f"应新建并切到「论文甲」，实际 {plugin._libraries.active_name()}"
    assert stats.get("switch") == "new"
    assert plugin._libraries.doc_bindings()["C:/docs/论文甲.docx"] == "论文甲"


def test_second_document_does_not_share_library(tmp_path, monkeypatch):
    """第二份不同文档必须再建一个库，不能与第一份共用。"""
    plugin = _plugin(tmp_path, monkeypatch)

    plugin._route_before_import("C:/docs/甲.docx", "甲.docx", {"endnote:1"}, {})
    plugin._route_before_import("C:/docs/乙.docx", "乙.docx", {"zotero:9"}, {})

    assert plugin._libraries.active_name() == "乙"
    assert set(plugin._libraries.names()) == {"默认库", "甲", "乙"}


def test_bound_document_switches_to_bound_library(tmp_path, monkeypatch):
    """已绑定文档优先走绑定库（S11 的语义保持不变）。"""
    plugin = _plugin(tmp_path, monkeypatch, names=["默认库", "我的库"])
    plugin._libraries.bind_document("C:/docs/丙.docx", "我的库")
    stats: dict = {}

    plugin._route_before_import("C:/docs/丙.docx", "丙.docx", {"endnote:7"}, stats)

    assert plugin._libraries.active_name() == "我的库"
    assert stats.get("switch") == "switch"


def test_suggested_name_avoids_collision(tmp_path, monkeypatch):
    """同名文档重扫时不能覆盖已有库名。"""
    plugin = _plugin(tmp_path, monkeypatch, names=["默认库", "论文甲"])

    assert plugin._suggest_library_name("论文甲.docx") == "论文甲 2"
