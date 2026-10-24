# tests/test_settings_page.py
"""P10-B7：设置页不再冻结界面、不悄悄改写配置、保存失败要可见。

审计 H12 与两条 medium：
- 「测试连接」在 GUI 线程同步 HTTP（timeout=30）⇒ 点一次界面冻结 30s，且"测试中"提示来不及重绘；
- 不透明度滑块从未 `setRange`（QSlider 默认 0–99）⇒ 100% 不可达，已存的 1.0 被 clamp 成 99 再写回 0.99；
- 保存逐项 `config.set()` ⇒ 一次保存重写配置文件十余次，中途失败留下前后不一致的配置且无提示。
"""
from __future__ import annotations

import threading

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp, config_env):
    from app.settings_page import SettingsPage
    from core.services.config_manager import ConfigManager

    manager = ConfigManager()
    widget = SettingsPage(manager)
    yield widget, manager
    widget.deleteLater()


def test_save_writes_config_once(page, monkeypatch):
    widget, manager = page
    calls: list[str] = []
    real_save = manager.save

    def counting_save():
        calls.append("save")
        return real_save()

    monkeypatch.setattr(manager, "save", counting_save)
    widget.save_settings()
    assert calls == ["save"], f"一次保存只应落盘一次，实际 {len(calls)} 次"


def test_save_failure_is_visible_and_not_broadcast(page, monkeypatch):
    widget, manager = page
    monkeypatch.setattr(manager, "save", lambda: False)
    manager.last_save_error = "disk full"
    emitted: list[int] = []
    widget.saved.connect(lambda: emitted.append(1))

    widget.save_settings()

    assert emitted == [], "写盘失败时不得广播 saved（否则后续动作会基于没落盘的配置执行）"
    assert "保存失败" in widget.save_status.text() or "disk full" in widget.save_status.text()


def test_connection_runs_off_gui_thread(page, qapp, monkeypatch):
    """连通性测试必须在后台线程跑，并在完成后恢复按钮。"""
    from app import settings_page as sp_mod

    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, config):
            self.config = config
            self.last_error = ""

        def test_connection(self):
            seen["thread"] = threading.current_thread()
            seen["ran"] = True
            return True

    monkeypatch.setattr(sp_mod, "DeepSeekClient", FakeClient)

    widget, _manager = page
    widget.api_key_edit.setText("sk-test-key")
    widget.test_connection()

    assert widget.test_btn.isEnabled() is False, "测试期间按钮应禁用"

    assert widget._tester is not None
    assert widget._tester.wait(5000), "后台线程应及时结束"
    qapp.processEvents()                      # 让 done 信号回到主线程槽

    assert seen.get("ran"), "后台线程应真的执行了 test_connection"
    assert seen["thread"] is not threading.main_thread(), "不能在 GUI 线程里做同步 HTTP"
    assert widget.test_btn.isEnabled() is True, "结束后按钮必须恢复"
