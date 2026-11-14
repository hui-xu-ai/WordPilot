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
    widget.model_edit.setText("deepseek-reasoner")      # 有真实改动才会写盘
    widget.save_settings()

    assert calls == ["save"], f"一次保存只应落盘一次，实际 {len(calls)} 次"


def test_no_save_when_nothing_changed(page, monkeypatch):
    """即改即存的前提：没有变化就不该写盘（防抖空转、切 Tab 都会走到这里）。"""
    widget, manager = page
    calls: list[str] = []
    real_save = manager.save

    monkeypatch.setattr(manager, "save", lambda: (calls.append("save"), real_save())[1])

    widget.save_settings()
    widget._flush_save()

    assert calls == [], f"没有改动不该写盘，实际写了 {len(calls)} 次"


def test_save_failure_is_visible_and_not_broadcast(page, monkeypatch):
    widget, manager = page
    monkeypatch.setattr(manager, "save", lambda: False)
    manager.last_save_error = "disk full"
    emitted: list[int] = []
    widget.saved.connect(lambda: emitted.append(1))

    widget.model_edit.setText("deepseek-reasoner")
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


def test_pet_controls_reach_core_service(page):
    """拖尺寸滑块 / 切皮肤下拉，都要落到内核 PetService（而不是只改界面）。"""
    from core.services.pet_service import PetService

    widget, manager = page
    from core.services.skin_service import SkinService

    widget.deleteLater()
    widget = type(widget)(manager, skins=SkinService(manager))   # 需要皮肤服务才有多选项
    service = PetService.instance()

    widget.pet_size_slider.setValue(96)
    assert service.size == 96, "滑块必须写进核心尺寸"

    index = next((i for i in range(widget.skin_combo.count())
                  if widget.skin_combo.itemData(i) == "sky_whale"), -1)
    assert index >= 0, "皮肤下拉应包含 sky_whale"
    widget.skin_combo.setCurrentIndex(index)
    assert service.skin == "sky_whale", "外观页切皮肤必须写进核心皮肤"


def test_pet_size_survives_skin_change(page):
    """切皮肤后尺寸不能被重置回默认（右键/下拉两条路径共用 PetHost.apply_skin）。"""
    from core.services.pet_host import PetHost
    from core.services.pet_service import PetService

    widget, _manager = page
    service = PetService.instance()
    widget.pet_size_slider.setValue(96)
    assert service.size == 96

    class _FakeWindow:
        def __init__(self):
            self.sizes = []

        def set_pet_size(self, size):
            self.sizes.append(size)

        def _switch_skin(self, skin_id):
            pass

    host = PetHost.instance()
    fake = _FakeWindow()
    host._window = fake
    host.apply_skin("ink_cat")
    assert fake.sizes[-1] == service.size == 96, "切皮肤后要按内核尺寸续用"


def test_all_eight_skin_choices_are_available(page):
    from core.services.skin_service import SkinService

    original, manager = page
    widget = type(original)(manager, skins=SkinService(manager))
    try:
        names = {widget.skin_combo.itemText(i) for i in range(widget.skin_combo.count())}
        assert names == {"奶团兔", "墨墨猫", "浮游鲸", "书页灵", "栗子柯基", "薄荷小龙", "枫糖小狐", "暮色小鸮"}
    finally:
        widget.deleteLater()


def test_only_api_tab_has_save_button(page):
    """外观即改即存；API 页保留显式「保存」（用户要求：点一下 + 底部明确反馈）。"""
    widget, _manager = page

    assert widget.api_save_btn is not None, "API 页应有保存按钮"
    assert not hasattr(widget, "save_btn"), "整页底部不应再有常驻保存按钮"


def test_api_edits_do_not_autosave(page, monkeypatch):
    """API 字段改成显式保存：输入过程中不该偷偷写盘（密钥尤其不能"填了就算生效"）。"""
    widget, manager = page
    calls: list[str] = []
    real_save = manager.save
    monkeypatch.setattr(manager, "save", lambda: (calls.append("save"), real_save())[1])

    widget.model_edit.setText("deepseek-reasoner")
    widget.api_key_edit.setText("sk-typed-but-not-saved")

    assert calls == [], "输入 API 字段不应自动写盘"


def test_api_save_button_persists_and_reports(page, monkeypatch):
    widget, manager = page
    statuses: list[tuple[str, int]] = []
    widget.statusMessage.connect(lambda text, ms: statuses.append((text, ms)))

    widget.api_save_btn.click()

    assert manager.get("model") is not None
    assert statuses, "保存成功后应往底部日志区发一条提示"
    assert statuses[0][1] > 0, "提示要带停留时长"
    assert widget.save_status.text(), "页面底部也要有反馈"


def test_loading_does_not_trigger_save(page, monkeypatch):
    """打开设置页时的程序化赋值不能写盘（否则每次进页面都闪一次已保存）。"""
    widget, manager = page
    calls: list[str] = []
    real_save = manager.save
    monkeypatch.setattr(manager, "save", lambda: (calls.append("save"), real_save())[1])

    widget.load_settings()
    widget._flush_save()          # 加载后立即触发也不会写（payload 未变）

    assert calls == [], "load_settings 及其后的空转都不应写盘"


@pytest.fixture
def rich_page(qapp, config_env):
    """带主题/语言/皮肤的完整页面：用来验证下拉框即时生效。"""
    from app.settings_page import SettingsPage
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService
    from core.services.skin_service import SkinService
    from core.services.theme_service import ThemeService

    manager = ConfigManager()
    widget = SettingsPage(manager, theme=ThemeService(manager), i18n=I18nService(manager),
                          skins=SkinService(manager))
    yield widget, manager
    widget.deleteLater()


def test_language_combo_saves_and_applies_immediately(rich_page):
    """界面语言以前要"点保存"才生效（用户改了没变）；现在选完即落盘并通知外壳重翻译。"""
    widget, manager = rich_page
    codes = [widget.ui_language_combo.itemData(i)
             for i in range(widget.ui_language_combo.count())]
    current = widget.ui_language_combo.currentData()
    others = [code for code in codes if code != current]
    if not others:
        pytest.skip("只有一个界面语言")
    target = others[0]
    index = widget.ui_language_combo.findData(target)
    assert index >= 0
    appearances: list[int] = []
    widget.appearanceSaved.connect(lambda: appearances.append(1))

    widget.ui_language_combo.setCurrentIndex(index)

    assert manager.get("ui_language") == target, "选完即落盘"
    assert appearances == [1], "外观变化要通知外壳切语言/重翻译"


def test_ai_change_does_not_trigger_appearance_refresh(rich_page):
    """只改 AI 参数不该触发重刷样式与全量重翻译（打字时每 0.5s 一次会造成闪烁）。"""
    widget, _manager = rich_page
    appearances: list[int] = []
    widget.appearanceSaved.connect(lambda: appearances.append(1))

    widget.model_edit.setText("deepseek-reasoner")
    widget._flush_save()

    assert appearances == []


def test_theme_combo_applies_immediately(rich_page):
    widget, manager = rich_page
    if widget.theme_combo.count() < 2:
        pytest.skip("只有一套主题")
    target = widget.theme_combo.itemData(1)

    widget.theme_combo.setCurrentIndex(1)

    assert manager.get("theme") == target, "主题选完即落盘"
    assert widget.theme.current == target, "主题要立即生效"


def test_api_key_is_masked_by_default(page):
    """U07：API Key 默认掩码，用户可主动显示。"""
    from PySide6.QtWidgets import QLineEdit

    widget, _manager = page
    assert widget.api_key_edit.echoMode() == QLineEdit.Password, "默认必须掩码"

    widget.api_key_toggle.setChecked(True)
    assert widget.api_key_edit.echoMode() == QLineEdit.Normal, "点显示后应可见"
    widget.api_key_toggle.setChecked(False)
    assert widget.api_key_edit.echoMode() == QLineEdit.Password, "再点应恢复掩码"


def test_page_destruction_stops_tester(page, monkeypatch):
    """U03：销毁设置页前必须结束连接测试线程（否则进程 0xC0000409 崩溃）。"""
    widget, _manager = page

    class _SlowTester:
        def __init__(self):
            self.stopped = False
            self.waited = 0

        def requestInterruption(self):
            self.stopped = True

        def quit(self):
            self.stopped = True

        def isRunning(self):
            return True

        def wait(self, ms):
            self.waited = ms
            return True

    tester = _SlowTester()
    widget._tester = tester
    widget._stop_tester()

    assert tester.stopped, "退出前必须请求线程停止"
    assert tester.waited > 0, "还要 wait 等它真正结束"
    assert widget._tester is None


# ---------------- 检查更新（关于页） ----------------
def _update_info(**kwargs):
    from core.services.update_service import UpdateInfo

    payload = dict(current="3.2.0", latest="9.9.9", has_update=True,
                   release_url="https://example.invalid/release",
                   download_url="https://example.invalid/pkg.zip",
                   notes="", published_at="2026-11-01T00:00:00Z")
    payload.update(kwargs)
    return UpdateInfo(**payload)


def test_about_page_has_update_controls(page):
    widget, manager = page

    assert widget.update_btn.isEnabled()
    assert widget.auto_check_update_check.isChecked() is True, "默认开启自动检查"
    assert widget.update_open_btn.isHidden(), "没查到新版本前不该显示下载入口"


def test_update_found_shows_version_and_download_entry(page):
    widget, manager = page

    widget._on_update_checked(_update_info(), "", "", manual=True)

    assert widget.update_status.text() == "app.update.available", "（假 i18n 下返回 key）"
    assert not widget.update_open_btn.isHidden()
    assert manager.get("update_last_seen_version") == "9.9.9"
    assert manager.get("update_last_checked_at")


def test_up_to_date_hides_download_entry(page):
    widget, manager = page

    widget._on_update_checked(_update_info(has_update=False, latest="3.2.0"), "", "", manual=True)

    assert widget.update_status.text() == "app.update.up_to_date"
    assert widget.update_open_btn.isHidden()


def test_manual_failure_shows_reason(page):
    widget, _manager = page

    widget._on_update_checked(None, "timeout", "", manual=True)

    assert widget.update_status.text() == "app.update.failed.timeout"
    assert widget.update_btn.isEnabled(), "失败后按钮必须恢复可点"


def test_automatic_failure_is_silent(page):
    widget, _manager = page

    widget._on_update_checked(None, "network", "", manual=False)

    assert widget.update_status.text() == "", "自动检查失败不该在界面上提示"
    assert widget.update_btn.isEnabled()


def test_background_check_respects_switch(page):
    widget, manager = page
    manager.config["auto_check_update"] = False

    widget.start_background_update_check()

    assert widget._update_checker is None, "关了自动检查就不该起线程"


def test_auto_check_toggle_persists(page):
    widget, manager = page

    widget.auto_check_update_check.setChecked(False)

    assert manager.get("auto_check_update") is False, "开关要立即落盘"
