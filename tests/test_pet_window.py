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


def test_window_grows_upward_when_bubble_appears(qapp):
    """气泡出现在宠物**上方**：窗口顶边上移、底边基本不动。"""
    pet = _make_pet()
    pet.show()
    qapp.processEvents()
    pet._remember_pet_anchor()
    top_before, bottom_before = pet.y(), pet.frameGeometry().bottom()

    pet.set_suggestion("原始句子内容", "修改后的句子内容")
    qapp.processEvents()

    assert pet.y() <= top_before, "出现气泡后窗口应向上扩展"
    assert abs(pet.frameGeometry().bottom() - bottom_before) <= 2, "窗口底边应基本不动"
    pet.close()


def test_apply_style_sets_opacity_and_font(qapp):
    """保存设置后透明度/字号必须立刻生效（用户反馈"没生效"）。"""
    pet = _make_pet()
    config = pet.config
    config["suggestion_opacity"] = 0.55
    config["suggestion_font_size"] = 21

    pet.bubble.show_suggestion("原句", "改句", None, config)
    pet.bubble.apply_style(config)

    assert abs(pet.window().windowOpacity() - 0.55) < 0.01
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

    PetService.instance().set_size(170)
    pet = _make_pet()
    pet.show()
    qapp.processEvents()
    pet.set_pet_size(170)

    pet.set_services(skins=getattr(pet, "_skins", None))
    qapp.processEvents()
    assert pet.width() == 170, "换皮肤后尺寸不能被重置回 64"

def test_pet_window_composes_mixins(qapp):
    """右键菜单与几何上报已抽成 mixin：PetWindow 应同时具备两者（回归防丢）。"""
    from core.widgets.pet_menu import PetMenuMixin

    pet = _make_pet()
    assert isinstance(pet, PetMenuMixin), "右键菜单 mixin 丢失"
    assert callable(pet._show_menu), "右键菜单入口必须存在"
    assert callable(getattr(pet, "_report_geometry", None)), "几何上报必须存在"
