# tests/test_plugin_names_unified.py
"""插件名称单一来源：`plugin.name`（语言包）为准，导航/托盘/插件列表三处一致。

用户发现（2026-10-27 截图）：插件列表显示「翻译改写工具」，导航与托盘显示「翻译改写」——
因为列表读 `plugin.json` 的 name，导航/托盘读语言包的 `page_label`/`nav.page`。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ("clipboard", "image_toolbox", "navigator", "proofread", "reference")
NAV_KEY = {"clipboard": "page_label", "image_toolbox": "page_label",
           "navigator": "nav.page", "proofread": "nav.page", "reference": "page_label"}


def _i18n(locale: str = "zh_CN"):
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService

    service = I18nService(ConfigManager(), ROOT / "src/assets/i18n")
    for plugin in PLUGINS:
        service.register_namespace(plugin, ROOT / f"src/plugins/{plugin}/i18n")
    return service


@pytest.mark.parametrize("plugin", PLUGINS)
def test_name_is_same_everywhere(plugin):
    service = _i18n()
    name = service.tr(f"{plugin}.plugin.name")
    nav = service.tr(f"{plugin}.{NAV_KEY[plugin]}")
    manifest = json.loads(
        (ROOT / f"src/plugins/{plugin}/plugin.json").read_text(encoding="utf-8"))["name"]

    assert name and name != f"{plugin}.plugin.name", f"{plugin} 缺 plugin.name 文案"
    assert name == nav, f"{plugin} 列表名「{name}」与导航名「{nav}」不一致"
    assert name == manifest, f"{plugin} 的 plugin.json name「{manifest}」与「{name}」不一致"


@pytest.mark.parametrize("plugin", PLUGINS)
def test_english_name_exists(plugin):
    data = json.loads(
        (ROOT / f"src/plugins/{plugin}/i18n/en_US.json").read_text(encoding="utf-8"))

    assert data.get("plugin.name"), f"{plugin} 缺英文 plugin.name"


def test_plugin_list_uses_i18n_name():
    source = (ROOT / "src/app/plugin_manager_page.py").read_text(encoding="utf-8")

    assert "_display_name(" in source
    assert 'key = f"{record.manifest.id}.plugin.name"' in source
    assert "QLabel(manifest.name or manifest.id)" not in source, "不要再用 manifest.name 当标题"
