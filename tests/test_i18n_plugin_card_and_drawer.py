# tests/test_i18n_plugin_card_and_drawer.py
"""两处一直漏掉的界面：
1) 插件管理页卡片上的**简介**（标签原先用 manifest.description，只改了搜索索引）；
2) 智能纠错页侧边抽屉里的**占位符说明**（重译代码被误插进 _build_prompt_card，永远不生效）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")
ALLOWED = {"中文", "简体中文"}


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def i18n():
    from core.services.config_manager import ConfigManager
    from core.services.i18n import I18nService

    class _Config(ConfigManager):
        def __init__(self):
            super().__init__()
            self.set("ui_language", "en_US")

    service = I18nService(_Config(), ROOT / "src/assets/i18n")
    for plugin in ("clipboard", "image_toolbox", "navigator", "proofread", "reference"):
        service.register_namespace(plugin, ROOT / f"src/plugins/{plugin}/i18n")
    return service


def test_plugin_card_description_is_localised(app, i18n):
    """卡片简介必须走 i18n：`_display_description()` 返回英文，且源码不再直接把
    manifest.description 塞进 QLabel（上一版就漏在这行）。"""
    from app.plugin_manager_page import PluginManagerPage

    class _Manifest:
        id = "clipboard"
        version = "1.0.0"
        author = "Xu Hui"
        description = "读取剪贴板文本，一键翻译 / 改写 / 纠错，结果可复制"

    class _Record:
        manifest = _Manifest()
        state = None

    page = PluginManagerPage.__new__(PluginManagerPage)
    page.i18n = i18n

    description = page._display_description(_Record())
    assert description.startswith("Read clipboard text"), description
    assert not CJK.search(description), description

    source = (ROOT / "src/app/plugin_manager_page.py").read_text(encoding="utf-8")
    assert "QLabel(manifest.description)" not in source, "卡片简介不该直接用 manifest.description"

def test_proofread_drawer_hint_is_localised(app, i18n):
    from plugins.proofread.dashboard_page import DashboardPage

    class _Plugin:
        def __init__(self, ctx):
            self.ctx = ctx
            self.floating_btn = None

        def is_enabled(self):
            return False

        def set_enabled(self, value):
            return None

        def subscribe(self, *a, **k):
            return None

        def get_settings_payload(self):
            return {}

    class _Ctx:
        def __init__(self):
            self.i18n = i18n
            self.config = i18n.config
            self.events = type("E", (), {"emit": staticmethod(lambda *a, **k: None)})()

        def tr(self, key, /, **params):
            return i18n.tr(f"proofread.{key}", **params)

        def get_service(self, name):
            return {"i18n": i18n, "config": i18n.config}.get(name)

    page = DashboardPage(_Plugin(_Ctx()))
    page.build_secondary_panel()
    page.retranslateUi()

    for attr in ("placeholder_help", "prompt_hint", "proof_language_label"):
        label = getattr(page, attr, None)
        assert label is not None, f"缺控件 {attr}"
        assert not CJK.search(label.text()), f"{attr} 仍是中文：{label.text()[:60]}"
    assert page.proof_language_edit.itemText(0) == "Auto detect"


def test_retranslate_block_is_not_inside_build_method():
    """重译代码必须在 retranslateUi 里，不能留在 _build_prompt_card 内部（否则永不生效）。"""
    source = (ROOT / "src/plugins/proofread/dashboard_page.py").read_text(encoding="utf-8")
    build = source[source.index("def _build_prompt_card"):source.index("def _build_cleanup_card")]
    retranslate = source[source.index("def retranslateUi"):]

    assert "placeholder_help.setText" not in build, "构建方法里不该做重译"
    assert "placeholder_help.setText" in retranslate
