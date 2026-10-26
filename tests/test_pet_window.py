# tests/test_pet_window.py
"""P10-B8b：采纳/忽略之后，旧建议必须彻底作废。

审计：`_on_accepted`/`_on_ignored` 只 `bubble.hide()`，既不清 diff 也不复位待处理建议；
用户下次左键点桌宠时 `toggle_bubble` 会把**已处理过的旧建议**原样弹回来，
再点「确认」就按陈旧偏移重写正文（`apply_correction` 按新正文长度算区间，会吃掉后文）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def pet_cls():
    from core.widgets.pet_window import PetWindow

    return PetWindow


class FakeBubble:
    def __init__(self) -> None:
        self.visible = False
        self.cleared = 0
        self.shown = 0

    def isVisible(self):
        return self.visible

    def hide(self):
        self.visible = False

    def setVisible(self, value):
        self.visible = bool(value)

    def show(self):
        self.visible = True
        self.shown += 1

    def clear_diff(self):
        self.cleared += 1

    def show_suggestion(self, original, corrected, anchor=None, config=None, start=-1, end=-1):
        self.visible = True
        self.shown += 1


def _pet(pet_cls):
    pet = pet_cls.__new__(pet_cls)
    pet.bubble = FakeBubble()
    pet._pending_suggestion = None
    pet._correction_handler = None
    pet.ignore_mgr = None
    pet.config = type("C", (), {"get": staticmethod(lambda key, default=None: default)})()
    pet._after_bubble_change = lambda: None
    return pet


def test_accept_discards_pending_suggestion(pet_cls):
    pet = _pet(pet_cls)
    pet.set_suggestion("原句", "改后", 10, 12)
    assert pet._pending_suggestion is not None

    pet._on_accepted("改后", 10, 12, "原句")

    assert pet._pending_suggestion is None, "采纳后必须清掉待处理建议"
    assert pet.bubble.cleared >= 1, "应清空 diff 内容"
    assert pet.bubble.isVisible() is False


def test_ignore_discards_pending_suggestion(pet_cls):
    pet = _pet(pet_cls)
    pet.set_suggestion("原句", "改后", 10, 12)
    pet._on_ignored("原句", -1, -1, "原句")
    assert pet._pending_suggestion is None


def test_toggle_bubble_does_not_reopen_discarded_suggestion(pet_cls):
    pet = _pet(pet_cls)
    pet.set_suggestion("原句", "改后", 10, 12)
    shown_once = pet.bubble.shown
    pet._on_accepted("改后", 10, 12, "原句")

    pet.toggle_bubble()          # 用户再点桌宠

    assert pet.bubble.shown == shown_once, "处理过的旧建议不得再次弹出"
    assert pet.bubble.isVisible() is False


def test_toggle_bubble_opens_live_suggestion(pet_cls):
    pet = _pet(pet_cls)
    pet.set_suggestion("原句", "改后", 10, 12)
    pet.bubble.hide()            # 模拟用户先手动收起
    pet.toggle_bubble()
    assert pet.bubble.isVisible() is True, "仍有未处理建议时应能重新展开"


def test_no_diff_clears_pending(pet_cls):
    pet = _pet(pet_cls)
    pet.set_suggestion("原句", "改后", 10, 12)
    pet.set_suggestion("原句", "原句", -1, -1)
    assert pet._pending_suggestion is None
    assert pet.bubble.isVisible() is False


# ── 定位：气泡显隐后宠物不能漂移（G5：并入本模块）───────────────

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


def _make_pet():
    from core.widgets.pet_window import PetWindow

    return PetWindow(_Cfg({"auto_show_suggestion": True}))


def test_pet_rect_unchanged_when_bubble_toggles(qapp):
    pet = _make_pet()
    pet.set_suggestion("如图1b所示", "如图2b所示")
    pet.show()
    qapp.processEvents()
    pet._remember_pet_anchor()
    before = pet._pet_rect()

    for _ in range(3):          # 反复显隐，漂移会累积
        pet.toggle_bubble()
        qapp.processEvents()
        pet.toggle_bubble()
        qapp.processEvents()

    after = pet._pet_rect()
    assert (after.x(), after.y()) == (before.x(), before.y()), \
        f"宠物位置漂移了：{before.topLeft()} → {after.topLeft()}"
    pet.close()


def test_pet_rect_unchanged_after_relayout(qapp):
    """字号/服务重载这类"没有气泡变化"的重排也不能挪动宠物。"""
    pet = _make_pet()
    pet.show()
    qapp.processEvents()
    pet._remember_pet_anchor()
    before = pet._pet_rect()

    pet._reflow_keeping_pet()
    qapp.processEvents()

    after = pet._pet_rect()
    assert (after.x(), after.y()) == (before.x(), before.y())
    pet.close()


def test_bubble_is_separate_window(qapp):
    """气泡必须是独立窗口：嵌进桌宠窗口会二选一（要么裁切，要么把宠物挤走）。"""
    pet = _make_pet()
    pet.show()
    qapp.processEvents()
    pet.set_suggestion("如图1b所示", "如图2b所示")
    qapp.processEvents()

    assert pet.bubble.parent() is None, "气泡不应嵌在桌宠窗口里"
    assert pet.bubble.isWindow(), "气泡应是独立窗口"
    assert pet.width() == pet.canvas.width(), "桌宠窗口尺寸不应被气泡撑大"

def test_apply_style_sets_opacity_and_font(qapp):
    """保存设置后透明度/字号必须立刻生效（用户反馈"没生效"）。"""
    pet = _make_pet()
    config = pet.config
    config["suggestion_opacity"] = 0.55
    config["suggestion_font_size"] = 21

    pet.bubble.show_suggestion("原句", "改句", None, config)
    pet.bubble.apply_style(config)

    # 离屏平台不支持 setWindowOpacity（会打印 "does not support setting window opacity"），
    # 这里只验证调用不抛异常；透明度需在真实桌面验收。
    assert pet.bubble.font().pointSize() == 21
    assert pet.bubble.font().pointSize() == 21
    pet.close()

def test_pet_size_scales_window_and_canvas(qapp):
    """改尺寸必须同时放大窗口与画布 —— 只改窗口时宠物看起来"永远一样大"（真凶）。"""
    pet = _make_pet()
    pet.show()
    qapp.processEvents()

    pet.set_pet_size(150)
    qapp.processEvents()

    assert pet.width() == 150
    canvas = getattr(pet, "canvas", None)
    assert canvas is not None, "PetWindow 应有 canvas"
    assert canvas.width() == 150, "画布必须跟随窗口缩放"
    assert canvas.height() == 150


def test_skin_refresh_keeps_size(qapp):
    """换皮肤后（set_services 会按皮肤基准重设）尺寸要按内核值恢复。"""
    from core.services.pet_service import PetService

    PetService.instance().set_size(96)
    pet = _make_pet()
    pet.show()
    qapp.processEvents()
    pet.set_pet_size(96)

    pet.set_services(skins=getattr(pet, "_skins", None))
    qapp.processEvents()
    assert pet.width() == 96, "换皮肤后尺寸不能被重置回基准"

def test_pet_window_composes_mixins(qapp):
    """右键菜单与几何上报已抽成 mixin：PetWindow 应同时具备两者（回归防丢）。"""
    from core.widgets.pet_menu import PetMenuMixin

    pet = _make_pet()
    assert isinstance(pet, PetMenuMixin), "右键菜单 mixin 丢失"
    assert callable(pet._show_menu), "右键菜单入口必须存在"
    assert callable(getattr(pet, "_report_geometry", None)), "几何上报必须存在"

def test_context_menu_builds(qapp):
    """右键菜单必须能构建出菜单项（之前 mixin 缺 QMenu 导入 → 右键点了没反应）。"""
    pet = _make_pet()
    menu = pet._build_menu()
    labels = [action.text() for action in menu.actions()]
    assert labels, "菜单不应为空"
    assert any("pet.menu" in str(label) or label for label in labels)
    submenus = [action.menu().title() for action in menu.actions() if action.menu() is not None]
    assert submenus, "至少应有「外观皮肤」「插件」等子菜单"


def test_pending_status_is_independent_of_bubble_visibility(pet_cls):
    pet = _pet(pet_cls)
    pet.set_suggestion("原文", "修改后的内容", 1, 4)
    pet.bubble.hide()
    assert pet.has_pending_suggestion()
    pet._on_ignored("原文", -1, -1, "原文")
    assert not pet.has_pending_suggestion()


def test_only_a_new_suggestion_triggers_attention(pet_cls):
    pet = _pet(pet_cls)

    class Canvas:
        calls = 0

        def notify_suggestion(self):
            self.calls += 1

    canvas = Canvas()
    pet.canvas = canvas
    pet._canvas_global_pos = lambda: None
    pet.set_suggestion("原文", "改文", 1, 4)
    pet.set_suggestion("原文", "改文", 1, 4)
    assert canvas.calls == 1
    pet.set_suggestion("另一原文", "另一改文", 5, 10)
    assert canvas.calls == 2
    pet.set_suggestion("原文", "原文", 1, 4)
    assert canvas.calls == 2


def test_real_pet_and_plugin_keep_status_until_handled(qapp, skins_dir):
    from PySide6.QtTest import QTest

    from core.services.skin_service import SkinService
    from core.widgets.pet_window import PetWindow
    from plugins.proofread.plugin import ProofreadPlugin

    config = _Cfg({"skin": "mochi_bunny", "auto_show_suggestion": False})
    pet = PetWindow(config, skins=SkinService(config, skins_dir))
    plugin = ProofreadPlugin()
    plugin.floating_btn = pet
    pet.bubbleToggled.connect(plugin._on_bubble_toggled)
    try:
        plugin._on_suggestion("原句", "改后的句子", -1, -1)
        assert not pet.bubble.isVisible()
        assert pet.state() == "error" and pet.has_pending_suggestion()
        pet.toggle_bubble()
        pet.toggle_bubble()
        assert pet.state() == "error"
        pet._on_ignored("原句", -1, -1, "原句")
        assert pet.state() == "idle" and not pet.has_pending_suggestion()
        plugin._on_suggestion("无须修改", "无须修改", -1, -1)
        assert pet.state() == "ok"
        QTest.qWait(2200)
        assert pet.state() == "idle"
    finally:
        pet.canvas.stop_animation()
        pet.bubble.close()
        pet.close()


@pytest.mark.parametrize(("skin_id", "name"), [
    ("chestnut_corgi", "栗子柯基"), ("mint_dragon", "薄荷小龙"),
    ("maple_fox", "枫糖小狐"), ("dusk_owl", "暮色小鸮"),
])
def test_added_skin_can_be_selected_from_menu_at_70px(qapp, skins_dir, skin_id, name):
    from core.services.pet_service import PetService
    from core.services.skin_service import SkinService
    from core.widgets.pet_window import PetWindow

    PetService.reset_instance()
    config = _Cfg({"skin": "mochi_bunny", "pet.size": 70})
    PetService.instance(config)
    pet = PetWindow(config, skins=SkinService(config, skins_dir))
    try:
        menu = pet._build_menu()
        menu_actions = menu.actions()
        skin_menu = menu_actions[0].menu()
        actions = {action.text(): action for action in skin_menu.actions()}
        assert len(actions) == 8
        assert name in actions
        actions[name].trigger()
        assert pet._skins.current_id == skin_id
        assert config["skin"] == skin_id
        assert pet.width() == pet.canvas.width() == 70
    finally:
        pet.canvas.stop_animation()
        pet.bubble.close()
        pet.close()
        PetService.reset_instance()


def test_context_menu_pause_label_follows_state(qapp, monkeypatch):
    """右键菜单要按当前状态显示「暂停校对」或「继续校对」，且不带勾选框。"""
    pet = _make_pet()

    labels: list[str] = []
    monkeypatch.setattr(pet, "_proofread_enabled", lambda: True)
    menu = pet._build_menu()
    labels = [action.text() for action in menu.actions()]
    assert any("pause_proofread" in text for text in labels), f"校对中应显示「暂停校对」：{labels}"

    monkeypatch.setattr(pet, "_proofread_enabled", lambda: False)
    menu = pet._build_menu()
    labels = [action.text() for action in menu.actions()]
    assert any("resume_proofread" in text for text in labels), f"已暂停应显示「继续校对」：{labels}"

    checkable = [action.text() for action in menu.actions() if action.isCheckable()]
    assert not any("proofread" in text for text in checkable), "校对项不应再是可勾选项"


def test_pause_menu_keys_exist_in_plugin_i18n():
    """桌宠的 tr 走 proofread 插件词条；缺键就会在菜单里显示成 `xxx.pause_proofread`。"""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "plugins" / "proofread" / "i18n"
    for lang in ("zh_CN", "en_US"):
        data = json.loads((root / f"{lang}.json").read_text(encoding="utf-8"))
        for key in ("pet.menu.pause_proofread", "pet.menu.resume_proofread"):
            assert key in data, f"{lang} 缺 {key}（菜单会显示原始 key）"


def test_menu_reflects_pushed_state(qapp):
    """插件把真实启停状态推过来后，菜单必须跟着变（否则永远显示同一个动作）。"""
    pet = _make_pet()

    pet.set_proofread_enabled(True)
    labels = [action.text() for action in pet._build_menu().actions()]
    assert any("pause_proofread" in text for text in labels), labels

    pet.set_proofread_enabled(False)
    labels = [action.text() for action in pet._build_menu().actions()]
    assert any("resume_proofread" in text for text in labels), labels
