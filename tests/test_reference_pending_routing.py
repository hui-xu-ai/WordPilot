# tests/test_reference_pending_routing.py
"""R2/R13：需要用户确认时不写入；目录被占用时不硬删。

审查复现：
- R2 相似度约 0.538 判成 prompt，但 10 条参考已经进了当前库 A，也没记录绑定；
- R13 rename 持续 PermissionError 后调用并不存在的 _force_remove（AttributeError），
  而产品承诺"移到回收区可恢复"，不该退化成硬删。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class _FakeLibraries:
    def __init__(self, root: Path, active="A", names=None, bindings=None):
        self.root = root
        self._active = active
        self._names = list(names or ["A", "B"])
        self._bindings = dict(bindings or {})

    def active_name(self):
        return self._active

    def names(self):
        return list(self._names)

    @property
    def libraries_dir(self):
        return self.root

    def doc_bindings(self):
        return dict(self._bindings)

    def bind_document(self, doc_path, library):
        self._bindings[doc_path] = library

    def create(self, name):
        self._names.append(name)
        self._active = name
        return self.root / name

    def set_active(self, name):
        if name not in self._names:
            raise KeyError(name)
        self._active = name
        return self.root / name


def _plugin(tmp_path, monkeypatch, active="A", bindings=None):
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._libraries = _FakeLibraries(tmp_path, active, bindings=bindings)
    plugin._active_name = active
    plugin._data_dir = tmp_path / active
    plugin._page = None
    plugin._overlay = None
    plugin._watch = None
    plugin._index = None
    plugin._scan_cache = None
    plugin._bib_cache = {}
    plugin.tr = lambda key, **kw: key
    return plugin


def test_prompt_plan_does_not_import(tmp_path, monkeypatch):
    """R2：中等相似（prompt）时返回 False，调用方一条都不写。"""
    from plugins.reference import scan_routing

    plugin = _plugin(tmp_path, monkeypatch, active="A")
    # 让快照里 B 库与本文档有中等重合
    monkeypatch.setattr(plugin, "_library_snapshots", lambda: {"B": {"k1", "k2", "k3"}})
    stats: dict = {}

    allowed = scan_routing.route_before_import(
        plugin, "C:/docs/新.docx", "新.docx", {"k1", "k2"}, stats)

    assert allowed is False, "待确认时不允许写入"
    assert stats.get("pending") is True
    assert "plan" in stats
    assert plugin._libraries.active_name() == "A", "确认前不得切换库"


def test_switch_plan_allows_import(tmp_path, monkeypatch):
    """高度相似 → 切库并允许写入。"""
    from plugins.reference import scan_routing

    plugin = _plugin(tmp_path, monkeypatch, active="A")
    monkeypatch.setattr(plugin, "_library_snapshots", lambda: {"B": {"k1", "k2", "k3", "k4"}})

    allowed = scan_routing.route_before_import(
        plugin, "C:/docs/旧.docx", "旧.docx", {"k1", "k2", "k3", "k4"}, {})

    assert allowed is True
    assert plugin._libraries.active_name() == "B"


def test_locked_directory_keeps_library(tmp_path, monkeypatch):
    """R13：目录被占用时抛可读错误，注册表与目录都保持不变（不进硬删兜底）。"""
    from plugins.reference.libraries import LibraryRegistry

    registry = LibraryRegistry(root=tmp_path / "references")
    registry.create("甲")
    registry.create("乙")
    registry.set_active("甲")

    real_rename = Path.rename

    def _blocked(self, target):
        raise PermissionError(13, "拒绝访问")

    monkeypatch.setattr(Path, "rename", _blocked)
    try:
        with pytest.raises(ValueError):
            registry.delete("乙")
    finally:
        monkeypatch.setattr(Path, "rename", real_rename)

    assert "乙" in registry.names(), "删除失败后注册表必须保持原样"
    assert (registry.libraries_dir / "乙").exists(), "目录不能被硬删"
