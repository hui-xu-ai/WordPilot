# tests/test_pet_service.py
"""桌宠共享服务：几何锚点、尺寸/皮肤持久化、可见性，供任意插件使用。"""
from __future__ import annotations

import pytest

from core.services.pet_service import DEFAULT_SIZE, MAX_SIZE, MIN_SIZE, PetService


class _Config:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture(autouse=True)
def _clean_singleton():
    PetService.reset_instance()
    yield
    PetService.reset_instance()


def test_anchor_none_before_geometry():
    service = PetService.instance(_Config())
    assert service.anchor_point() is None


def test_anchor_is_right_of_pet():
    service = PetService.instance(_Config())
    service.update_geometry(100, 200, 96)
    x, y = service.anchor_point()
    assert x == 100 + 96 + 8 and y == 200


def test_hidden_pet_has_no_anchor():
    service = PetService.instance(_Config())
    service.update_geometry(10, 20, 96)
    service.set_visible(False)
    assert service.anchor_point() is None


def test_size_is_clamped_and_persisted():
    config = _Config()
    service = PetService.instance(config)
    service.set_size(10)
    assert service.size == MIN_SIZE
    service.set_size(9999)
    assert service.size == MAX_SIZE
    service.set_size(120)
    assert config.get("pet.size") == 120

    PetService.reset_instance()
    assert PetService.instance(config).size == 120


def test_skin_and_visibility_persist():
    config = _Config()
    service = PetService.instance(config)
    service.set_skin("neon")
    service.set_visible(False)
    assert config.get("pet.skin") == "neon"
    assert config.get("pet.visible") is False


def test_defaults_without_config():
    service = PetService.instance(None)
    assert service.size == DEFAULT_SIZE
    assert service.skin == "classic"
    service.update_geometry(0, 0)
    assert service.anchor_point() == (0 + DEFAULT_SIZE + 8, 0)
