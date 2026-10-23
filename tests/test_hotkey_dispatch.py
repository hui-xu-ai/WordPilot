# tests/test_hotkey_dispatch.py
"""P10-B4：热键回调必须编组回主线程，且注销要真正摘掉 OS 钩子。

背景（审计 H6/H7/H18）：`keyboard` 库在自己的钩子线程里回调，而注册进来的全是会动
Qt 控件与 Word COM 的插件方法；`unregister_owner` 之前只删登记表不 `remove_hotkey`，
插件停用后热键仍打到已卸载实例。
"""
from __future__ import annotations

import pytest

from core.services import hotkey_service as hk_mod


class FakeKeyboard:
    """替身：记录绑定，允许手动触发回调，模拟钩子线程调用。"""

    def __init__(self) -> None:
        self.bound: dict[int, object] = {}
        self._next = 0
        self.removed: list[int] = []

    def add_hotkey(self, combo, callback):
        handle = self._next
        self._next += 1
        self.bound[handle] = callback
        return handle

    def remove_hotkey(self, handle):
        self.removed.append(handle)
        self.bound.pop(handle, None)


@pytest.fixture
def kb(monkeypatch):
    fake = FakeKeyboard()
    monkeypatch.setattr(hk_mod, "kb", fake)
    return fake


@pytest.fixture
def service(kb):
    svc = hk_mod.HotkeyService({"hotkeys": {}})
    return svc


def test_callback_is_dispatched_not_run_in_hook_thread(service, kb):
    calls = []
    dispatched = []

    def cb():
        calls.append(1)

    service.register("demo", "ctrl+shift+d", cb, owner="plug")
    service.set_dispatcher(lambda fn: dispatched.append(fn))
    service.apply()

    (callback,) = kb.bound.values()
    callback()                                  # 模拟钩子线程触发
    assert calls == [], "回调不能在钩子线程里直接执行（会跨线程碰 Qt/COM）"
    assert len(dispatched) == 1, "应交给 dispatcher 编组"
    dispatched[0]()                             # 主线程执行
    assert calls == [1]


def test_without_dispatcher_callback_runs_directly(service, kb):
    calls = []
    service.register("demo", "ctrl+shift+d", lambda: calls.append(1), owner="plug")
    service.apply()
    (callback,) = kb.bound.values()
    callback()
    assert calls == [1], "未注入 dispatcher 时（内核 boot 阶段）退化为直接执行"


def test_unregister_owner_removes_os_hook(service, kb):
    service.register("a", "ctrl+shift+a", lambda: None, owner="plug")
    service.register("b", "ctrl+shift+b", lambda: None, owner="other")
    service.apply()
    assert len(kb.bound) == 2

    service.unregister_owner("plug")
    assert len(kb.bound) == 1, "注销插件必须真正 remove_hotkey，否则热键变成幽灵"
    assert service.names() == ("b",), "登记表也只应剩别的插件"


def test_reapply_unbinds_previous_handles(service, kb):
    service.register("a", "ctrl+shift+a", lambda: None, owner="plug")
    service.apply()
    first = set(kb.bound)
    service.apply()
    assert first.isdisjoint(set(kb.bound)), "重新 apply 前必须解绑旧句柄"
    assert len(kb.bound) == 1


def test_dispatch_failure_falls_back_to_direct_call(service, kb):
    calls = []

    def broken_dispatcher(fn):
        raise RuntimeError("no event loop")

    service.register("a", "ctrl+shift+a", lambda: calls.append(1), owner="plug")
    service.set_dispatcher(broken_dispatcher)
    service.apply()
    (callback,) = kb.bound.values()
    callback()
    assert calls == [1], "编组失败要退化为直接执行，不能静默丢掉热键"
