# tests/test_prompt_version_labels.py
"""版本条的标签必须能在**插件命名空间**里查到（否则下拉显示 `clipboard.prompt.version.default`）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PLUGIN_KEYS = ("prompt.version.default", "prompt.version.active", "prompt.version.save",
               "prompt.version.delete", "prompt.version.reset", "prompt.version.title",
               "prompt.version.name", "prompt.version.empty",
               "prompt.version.delete_confirm", "prompt.version.last")


@pytest.mark.parametrize("plugin", ["clipboard", "proofread"])
@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_plugin_i18n_has_all_version_keys(plugin, locale):
    path = ROOT / f"src/plugins/{plugin}/i18n/{locale}.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    missing = [key for key in PLUGIN_KEYS if key not in data]
    assert not missing, f"{plugin}/{locale} 缺少版本条文案：{missing}"


def test_labels_resolve_through_plugin_namespace():
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService

    i18n = I18nService(ConfigManager(), ROOT / "src/assets/i18n")
    i18n.register_namespace("clipboard", ROOT / "src/plugins/clipboard/i18n")

    label = i18n.tr("clipboard.prompt.version.default")

    assert label and "prompt.version" not in label, f"标签没翻译：{label}"
