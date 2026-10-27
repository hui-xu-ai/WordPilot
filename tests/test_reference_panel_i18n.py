# tests/test_reference_panel_i18n.py
"""参考文献右栏的字段行标签要跟着界面语言重译。

现场（2026-10-27 英文界面截图）：右栏里「引用编号 / 标题 / 作者 / 年份 / 期刊出处 / DOI /
类型 / 标签（分号分隔）」仍是中文 —— 这些 QLabel 建面板时求值一次，retranslateUi 没重新设置。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "src/plugins/reference/page_panel.py"
READ_FIELDS = ("cite_label", "title", "authors", "year", "container", "doi", "type", "tags")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_panel_keeps_label_widgets():
    text = PANEL.read_text(encoding="utf-8")

    assert "read_row_labels" in text, "只读行标签必须留着引用才能重译"
    assert "edit_row_labels" in text, "编辑行标签同理"
    assert "def retranslate_panel(" in text


def test_page_calls_panel_retranslate():
    text = (ROOT / "src/plugins/reference/library_page.py").read_text(encoding="utf-8")

    assert "retranslate_panel(self)" in text


def test_field_labels_translated_in_both_languages():
    zh = json.loads((ROOT / "src/plugins/reference/i18n/zh_CN.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / "src/plugins/reference/i18n/en_US.json").read_text(encoding="utf-8"))

    for key in READ_FIELDS:
        i18n_key = "field.label" if key == "cite_label" else f"field.{key}"
        assert i18n_key in zh and i18n_key in en, f"缺 {i18n_key}"
        if i18n_key == "field.doi":
            # DOI 是专有名词，两种语言都写 DOI
            assert zh[i18n_key] == en[i18n_key] == "DOI"
        else:
            assert zh[i18n_key] != en[i18n_key], f"{i18n_key} 中英相同，等于没翻译"


def test_retranslate_updates_labels(app):
    """用最小替身验证 retranslate_panel 真的改了标签文案。"""
    from PySide6.QtWidgets import QLabel

    from plugins.reference.page_panel import retranslate_panel

    class _Host:
        def __init__(self):
            self.lang = "zh"
            self.read_row_labels = {key: QLabel("旧") for key in READ_FIELDS}
            self.edit_row_labels = {"field.title": QLabel("旧")}
            self.read_note_title = QLabel("旧")
            self.bind_btn = None
            self.edit_btn = None

        def tr(self, key, /, **params):
            return ("EN:" if self.lang == "en" else "中:") + key

    host = _Host()
    host.lang = "en"

    retranslate_panel(host)

    assert host.read_row_labels["title"].text() == "EN:field.title"
    assert host.edit_row_labels["field.title"].text() == "EN:field.title"
    assert host.read_note_title.text() == "EN:field.note"
