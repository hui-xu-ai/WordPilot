# tests/test_dashboard_page.py
"""智能纠错插件抽屉里的设置（从主设置页搬来的那批）。

原先在 `tests/test_settings_page.py` 里测的透明度范围/夹取，随控件一起迁到这里：
抽屉里的滑块是唯一写入 `suggestion_opacity` 的地方，范围 20–100 的约束不能丢。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Services(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class _Ctx:
    """最小插件上下文：DashboardPage 只用 services / events。"""

    def __init__(self, config):
        self._services = _Services({"config": config})
        self.emitted: list[str] = []

    def get_service(self, name):
        return self._services.get(name)

    def tr(self, key, /, **params):
        """假翻译：直接回 key，便于断言文案来源。"""
        return key

    @property
    def events(self):
        emitted = self.emitted

        class _Events:
            @staticmethod
            def emit(name, payload=None):
                emitted.append(name)

        return _Events()


class _FakePlugin:
    """DashboardPage 只用到这几样：ctx、开关状态、启停回调。"""

    def __init__(self, config):
        self.ctx = _Ctx(config)
        self.enabled = True

    def is_enabled(self):
        return self.enabled

    def subscribe(self, *_args, **_kwargs):
        return lambda: None

    def set_enabled(self, on):
        self.enabled = bool(on)

    def toggle(self, *_args):
        self.enabled = not self.enabled

    def apply_correction(self, *_args, **_kwargs):
        return None

    def _plugin_tools(self):
        return []


@pytest.fixture
def page(qapp, config_env):
    from core.services.config_manager import ConfigManager
    from plugins.proofread.dashboard_page import DashboardPage

    manager = ConfigManager()
    plugin = _FakePlugin(manager)
    widget = DashboardPage(plugin=plugin)
    widget.build_secondary_panel()
    yield widget, manager, plugin
    widget.deleteLater()


def test_opacity_slider_has_reachable_full_range(page):
    widget, _manager, _plugin = page
    assert widget.opacity_slider.minimum() == 20, "下限要防止 0% 完全不可见"
    assert widget.opacity_slider.maximum() == 100, "上限必须是 100%，否则 100% 不可达"


def test_save_clamps_opacity_and_broadcasts(page):
    widget, manager, plugin = page
    widget.opacity_slider.setValue(100)
    widget.save_plugin_settings()
    assert manager.get("suggestion_opacity") == 1.0
    widget.opacity_slider.setValue(20)
    widget.save_plugin_settings()
    assert manager.get("suggestion_opacity") == 0.2
    assert plugin.ctx.emitted.count("settings.saved") == 2, "保存成功必须广播 settings.saved"


def test_save_writes_proof_prompt(page):
    widget, manager, _plugin = page
    widget.proof_prompt_edit.setPlainText("只改错别字：{text}")
    widget.save_plugin_settings()
    assert manager.get("proof_prompt") == "只改错别字：{text}"

def test_proofread_drawer_has_no_pet_controls(page):
    """宠物外观/显隐归内核外观页，proofread 抽屉里不能再有宠物尺寸、皮肤或显隐开关。"""
    widget = page[0]
    assert not hasattr(widget, "pet_size_slider")
    assert not hasattr(widget, "pet_skin_combo")
    assert not hasattr(widget, "show_floating_check")


def _page_with_hotkeys(config_env, combo="ctrl+alt+shift+e"):
    from core.services.config_manager import ConfigManager
    from core.services.hotkey_service import HotkeyService
    from plugins.proofread.dashboard_page import DashboardPage

    manager = ConfigManager()
    hotkeys = HotkeyService(manager)
    hotkeys.register("toggle_monitor", combo, lambda: None, owner="proofread")
    plugin = _FakePlugin(manager)
    plugin.ctx._services["hotkeys"] = hotkeys
    page = DashboardPage(plugin=plugin)
    page.build_secondary_panel()        # 快捷键行在抽屉里
    page.retranslateUi()
    return page, manager, hotkeys


def _capture_tr(page) -> dict:
    """把 tr() 换成记录参数的假翻译（本测试只关心"传进去的组合键是什么"）。"""
    seen: dict = {}

    def _tr(key, /, **params):
        seen.update(params)
        seen["key"] = key
        return f"{key} {params}" if params else key

    page.tr = _tr
    return seen


def test_toggle_hint_shows_hotkey(qapp, config_env):
    """校对开关旁要显示快捷键，用户才知道怎么用键盘开关。"""
    page, _manager, _hotkeys = _page_with_hotkeys(config_env)
    seen = _capture_tr(page)

    page._refresh_hotkey_hint()

    assert seen["key"] == "dashboard.toggle_shortcut"
    assert seen["combo"] == "Ctrl+Alt+Shift+E", "默认组合键要按显示格式给出来"
    assert page.hotkey_hint.text(), "提示不能为空"


def test_toggle_hint_follows_configured_combo(qapp, config_env):
    """提示取"实际生效"的组合键：用户改过配置就显示配置值，不写死。"""
    page, manager, hotkeys = _page_with_hotkeys(config_env)
    seen = _capture_tr(page)
    manager.set("hotkeys", {"toggle_monitor": "ctrl+alt+p"})

    page._refresh_hotkey_hint()

    assert seen["combo"] == "Ctrl+Alt+P"
    assert hotkeys.combo("toggle_monitor") == "ctrl+alt+p"


# ---------------- 快捷键：改键与冲突检测 ----------------
def _counting_apply(hotkeys) -> list[int]:
    """统计重绑次数（真 `apply()` 会装 OS 钩子，测试里不能真跑）。"""
    calls: list[int] = []
    hotkeys.apply = lambda: calls.append(1)
    return calls


def test_hotkey_row_shows_effective_combo(qapp, config_env):
    """抽屉里要能看到当前生效的组合键（Word 的「修订开关」曾经抢同一个键）。"""
    page, _manager, _hotkeys = _page_with_hotkeys(config_env)

    assert page.hotkey_edit.text() == "Ctrl+Alt+Shift+E"


def test_new_combo_is_saved_and_rebound(qapp, config_env):
    page, manager, hotkeys = _page_with_hotkeys(config_env)
    applied = _counting_apply(hotkeys)
    seen = _capture_tr(page)            # 主页面提示走 tr(key, combo=...)

    page.hotkey_edit.setText("ctrl+alt+j")
    page.apply_hotkey_combo()

    assert (manager.get("hotkeys") or {}).get("toggle_monitor") == "ctrl+alt+j"
    assert applied == [1], "改键后必须重新绑定，否则新键不生效"
    assert page.hotkey_edit.text() == "Ctrl+Alt+J"
    assert seen.get("combo") == "Ctrl+Alt+J", "主页面的快捷键提示要同步"


def test_invalid_combo_is_rejected(qapp, config_env):
    page, manager, hotkeys = _page_with_hotkeys(config_env)
    applied = _counting_apply(hotkeys)

    page.hotkey_edit.setText("shift+e")          # 只有 Shift：会抢掉正常输入
    page.apply_hotkey_combo()

    assert (manager.get("hotkeys") or {}).get("toggle_monitor") == "ctrl+alt+shift+e", "无效输入不得写盘"
    assert applied == []
    assert page.hotkey_status.text() == "settings.hotkey.invalid"
    assert page.hotkey_edit.text() == "Ctrl+Alt+Shift+E", "无效输入要退回当前生效值"


def test_check_conflict_reports_word_binding(qapp, config_env, monkeypatch):
    from core.services import hotkey_conflict

    page, _manager, _hotkeys = _page_with_hotkeys(config_env)
    monkeypatch.setattr(hotkey_conflict, "assess",
                        lambda *_a, **_k: {"status": hotkey_conflict.STATUS_CONFLICT,
                                           "command": "ToolsRevisionMarksToggle",
                                           "human": "修订模式开关", "detail": ""})

    page.check_hotkey_combo()

    assert page.hotkey_status.text() == "settings.hotkey.conflict"


def test_check_conflict_free_and_unknown(qapp, config_env, monkeypatch):
    from core.services import hotkey_conflict

    page, _manager, _hotkeys = _page_with_hotkeys(config_env)
    monkeypatch.setattr(hotkey_conflict, "assess",
                        lambda *_a, **_k: {"status": hotkey_conflict.STATUS_FREE,
                                           "command": "", "human": "", "detail": ""})
    page.check_hotkey_combo()
    assert page.hotkey_status.text() == "settings.hotkey.free"

    monkeypatch.setattr(hotkey_conflict, "assess",
                        lambda *_a, **_k: {"status": hotkey_conflict.STATUS_UNKNOWN,
                                           "command": "", "human": "", "detail": ""})
    page.check_hotkey_combo()
    assert page.hotkey_status.text() == "settings.hotkey.unknown"
