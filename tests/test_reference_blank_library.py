# tests/test_reference_blank_library.py
"""新建空白仓库后「同步本文」必须能装进去。

现场：手动新建空白仓库 → 点同步本文 → 弹窗（因为文档没绑定、库里也没相似文献），
选「保持当前不动」也什么都不发生，空白仓库永远是空的。
"""
from __future__ import annotations

import pytest


class _FakeLibraries:
    def __init__(self, root, names, active):
        self.root = root
        self._names = list(names)
        self._active = active
        self._bindings: dict[str, str] = {}

    @property
    def libraries_dir(self):
        return self.root

    def names(self):
        return list(self._names)

    def active_name(self):
        return self._active

    def doc_bindings(self):
        return dict(self._bindings)

    def bind_document(self, doc_path, library):
        self._bindings[doc_path] = library

    def set_active(self, name):
        self._active = name
        return self.root / name


class _Store:
    def __init__(self, refs):
        self._refs = list(refs)

    def all(self):
        return list(self._refs)


def _plugin(tmp_path, monkeypatch, refs):
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._libraries = _FakeLibraries(tmp_path, ["空白库", "旧库"], "空白库")
    plugin._active_name = "空白库"
    plugin._store = _Store(refs)
    plugin.tr = lambda key, **kw: key
    monkeypatch.setattr(plugin, "_library_snapshots", lambda: {"旧库": {"a", "b"}}, raising=False)
    return plugin


def test_blank_library_accepts_new_document(tmp_path, monkeypatch):
    """当前库是空的 → 直接用它，不弹窗、不再要求用户建库。"""
    plugin = _plugin(tmp_path, monkeypatch, refs=[])
    stats: dict = {}

    allowed = plugin._route_before_import("C:/docs/新论文.docx", "新论文.docx",
                                          {"endnote:1", "endnote:2"}, stats)

    assert allowed is True, "空白库应直接收货"
    assert plugin._libraries.doc_bindings()["C:/docs/新论文.docx"] == "空白库"
    assert stats.get("switch_text") == "switch.into_blank"


def test_non_blank_library_still_asks(tmp_path, monkeypatch):
    """当前库已有内容且文档不认识 → 仍然交给用户决定（不自动建库）。"""
    plugin = _plugin(tmp_path, monkeypatch, refs=[object()])
    stats: dict = {}

    allowed = plugin._route_before_import("C:/docs/别人的论文.docx", "别人的论文.docx",
                                          {"endnote:9"}, stats)

    assert allowed is False
    assert stats.get("pending") is True
    assert "C:/docs/别人的论文.docx" not in plugin._libraries.doc_bindings()


def test_stay_choice_imports_into_current_library(tmp_path):
    """「保持当前不动」= 装进当前库（原来是啥也不做）。"""
    from PySide6.QtWidgets import QApplication

    from plugins.reference.library_page import LibraryPage
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import ReferenceStore

    app = QApplication.instance() or QApplication([])
    store = ReferenceStore(directory=tmp_path / "空白库")
    index = ReferenceIndex(db_path=tmp_path / "空白库" / "index.db")

    class _Plugin:
        _active_name = "空白库"
        _store = store
        _index = index
        _note_service = NoteService(store, NoteStore(directory=tmp_path))
        _libraries = type("L", (), {"names": lambda self: ["空白库"],
                                    "bind_document": lambda self, p, n: None})()

        def __init__(self):
            self.bound: list[tuple[str, str]] = []

        def bind_document(self, doc_path, library):
            self.bound.append((doc_path, library))
            return True

    plugin = _Plugin()

    class _Ctx:
        def tr(self, key, /, **params):
            return key

        def get_service(self, name):
            return None

    page = LibraryPage(_Ctx(), store, index, plugin=plugin,
                       note_service=plugin._note_service)
    rescans: list[str] = []
    page.scan_document = lambda silent=False: rescans.append("scan")

    class _Plan:
        action = "new"
        library = ""
        score = 0.0

    import plugins.reference.switch_dialog as dialog_module

    class _Dialog:
        def __init__(self, *a, **k):
            self.choice = "stay"
            self.new_name = ""
            self.name_edit = type("E", (), {"setText": lambda self, t: None,
                                            "setVisible": lambda self, v: None})()

        def exec(self):
            return 0

    original = dialog_module.SwitchDialog
    dialog_module.SwitchDialog = _Dialog
    try:
        page.handle_switch_plan(_Plan(), doc_path="C:/docs/新.docx", suggested="新")
    finally:
        dialog_module.SwitchDialog = original

    assert plugin.bound == [("C:/docs/新.docx", "空白库")], "应把文档绑到当前库"
    assert rescans == ["scan"], "绑定后应重新扫描导入"
    index.close()
    page.deleteLater()
