# tests/test_plugin_manifest.py
"""插件清单解析（`core.sdk.plugin_base.PluginManifest`）离线断言。

来源：`tools/smoke_p7_frozen.py:144-152`（id == 目录名）、
`tools/smoke_p7_install.py:13-15`（缺 id 必须报错、entry 拆分）。
全部零 Qt、零 COM：`from_file()` 只读给定 JSON。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.sdk.plugin_base import PluginManifest


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_missing_id_raises(tmp_path):
    with pytest.raises(ValueError):
        PluginManifest.from_file(_write(tmp_path, {"name": "无 id 的清单"}))


def test_name_defaults_to_id(tmp_path):
    manifest = PluginManifest.from_file(_write(tmp_path, {"id": "hello"}))
    assert manifest.id == "hello"
    assert manifest.name == "hello"


def test_unknown_fields_are_dropped(tmp_path):
    manifest = PluginManifest.from_file(
        _write(tmp_path, {"id": "hello", "not_a_field": 1})
    )
    assert not hasattr(manifest, "not_a_field")


def test_default_entry_is_plugin_class():
    manifest = PluginManifest(id="x")
    assert manifest.entry_module == "plugin"
    assert manifest.entry_class == "Plugin"


def test_entry_without_colon_keeps_default_class(tmp_path):
    manifest = PluginManifest.from_file(_write(tmp_path, {"id": "x", "entry": "main"}))
    assert manifest.entry_module == "main"
    assert manifest.entry_class == "Plugin"


@pytest.mark.parametrize(
    ("entry", "module", "cls"),
    [
        ("plugin:NavigatorPlugin", "plugin", "NavigatorPlugin"),
        ("entry_impl:MyPlugin", "entry_impl", "MyPlugin"),
    ],
)
def test_entry_is_split_on_first_colon(tmp_path, entry, module, cls):
    manifest = PluginManifest.from_file(_write(tmp_path, {"id": "x", "entry": entry}))
    assert (manifest.entry_module, manifest.entry_class) == (module, cls)


def test_bundled_manifests_parse_and_match_directory(plugin_dirs):
    """自带插件的清单必须可解析，且 id 与目录名一致（否则插件管理器会错配）。"""
    for directory in plugin_dirs:
        manifest = PluginManifest.from_file(directory / "plugin.json")
        assert manifest.id == directory.name, f"{directory.name} 的清单 id 不匹配"
        assert manifest.entry_class, f"{directory.name} 缺少入口类名"
