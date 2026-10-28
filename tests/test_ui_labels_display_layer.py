# tests/test_ui_labels_display_layer.py
"""前端显示层：界面文案与后端数据解耦（用户 2026-10-28 要求）。

约定：
- 后端数据（库名、任务 key、版本 id、皮肤 id）保持语言无关；
- 界面显示统一经 `core.services.ui_labels` 映射，换语言只改映射，不动数据。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _i18n(language: str):
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService

    class _Config(ConfigManager):
        def __init__(self):
            super().__init__()
            self.set("ui_language", language)

    return I18nService(_Config(), ROOT / "src/assets/i18n")


def test_library_label_translates_but_keeps_unknown_names():
    from core.services.ui_labels import library_label

    zh, en = _i18n("zh_CN"), _i18n("en_US")

    assert library_label("默认库", zh.tr) == "默认库"
    assert library_label("默认库", en.tr) == "Default library"
    assert library_label("My own library", en.tr) == "My own library", "自建库名不该被翻译"


def test_task_and_version_labels():
    from core.services.ui_labels import task_label, version_label

    en = _i18n("en_US")

    assert task_label("task.translate", en.tr) == "Translation"
    assert task_label("翻译", en.tr) == "Translation", "旧日志里的中文任务名也要翻译"
    assert version_label("默认", en.tr) == "Default"
    assert version_label("mine-2", en.tr) == "My version 2"
    assert version_label("我的版本 3", en.tr) == "My version 3"
    assert version_label("auto-20261028-001122", en.tr) == "Auto backup 20261028-001122"


def test_skin_label(app):
    from core.services.ui_labels import skin_label

    en = _i18n("en_US")

    assert skin_label("chestnut_corgi", "栗子柯基", en.tr) == "Chestnut corgi"


def test_library_combo_shows_translation_but_keeps_backend_value(app):
    from PySide6.QtWidgets import QComboBox

    from core.services.ui_labels import library_label

    en = _i18n("en_US")

    class _Host:
        _libraries = ["默认库", "My own library"]
        _active_library = "默认库"
        tr = staticmethod(en.tr)

        def __init__(self):
            self._on_switch_library = None

        def _on_library_changed(self, _name):
            return None

    from plugins.reference.page_libraries import LibraryMixin

    combo = LibraryMixin.build_library_combo(
        _Host(), type("T", (), {"addWidget": lambda *a, **k: None})())

    assert combo.itemText(0) == "Default library", combo.itemText(0)
    assert combo.itemData(0) == "默认库", "后端值必须保持不变"
    assert combo.itemText(1) == "My own library"
    assert combo.currentData() == "默认库"


def test_page_libraries_uses_item_data_for_switch():
    source = (ROOT / "src/plugins/reference/page_libraries.py").read_text(encoding="utf-8")

    assert "combo.addItem(library_label(name, tr), name)" in source
    assert "backend = combo.currentData()" in source, "切换库必须用 itemData，不能用显示文案"


def test_library_page_rebuilds_combo_on_language_switch():
    text = (ROOT / "src/plugins/reference/library_page.py").read_text(encoding="utf-8")
    retranslate = text[text.index("def retranslateUi"):]

    assert "rebuild_library_combo()" in retranslate


def test_display_mapping_is_single_place():
    """映射表集中在 ui_labels，别处不该再各写一套。"""
    source = (ROOT / "src/core/services/ui_labels.py").read_text(encoding="utf-8")
    assert "LIBRARY_ALIASES" in source and "TASK_KEYS" in source

    monitor = (ROOT / "src/core/widgets/api_monitor.py").read_text(encoding="utf-8")
    assert "ui_labels" in monitor, "监控面板应复用共享映射"
    bar = (ROOT / "src/core/widgets/prompt_version_bar.py").read_text(encoding="utf-8")
    assert "ui_labels" in bar, "版本条应复用共享映射"
