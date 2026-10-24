# tests/test_ui_registry.py
"""P10-B6：UI 贡献点的归属清理。

`remove_plugin` 只能按 plugin_id 精确清理；历史调用点漏传 plugin_id（默认 None）时，
那条入口永远清不掉，插件停用后侧边栏仍留着、点下去执行已释放的处理器。
`retain_only` 是外壳重建导航前的兜底。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_retain_only_drops_orphans(qapp):
    from app.ui_registry import QtUiRegistry

    ui = QtUiRegistry()
    ui.add_page("label_a", lambda: None, plugin_id="plug")
    ui.add_page("label_orphan", lambda: None)          # 历史写法：没传归属
    ui.add_page("label_b", lambda: None, plugin_id="gone")

    ui.retain_only({"plug"})
    assert [entry[3] for entry in ui.pages()] == ["plug"], "无归属/已停用插件的入口必须清掉"


def test_remove_plugin_only_clears_its_own(qapp):
    from app.ui_registry import QtUiRegistry

    ui = QtUiRegistry()
    ui.add_tool("a", lambda: None, plugin_id="plug")
    ui.add_tool("b", lambda: None, plugin_id="other")
    ui.remove_plugin("plug")
    assert [entry[3] for entry in ui.tools()] == ["other"]


# ── ADR-002：次要界面（右侧抽屉）贡献点 ─────────────────────────────
def test_add_side_panel_is_visible_to_shell(qapp):
    from app.ui_registry import QtUiRegistry

    ui = QtUiRegistry()
    ui.add_side_panel("panel_label", lambda: None, width=380, plugin_id="plug")

    assert ui.side_panel_for("plug") is not None
    assert ui.side_panel_for("plug")[2] == 380, "宽度要按注册值传下去"
    assert ui.side_panel_for("other") is None
    assert ui.side_panel_for(None) is None


def test_remove_plugin_clears_side_panel(qapp):
    from app.ui_registry import QtUiRegistry

    ui = QtUiRegistry()
    ui.add_side_panel("panel_label", lambda: None, plugin_id="plug")
    ui.remove_plugin("plug")
    assert ui.side_panel_for("plug") is None, "插件卸载后副作用面板必须一起清掉"


def test_retain_only_clears_orphan_side_panel(qapp):
    from app.ui_registry import QtUiRegistry

    ui = QtUiRegistry()
    ui.add_side_panel("a", lambda: None, plugin_id="plug")
    ui.add_side_panel("b", lambda: None, plugin_id="gone")
    ui.retain_only({"plug"})
    assert set(ui.side_panels()) == {"plug"}


def test_null_registry_accepts_side_panel_silently():
    from core.sdk.ui import NullUiRegistry

    ui = NullUiRegistry()
    ui.add_side_panel("panel_label", lambda: None, plugin_id="plug")   # 不应抛
    assert ui.side_panels() == {}
