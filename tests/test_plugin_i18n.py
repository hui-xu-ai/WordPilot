# tests/test_plugin_i18n.py
"""语言包完整性离线断言。

来源：`tools/smoke_p7_frozen.py:120-160`（核心包必备键、插件双语包、中文词条 ⊆ 英文词条）。
纯 JSON 读取，不启动 Qt、不读用户配置。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _keys(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8")))


#: 核心语言包里外壳依赖的键（缺一个就会在标题栏/设置页露出裸 key）
CORE_REQUIRED_KEYS = [
    "app.titlebar.minimize",
    "app.titlebar.maximize",
    "app.titlebar.close",
    "app.proof.group",
    "settings.saved_hint",
]


def test_core_language_packs_exist(assets_dir):
    for lang in ("zh_CN", "en_US"):
        assert (assets_dir / "i18n" / f"{lang}.json").is_file(), f"缺少核心语言包 {lang}"


@pytest.mark.parametrize("key", CORE_REQUIRED_KEYS)
def test_core_language_pack_has_key(key, assets_dir):
    for lang in ("zh_CN", "en_US"):
        keys = _keys(assets_dir / "i18n" / f"{lang}.json")
        assert key in keys, f"{lang} 缺少词条 {key}"


def test_core_chinese_covered_by_english(assets_dir):
    """中文词条必须都有英文对照（英文缺失会在 EN 界面露中文）。"""
    zh = _keys(assets_dir / "i18n" / "zh_CN.json")
    en = _keys(assets_dir / "i18n" / "en_US.json")
    assert not (zh - en), f"英文缺词条：{sorted(zh - en)[:5]}"


@pytest.mark.parametrize("lang", ["zh_CN", "en_US"])
def test_plugin_language_packs_exist(plugin_dirs, lang):
    missing = [d.name for d in plugin_dirs if not (d / "i18n" / f"{lang}.json").is_file()]
    assert not missing, f"以下插件缺少 {lang} 语言包：{missing}"


def test_plugin_chinese_covered_by_english(plugin_dirs):
    problems = {}
    for directory in plugin_dirs:
        zh_path = directory / "i18n" / "zh_CN.json"
        en_path = directory / "i18n" / "en_US.json"
        if not (zh_path.is_file() and en_path.is_file()):
            continue
        missing = _keys(zh_path) - _keys(en_path)
        if missing:
            problems[directory.name] = sorted(missing)[:5]
    assert not problems, f"插件英文缺词条：{problems}"
