# tests/test_asset_registry.py
"""资产保护接口：插件自己声明资产，清理模块只认登记表（互不认识）。

用户要求（2026-10-27）：白名单要留接口，注册插件时由插件提供保护清单。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clean_registry():
    from core.services import housekeeping

    before = dict(housekeeping._ASSET_SOURCES)
    yield
    housekeeping._ASSET_SOURCES.clear()
    housekeeping._ASSET_SOURCES.update(before)


def test_plugin_declared_asset_is_never_cleaned(tmp_path):
    from core.services import housekeeping

    (tmp_path / "app.log").write_text("log", encoding="utf-8")
    keep = tmp_path / "plugin_state.json"
    keep.write_text("{}", encoding="utf-8")

    housekeeping.register_asset_source("demo", lambda: [keep])

    assert keep in housekeeping.declared_assets(tmp_path)
    result = housekeeping.clean(["logs", "wal", "cache", "trash"], root=tmp_path)

    assert keep.exists(), "插件声明的资产不能被清理"
    assert not (tmp_path / "app.log").exists()
    assert result["logs"][0] == 1


def test_directory_asset_protects_everything_inside(tmp_path):
    from core.services import housekeeping

    data = tmp_path / "plugin_data"
    data.mkdir()
    inside = data / "proof_cache.db"          # 名字像缓存，但声明了目录就不能删
    inside.write_text("x", encoding="utf-8")

    housekeeping.register_asset_source("demo", lambda: [data])
    housekeeping.clean(["cache"], root=tmp_path)

    assert inside.exists()


def test_unregister_drops_protection(tmp_path):
    from core.services import housekeeping

    keep = tmp_path / "state.json"
    keep.write_text("{}", encoding="utf-8")
    housekeeping.register_asset_source("demo", lambda: [keep])
    housekeeping.unregister_asset_source("demo")

    assert keep not in housekeeping.declared_assets(tmp_path)
    assert housekeeping.asset_sources() == []


def test_plugin_base_default_assets_is_empty():
    from core.sdk.plugin_base import PluginBase

    assert list(PluginBase().assets()) == []


def test_reference_plugin_declares_its_libraries(tmp_path):
    from plugins.reference.libraries import LibraryRegistry
    from plugins.reference.plugin import ReferencePlugin

    plugin = ReferencePlugin.__new__(ReferencePlugin)
    plugin._libraries = LibraryRegistry()      # 用真实注册表（读真实 config 只读）
    plugin._libraries.ensure_default()

    assets = [str(path) for path in plugin.assets()]

    assert any(path.endswith("references.csv") for path in assets), assets
    assert any(path.endswith("notes.csv") for path in assets), assets
    assert not any(path.endswith("doc_scan.db") for path in assets), "缓存不该当资产"


def test_image_toolbox_declares_its_workdoc():
    from plugins.image_toolbox.plugin import CONFIG_FILE, ImageToolboxPlugin

    plugin = ImageToolboxPlugin.__new__(ImageToolboxPlugin)

    assert CONFIG_FILE in list(plugin.assets())
