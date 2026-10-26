"""桌宠关键状态在小尺寸下仍能用颜色与形状区分，且提示动画不挪动窗口。"""
from __future__ import annotations

import json

import pytest

from core.services.skin_service import STATE_TOKENS, SkinService


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp, tmp_path):
    from core.services.pet_service import PetService
    from core.widgets.pet_window import PetCanvas

    PetService.reset_instance()
    PetService.instance({"pet.size": 64})
    folder = tmp_path / "fixture"
    folder.mkdir()
    (folder / "skin.json").write_text(json.dumps({
        "id": "fixture", "status_overlay": True,
        "parts": {"body": "body.svg"},
        "animation": {"breathe_scale": 0.01},
    }), encoding="utf-8")
    (folder / "body.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<circle cx="32" cy="31" r="18" fill="#fff2dc"/></svg>', encoding="utf-8")
    widget = PetCanvas(skins=SkinService({"skin": "fixture"}, tmp_path))
    yield widget
    widget.stop_animation()
    widget.close()
    PetService.reset_instance()


def _image(canvas, state, size=64):
    from PySide6.QtGui import QImage
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    canvas.set_pet_size(size)
    canvas.set_state(state)
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    canvas.render(image, renderFlags=QWidget.DrawChildren)
    return image


def _colored_count(image, predicate):
    return sum(predicate(image.pixelColor(x, y)) for y in range(image.height())
               for x in range(image.width()))


def test_rest_is_not_a_success_state():
    assert STATE_TOKENS["idle"] != STATE_TOKENS["ok"]


def test_status_overlay_is_opt_in(canvas, tmp_path):
    assert canvas._skins.status_overlay_enabled()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "skin.json").write_text(json.dumps({"id": "legacy", "parts": {}}), encoding="utf-8")
    assert not SkinService({"skin": "legacy"}, tmp_path).status_overlay_enabled()


@pytest.mark.parametrize("size", [48, 64, 96])
def test_pass_and_suggestion_have_large_distinct_signals(canvas, size):
    green = _image(canvas, "ok", size)
    coral = _image(canvas, "error", size)
    assert _colored_count(green, lambda c: c.alpha() > 150 and c.green() > c.red() * 1.3) > size * 2
    assert _colored_count(coral, lambda c: c.alpha() > 150 and c.red() > c.green() * 1.3) > size * 2
    # 形状轮廓也必须不同：不能只给同一个灰度图换色。
    differing_alpha = sum(abs(green.pixelColor(x, y).alpha() - coral.pixelColor(x, y).alpha()) > 40
                          for y in range(size) for x in range(size))
    assert differing_alpha > size / 2


def test_rest_has_no_green_pass_halo(canvas):
    rest = _image(canvas, "idle")
    assert _colored_count(rest, lambda c: c.alpha() > 150 and c.green() > c.red() * 1.3) == 0


def test_attention_is_one_shot_and_does_not_move_canvas(canvas):
    from PySide6.QtCore import QAbstractAnimation

    before = canvas.pos()
    canvas.notify_suggestion()
    animation = canvas._attention_anim
    assert animation.loopCount() == 1
    animation.setCurrentTime(animation.duration() // 2)
    assert canvas._attention > 0
    assert canvas.pos() == before
    animation.setCurrentTime(animation.duration())
    assert canvas._attention == pytest.approx(0)
    canvas.stop_animation()
    assert animation.state() == QAbstractAnimation.Stopped


def test_paused_pet_does_not_jump_for_a_late_result(canvas):
    from PySide6.QtCore import QAbstractAnimation

    canvas.set_state("disabled")
    canvas.notify_suggestion()
    assert canvas._attention_anim.state() == QAbstractAnimation.Stopped
    assert canvas._attention == 0
