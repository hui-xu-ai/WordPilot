"""S1/S11/S2：文献库归属一致性 + 指纹变化重扫。

审查证据（2026-10-25-strict-audit）：
- S1 新建库后 registry.active 已变，switch_library 却因"name == active_name()"提前返回，
  实际 Store 没切，新条目写进旧库；
- S11 文档绑定 B、当前在 A 时，扫描先往 A 写入再切 B，A 被污染；
- S2 更新文件指纹时保留了 bibliography_done，改过的新版本被判"已导入"。
"""
from __future__ import annotations

from pathlib import Path

import pytest


class _FakeLibraries:
    """最小 LibraryRegistry 替身：只关心 name/路径/绑定。"""

    def __init__(self, root: Path, active: str = "A", bindings=None):
        self.root = root
        self._active = active
        self._names = ["A", "B"]
        self._bindings = dict(bindings or {})
        self.created: list[str] = []

    def active_name(self) -> str:
        return self._active

    def active_path(self) -> Path:
        return self.root / self._active

    def names(self) -> list[str]:
        return list(self._names)

    def set_active(self, name: str) -> Path:
        if name not in self._names:
            raise KeyError(name)
        self._active = name
        return self.root / name

    def create(self, name: str) -> Path:
        self._names.append(name)
        self._active = name
        self.created.append(name)
        return self.root / name

    def doc_bindings(self) -> dict:
        return dict(self._bindings)

    def bind_document(self, doc_path: str, library: str) -> None:
        self._bindings[doc_path] = library

    @property
    def libraries_dir(self) -> Path:
        return self.root


def _make_plugin(monkeypatch, tmp_path, active="A", bindings=None):
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)      # 跳过 Qt 激活
    plugin._libraries = _FakeLibraries(tmp_path, active, bindings)
    plugin._data_dir = plugin._libraries.active_path()
    plugin._active_name = active
    plugin._index = None
    plugin._scan_cache = None
    plugin._store = None
    plugin._notes = None
    plugin._note_service = None
    plugin._watch = None
    plugin._page = None
    plugin._overlay = None
    plugin._bib_cache = {}

    class _Store:
        def __init__(self, directory=None):
            self.directory = directory

        def upsert(self, reference):
            raise AssertionError("测试不应写入")

    class _Notes:
        def __init__(self, directory=None):
            self.directory = directory

    class _Index:
        def __init__(self, db_path=None):
            self.db_path = db_path
            self.closed = False

        def close(self):
            self.closed = True

    class _Cache:
        def __init__(self, db_path=None):
            self.db_path = db_path
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr("plugins.reference.plugin.ReferenceStore", _Store)
    monkeypatch.setattr("plugins.reference.plugin.NoteStore", _Notes)
    monkeypatch.setattr("plugins.reference.plugin.ReferenceIndex", _Index)
    monkeypatch.setattr("plugins.reference.plugin.ScanCache", _Cache)
    return plugin


def test_create_library_switches_actual_store(monkeypatch, tmp_path):
    """S1：新建库必须真的切过去（此前 registry 已变、Store 未变）。"""
    plugin = _make_plugin(monkeypatch, tmp_path, active="A")

    plugin.create_library("C")

    assert plugin._active_name == "C", "新建后实际库应切到 C"
    assert plugin._data_dir.name == "C", f"Store 目录仍是 {plugin._data_dir}"


def test_switch_library_reopens_when_registry_already_changed(monkeypatch, tmp_path):
    """S1 的成因：registry 已被 create 改成新库时，仍要真正打开新目录。"""
    plugin = _make_plugin(monkeypatch, tmp_path, active="A")
    plugin._libraries.set_active("B")          # 模拟 create() 提前改了 registry

    plugin.switch_library("B")

    assert plugin._data_dir.name == "B", "registry 已变也必须重开数据文件"


def test_scan_targets_bound_library_first(monkeypatch, tmp_path):
    """S11：文档绑定 B、当前在 A 时，扫描前必须先切到 B。"""
    from plugins.reference.plugin import ReferencePlugin

    plugin = _make_plugin(monkeypatch, tmp_path, active="A",
                          bindings={"C:/docs/论文.docx": "B"})
    calls: list[str] = []

    monkeypatch.setattr(ReferencePlugin, "switch_library",
                        lambda self, name: (calls.append(name),
                                            setattr(self, "_active_name", name)))

    plugin._ensure_scan_library("C:/docs/论文.docx")

    assert calls == ["B"], f"应切到绑定库 B，实际 {calls}"


def test_scan_keeps_current_library_when_unbound(monkeypatch, tmp_path):
    """未绑定文档：不切库，避免无谓抖动。"""
    from plugins.reference.plugin import ReferencePlugin

    plugin = _make_plugin(monkeypatch, tmp_path, active="A")
    calls: list[str] = []
    monkeypatch.setattr(ReferencePlugin, "switch_library",
                        lambda self, name: calls.append(name))

    plugin._ensure_scan_library("C:/docs/另一篇.docx")

    assert calls == [], "未绑定文档不应切库"


def test_anchor_save_resets_bibliography_flag_when_file_changed(tmp_path):
    """S2：文件变了就不能再认为"参考文献列表已导入"。"""
    from plugins.reference.scan_cache import ScanCache

    cache = ScanCache(db_path=tmp_path / "doc_scan.db")
    path = "C:/docs/论文.docx"
    cache.mark_bibliography(path, (1, 100), "论文.docx")
    assert cache.bibliography_done(path, (1, 100)) is True

    cache.save_anchors(path, (2, 200), "论文.docx", [])     # 文件已修改
    assert cache.bibliography_done(path, (2, 200)) is False, "新版本不该被判为已导入"
    cache.close()

def test_index_close_from_other_thread(tmp_path):
    """S9：扫描线程（QThread）会关主线程建的索引连接，不能抛 ProgrammingError。"""
    import threading

    from plugins.reference.reference_index import ReferenceIndex

    index = ReferenceIndex(db_path=tmp_path / "index.db")
    index._connect()                       # 主线程建立连接
    errors: list[BaseException] = []

    def _close():
        try:
            index.close()
        except BaseException as exc:       # noqa: BLE001 - 就是要抓住它
            errors.append(exc)

    worker = threading.Thread(target=_close)
    worker.start()
    worker.join(5)

    assert errors == [], f"跨线程关闭不应报错：{errors}"
    assert index._conn is None
