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

def test_plugin_actions_only_when_active():
    """插件注册的右键快捷项：插件没启用就不显示（用户要求）。"""
    service = PetService.instance(_Config())
    service.register_action("reference", "参考文献助手", lambda: None, order=20)
    service.register_action("navigator", "题录导航", lambda: None, order=10)

    assert service.actions() == [], "插件都没启用时不应有任何快捷项"

    service.set_plugin_active("reference", True)
    assert [label for label, _cb in service.actions()] == ["参考文献助手"]

    service.set_plugin_active("navigator", True)
    assert [label for label, _cb in service.actions()] == ["题录导航", "参考文献助手"], "按 order 排序"


def test_unregister_plugin_removes_actions():
    service = PetService.instance(_Config())
    service.set_plugin_active("proofread", True)
    service.register_action("proofread", "暂停校对", lambda: None)
    assert service.actions()

    service.unregister_plugin("proofread")
    assert service.actions() == []


def test_deactivated_plugin_hides_actions():
    service = PetService.instance(_Config())
    service.set_plugin_active("proofread", True)
    service.register_action("proofread", "暂停校对", lambda: None)
    service.set_plugin_active("proofread", False)
    assert service.actions() == []

# ---------------- 插件生命周期（快捷项自动显示/隐藏） ----------------

def test_plugin_manager_notifies_pet_on_load_and_unload():
    """插件加载/停用/卸载 → 宠物右键快捷项自动显示/隐藏。"""
    from core.plugin_manager import PluginManager

    service = PetService.instance(_Config())
    service.register_action("reference", "参考文献助手", lambda: None)
    assert service.actions() == []

    PluginManager._notify_pet("reference", True)
    assert [label for label, _cb in service.actions()] == ["参考文献助手"]

    PluginManager._notify_pet("reference", False)
    assert service.actions() == []

    PluginManager._notify_pet("reference", True)
    PluginManager._pet_cleanup("reference")
    assert service.actions() == [], "卸载后注册项一并清除"


def test_pet_unavailable_does_not_break_plugin_loading(monkeypatch):
    """宠物服务异常不能让插件加载失败。"""
    from core.plugin_manager import PluginManager

    def boom(*_args, **_kwargs):
        raise RuntimeError("宠物服务挂了")

    monkeypatch.setattr(PetService, "instance", classmethod(lambda cls, *a, **k: boom()))
    PluginManager._notify_pet("reference", True)

def test_ui_registry_add_pet_action_registers_into_service():
    """插件通过 ctx.ui.add_pet_action(...) 注册，落到 PetService 且受启用状态控制。"""
    from core.sdk.ui import UiRegistry

    service = PetService.instance(_Config())
    registry = UiRegistry()
    registry.add_pet_action("reference", "打开参考文献助手", lambda: None, order=20)
    assert service.actions() == [], "插件未启用前不显示"

    service.set_plugin_active("reference", True)
    assert [label for label, _cb in service.actions()] == ["打开参考文献助手"]
