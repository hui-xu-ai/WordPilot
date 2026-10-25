# tests/test_pet_host.py
"""PetHost：尺寸/皮肤下发到窗口，并记住内核状态（拖滑块/切皮肤/再拖尺寸）。"""
from __future__ import annotations

import pytest

from core.services.pet_host import PetHost
from core.services.pet_service import PetService


class _FakeWindow:
    def __init__(self) -> None:
        self.sizes: list[int] = []
        self.skins: list[str] = []

    def set_pet_size(self, size: int) -> None:
        self.sizes.append(int(size))

    def _switch_skin(self, skin_id: str) -> None:
        self.skins.append(skin_id)


class _Config(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


@pytest.fixture(autouse=True)
def _reset():
    PetService.reset_instance()
    PetHost.reset_instance()
    yield
    PetService.reset_instance()
    PetHost.reset_instance()


def test_apply_size_reaches_window():
    host = PetHost.instance()
    window = _FakeWindow()
    host._window = window
    PetService.instance(_Config())

    host.apply_size(96)
    assert window.sizes == [96], "拖滑块必须真正把尺寸下发给窗口"


def test_apply_skin_records_and_switches():
    config = _Config()
    service = PetService.instance(config)
    host = PetHost.instance()
    window = _FakeWindow()
    host._window = window

    host.apply_skin("neon")
    assert window.skins == ["neon"], "皮肤必须真正切到窗口"
    assert service.skin == "neon", "内核要记住选择"
    assert config.get("pet.skin") == "neon", "配置要持久化"


def test_skin_switch_keeps_size():
    """切皮肤后尺寸不该被重置回 64（用户反馈的第三个问题）。"""
    service = PetService.instance(_Config())
    service.set_size(96)
    host = PetHost.instance()
    window = _FakeWindow()
    host._window = window

    host.apply_skin("deepseek")
    assert window.sizes[-1] == 96, "切皮肤后要按内核尺寸重新套用"
