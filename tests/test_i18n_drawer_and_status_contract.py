# tests/test_i18n_drawer_and_status_contract.py
"""抽屉与状态栏的语言契约（2026-10-28 现场两个洞）：

1) 翻译改写插件的抽屉由**插件**构建（控件与 `_prompt_bars` 都在插件对象上），
   外壳只重译页面 → 抽屉里的中英切换从不生效。现在抽屉暴露 `retranslateUi()`，
   外壳遍历 `_side_panel_widgets` 调用；
2) 提示词正文是 `QTextEdit`，之前的往返快照只比 QLabel/按钮 → 正文的盲区。
   这里把 QTextEdit 正文纳入快照。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Config:
    def __init__(self, language: str = "zh_CN"):
        self.data = {"ui_language": language, "translate_language": "auto",
                     "correct_language": "auto", "rewrite_strength": "中"}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def update(self, payload):
        self.data.update(payload)
        return True

    def save(self):
        return True


def _i18n(config):
    from core.services.i18n import I18nService

    service = I18nService(config, ROOT / "src/assets/i18n")
    service.register_namespace("clipboard", ROOT / "src/plugins/clipboard/i18n")
    return service


class _Ctx:
    def __init__(self, config, i18n):
        self.i18n = i18n
        self.config = config
        self.events = type("E", (), {"emit": staticmethod(lambda *a, **k: None)})()

    def tr(self, key, /, **params):
        return self.i18n.tr(f"clipboard.{key}", **params)

    def get_service(self, name):
        return {"i18n": self.i18n, "config": self.config}.get(name)


def _texts(widget) -> set[str]:
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QLabel, QLineEdit,
                                   QTextEdit)

    found = {label.text() for label in widget.findChildren(QLabel)}
    found |= {button.text() for button in widget.findChildren(QAbstractButton)}
    for combo in widget.findChildren(QComboBox):
        found |= {combo.itemText(i) for i in range(combo.count())}
    for edit in widget.findChildren(QTextEdit):
        found.add(edit.toPlainText())
        found.add(edit.placeholderText())
    for line in widget.findChildren(QLineEdit):
        found.add(line.text())
        found.add(line.placeholderText())
    return {text for text in found if text}


def _build_panel(app):
    from plugins.clipboard.plugin import ClipboardPlugin

    config = _Config("zh_CN")
    i18n = _i18n(config)
    plugin = ClipboardPlugin.__new__(ClipboardPlugin)
    plugin.ctx = _Ctx(config, i18n)
    plugin._side_panel = None
    plugin._prompt_bars = []
    plugin._card_labels = []
    panel = plugin._build_side_panel()
    assert panel is not None, "抽屉必须能构造"
    return plugin, panel, i18n


def test_drawer_exposes_retranslate(app):
    _plugin, panel, _i18n_service = _build_panel(app)

    assert hasattr(panel, "retranslateUi"), "抽屉必须暴露 retranslateUi（外壳按此重译）"


def test_drawer_roundtrip_including_prompt_text(app):
    _plugin, panel, i18n = _build_panel(app)

    panel.retranslateUi()
    baseline = _texts(panel)
    assert baseline, "抽屉里没有任何文字，测试无效"

    i18n.set_language("en_US")
    panel.retranslateUi()
    english = _texts(panel)
    offenders = [text for text in english if CJK.search(text)]
    assert not offenders, f"英文下抽屉里仍有中文：{offenders[:5]}"

    i18n.set_language("zh_CN")
    panel.retranslateUi()
    assert _texts(panel) == baseline, "切回中文后抽屉文字与最初不一致"


def test_shell_retranslates_side_panels():
    main = (ROOT / "src/app/main_window.py").read_text(encoding="utf-8")
    mixin = (ROOT / "src/app/pages_mixin.py").read_text(encoding="utf-8")

    assert "retranslate_side_panels()" in main, "外壳切语言时必须重译抽屉"
    assert "def retranslate_side_panels" in mixin
    assert "_side_panel_widgets.items()" in mixin
