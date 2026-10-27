# tests/test_reference_switch_dialog.py
"""确认框：切库 / 保持 / 新建（两次点击防误触、重名与空名拦截）。"""
from __future__ import annotations

import pytest

from plugins.reference.switch_plan import Plan


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _plan(action: str, library: str = "论文A", score: float = 0.78) -> Plan:
    return Plan(action, library=library, score=score,
                added=["doi:new1", "doi:new2"], removed=["doi:old1"])


def test_prompt_shows_switch_button(qapp):
    from plugins.reference.switch_dialog import SwitchDialog

    dialog = SwitchDialog(_plan("prompt"), tr=lambda _key, **kwargs: _key)
    assert dialog.switch_btn.isVisibleTo(dialog) is True
    assert dialog.diff_list.count() == 3          # 2 新增 + 1 移除
    dialog.deleteLater()


def test_new_plan_hides_switch_button(qapp):
    from plugins.reference.switch_dialog import SwitchDialog

    dialog = SwitchDialog(_plan("new", score=0.0), tr=lambda _key, **kwargs: _key)
    assert dialog.switch_btn.isVisibleTo(dialog) is False
    dialog.deleteLater()


def test_switch_choice(qapp):
    from plugins.reference.switch_dialog import SwitchDialog

    dialog = SwitchDialog(_plan("prompt"), tr=lambda _key, **kwargs: _key)
    dialog._on_switch()
    assert dialog.choice == "switch"
    dialog.deleteLater()


def test_stay_choice(qapp):
    from plugins.reference.switch_dialog import SwitchDialog

    dialog = SwitchDialog(_plan("prompt"), tr=lambda _key, **kwargs: _key)
    dialog._on_stay()
    assert dialog.choice == "stay"
    dialog.deleteLater()


def test_new_requires_second_click_and_a_name(qapp):
    from plugins.reference.switch_dialog import SwitchDialog

    dialog = SwitchDialog(_plan("new", score=0.0), tr=lambda _key, **kwargs: _key)
    dialog._on_new()                               # 第一次：展开命名框
    assert dialog.choice == "stay"
    assert dialog.name_edit.isVisibleTo(dialog) is True

    dialog._on_new()                               # 空名：不接受
    assert dialog.choice == "stay"

    dialog.name_edit.setText("论文B")
    dialog._on_new()
    assert dialog.choice == "new"
    assert dialog.new_name == "论文B"
    dialog.deleteLater()


def test_prefilled_visible_name_creates_on_first_click(qapp):
    """页面预填建议名并展开命名框后，**第一次**点"新建"就该创建（用户现场：第一次没反应）。"""
    from plugins.reference.switch_dialog import SwitchDialog

    dialog = SwitchDialog(_plan("new", score=0.0), tr=lambda _key, **kwargs: _key)
    dialog.name_edit.setText("论文B")
    dialog.name_edit.setVisible(True)          # handle_switch_plan() 会这么做

    dialog._on_new()

    assert dialog.choice == "new"
    assert dialog.new_name == "论文B"
    dialog.deleteLater()


def test_duplicate_name_is_rejected(qapp):
    from plugins.reference.switch_dialog import SwitchDialog

    dialog = SwitchDialog(_plan("new", score=0.0), tr=lambda _key, **kwargs: _key,
                          existing=["默认库", "论文A"])
    dialog._on_new()
    dialog.name_edit.setText("论文A")
    dialog._on_new()
    assert dialog.choice == "stay"
    assert dialog.new_name == ""
    dialog.deleteLater()
