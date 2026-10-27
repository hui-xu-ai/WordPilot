# tests/test_choice_combo.py
"""真下拉（预设项 + 「自定义…」）：用户能看见能点开，不是伪装成输入框。

用户反馈（2026-10-27）：「你怎么就设计一个输入框，用户怎么进行切换？」
—— 原来是可编辑 QComboBox，外观就是个输入框，没有下拉箭头。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_combo_is_not_editable_and_has_custom_option(app):
    from core.widgets.choice_combo import CUSTOM_MARK, ChoiceCombo

    combo = ChoiceCombo()
    combo.set_choices([("自动识别", "auto"), ("中文", "中文")])

    assert combo.isEditable() is False, "必须是真下拉（有箭头），不能像输入框"
    assert combo.count() == 3
    assert combo.itemData(combo.count() - 1) == CUSTOM_MARK
    assert combo.itemText(combo.count() - 1) == "自定义…"


def test_set_and_read_value(app):
    from core.widgets.choice_combo import ChoiceCombo

    combo = ChoiceCombo()
    combo.set_choices([("自动识别", "auto"), ("中文", "中文"), ("English", "English")])

    combo.set_value("中文")
    assert combo.current_value() == "中文"

    combo.set_value("Français")                  # 不在预设里 → 追加为自定义项
    assert combo.current_value() == "Français"
    assert combo.itemText(combo.count() - 1) == "自定义…", "自定义项必须仍在末尾"


def test_rewrite_strength_expands_to_concrete_instruction():
    """`{strength}` 不能只是「中」——要展开成模型能执行的具体要求。"""
    import uuid

    from core.services.api_client import DeepSeekClient

    class _Config:
        def __init__(self):
            self.data = {"rewrite_prompt": "要求：{strength}\n{text}",
                         "rewrite_strength": "中",
                         "correct_language": "auto", "proof_language": "auto"}

        def get(self, key, default=None):
            return self.data.get(key, default)

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return {
                "prompt.rewrite.system": "SYS",
                "prompt.rewrite.mode.light": "轻度：只替换用词与标点",
                "prompt.rewrite.mode.medium": "中度：可拆分或合并句子",
                "prompt.rewrite.mode.strong": "深度：可重构段落顺序",
            }.get(key, key)

    client = DeepSeekClient.__new__(DeepSeekClient)
    client.config = _Config()
    client.i18n = _I18n()
    client.last_error = ""
    client.model = "test"
    client.last_usage = {"prompt": 0, "completion": 0, "total": 0}
    client.last_latency_ms = 0
    sent: list[str] = []

    def _capture(messages):
        sent.append(messages[1]["content"])
        return "结果"

    client._call_api = _capture                  # type: ignore[assignment]

    client.rewrite(f"一段文字{uuid.uuid4().hex}")
    assert "中度：可拆分或合并句子" in sent[-1], sent[-1]

    client.rewrite(f"一段文字{uuid.uuid4().hex}", strength="strong")
    assert "strong" in sent[-1] or "深度" in sent[-1]

    client.rewrite(f"一段文字{uuid.uuid4().hex}", strength="把句子改短，别用排比")
    assert "把句子改短，别用排比" in sent[-1], "自定义要求应原样注入"


def test_pages_use_choice_combo():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for relative in ("src/plugins/clipboard/plugin.py", "src/plugins/proofread/dashboard_page.py"):
        text = (root / relative).read_text(encoding="utf-8")
        assert "ChoiceCombo" in text, f"{relative} 应使用真下拉"
        assert "setEditable(True)" not in text, f"{relative} 不该再把下拉做成可编辑输入框"
