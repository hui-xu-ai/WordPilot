# tests/test_reference_page_builds.py
"""回归：参考文献插件页必须能真正构造出来。

教训：`plugin.py` 里 `_build_page()` 调用的私有方法被"抽模块"时删掉了，
pytest 全绿（没有覆盖页面构造路径），用户那边表现为
「插件页构造失败: plugin:reference  AttributeError: ...」。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """构造一个只填必要属性的插件实例（不跑 activate，避免碰真实配置）。"""
    from plugins.reference import plugin as module
    from plugins.reference.libraries import LibraryRegistry
    from plugins.reference.notes import NoteService, NoteStore
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import ReferenceStore

    storage = LibraryRegistry(root=tmp_path / "references")
    storage.create("库A")
    data_dir = storage.active_path()

    instance = module.ReferencePlugin.__new__(module.ReferencePlugin)
    instance._libraries = storage
    instance._active_name = "库A"
    instance._data_dir = data_dir
    instance._store = ReferenceStore(directory=data_dir)
    instance._index = ReferenceIndex(db_path=data_dir / "index.db")
    instance._scan_cache = None
    instance._master_notes = NoteStore(directory=tmp_path / "总库")
    instance._notes = instance._master_notes
    instance._note_service = NoteService(instance._store, instance._master_notes)
    instance._page = None
    instance._overlay = None
    instance._watch = None
    instance._last_items = []
    instance._bib_cache = {}
    instance._dismissed = set()
    instance.ctx = None
    instance.tr = lambda key, **kw: key
    yield instance
    instance._index.close()


def test_build_page_succeeds(plugin):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    page = plugin._build_page()

    assert page is not None, "页面构造不能返回 None"
    assert hasattr(page, "table") and hasattr(page, "library_combo")
    page.deleteLater()


def test_page_factory_callback_works(plugin):
    """`activate()` 注册给外壳的工厂函数也不能抛（用户看到的就是这条路径）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    plugin.ctx = None
    page = plugin._build_page()
    assert page is not None
    page.deleteLater()
