# tests/test_choice_combo.py
"""预设选项下拉：只有预定义项，用户不能填自由文本（填错会直接送到接口）。

用户要求（2026-10-27）：
- 语言/强度只能预先定义，用户要自定义就直接改提示词；
- 曾经的「自定义…」允许乱填，例如把 `clipboard.韩文` 这类脏值写进配置。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_combo_is_readonly_and_has_no_custom_item(app):
    from core.widgets.choice_combo import ChoiceCombo

    combo = ChoiceCombo()
    combo.set_choices([("自动识别", "auto"), ("中文", "中文")])

    assert combo.isEditable() is False
    assert combo.count() == 2, "不该再有「自定义…」项"
    assert all("自定义" not in combo.itemText(i) for i in range(combo.count()))


def test_set_get_value_and_dirty_value_falls_back(app):
    from core.widgets.choice_combo import ChoiceCombo

    combo = ChoiceCombo()
    combo.set_choices([("自动识别", "auto"), ("中文", "中文")])

    combo.set_value("中文")
    assert combo.current_value() == "中文"

    combo.set_value("clipboard.韩文")           # 脏值不允许被选中
    assert combo.current_value() == "auto", "未知值必须退回第一项"


def test_option_labels_come_from_i18n():
    """下拉选项文案必须来自语言包 —— 曾经直接写 `("韩文", "韩文")`，tr 查不到就显示 key。"""
    for plugin, source_name in (("clipboard", "plugin.py"),
                                ("proofread", "dashboard_page.py")):
        data = json.loads(
            (ROOT / f"src/plugins/{plugin}/i18n/zh_CN.json").read_text(encoding="utf-8"))
        source = (ROOT / f"src/plugins/{plugin}" / source_name).read_text(encoding="utf-8")

        for key in re.findall(r'\("(settings\.prompt\.[\w.]+)",\s*"', source):
            assert key in data, f"{plugin} 缺少选项文案：{key}"
        # 选项值必须是固定语言名，不能出现 key 形态或中文当 key
        for label in re.findall(r'\(\("?(settings\.prompt\.[\w.]+|[\u4e00-\u9fff]+)"?,\s*"([^"]+)"\)', source):
            assert not label[1].startswith("clipboard."), f"选项值不该是 i18n key：{label}"


def test_pages_explain_placeholders():
    """每个页面都要有一段统一说明，讲清 {text}/{lang}/{target_lang}/{strength} 是什么。"""
    clip = (ROOT / "src/plugins/clipboard/i18n/zh_CN.json").read_text(encoding="utf-8")
    proof = (ROOT / "src/plugins/proofread/i18n/zh_CN.json").read_text(encoding="utf-8")

    for placeholder in ("{text}", "{target_lang}", "{lang}", "{strength}"):
        assert placeholder in clip, f"翻译改写页说明缺 {placeholder}"
    assert "{text}" in proof and "{lang}" in proof
    assert "占位符" in clip and "占位符" in proof
