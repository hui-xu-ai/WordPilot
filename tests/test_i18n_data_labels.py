# tests/test_i18n_data_labels.py
"""数据级文案（不是 UI 字面量，但会显示在界面上）也要跟随语言。

现场（2026-10-27 截图）：
- 参考文献页的库下拉显示「默认库」；
- API 监控「任务」列显示 逐句校对 / 翻译 / 改写；
- 设置页皮肤名「栗子柯基」。
这些值来自数据层/清单文件，源码审计抓不到，必须单独守。
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
    from core.services.i18n import I18nService

    class _Config:
        def __init__(self):
            self.data = {"ui_language": language, "prompt_language": "auto"}

        def get(self, key, default=None):
            return self.data.get(key, default)

    return I18nService(_Config(), ROOT / "src/assets/i18n")


def test_default_library_name_follows_language(monkeypatch):
    from core.services import i18n as i18n_mod
    from plugins.reference.libraries import default_library_name

    i18n_mod.set_message_service(_i18n("en_US"))
    assert default_library_name() == "Default library"

    i18n_mod.set_message_service(_i18n("zh_CN"))
    assert default_library_name() == "默认库"


def test_logger_task_names_are_keys():
    from core.services import api_logger

    for action in ("proof", "correct", "translate", "rewrite"):
        _plugin, task = api_logger.ACTION_OWNER[action]
        assert task.startswith("task."), f"{action} 的任务名应是 i18n key，实际 {task}"


def test_monitor_translates_task_labels(app):
    from core.widgets.api_monitor import ApiMonitorPanel

    panel = ApiMonitorPanel(i18n=_i18n("en_US"))

    assert panel._task_label("task.translate") == "Translation"
    assert panel._task_label("翻译") == "Translation", "旧日志里的中文任务名也要翻译"


@pytest.mark.parametrize("locale,expected", [("zh_CN", "翻译"), ("en_US", "Translation")])
def test_task_keys_exist(locale, expected):
    data = json.loads((ROOT / f"src/assets/i18n/{locale}.json").read_text(encoding="utf-8"))

    assert data["task.translate"] == expected


def test_skin_names_follow_language():
    from core.services.config_manager import ConfigManager
    from core.services.skin_service import SkinService

    config = ConfigManager()
    names = {}
    for language in ("zh_CN", "en_US"):
        config.set("ui_language", language)
        service = SkinService(config, ROOT / "src/assets/skins")
        service._i18n = _i18n(language)
        names[language] = [item["name"] for item in service.available()]

    assert names["zh_CN"] != names["en_US"], "皮肤名没有跟随语言"
    assert all(name.isascii() for name in names["en_US"]), names["en_US"]


def test_theme_names_exist():
    zh = json.loads((ROOT / "src/assets/i18n/zh_CN.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / "src/assets/i18n/en_US.json").read_text(encoding="utf-8"))

    for theme_id in ("light", "dark"):
        assert zh[f"theme.{theme_id}"] and en[f"theme.{theme_id}"]
