# tests/test_i18n_roundtrip_contract.py
"""语言切换契约（生产级）：中文 → 英文 → 中文，界面必须回到**逐字相同**的中文。

用户 2026-10-28 现场：「切换为英文，再切换回中文后，很多地方还是英文」。
根因是部分控件只在构造时取词，`retranslateUi()` 没覆盖（或页面根本没有该方法）。

这里守住两件事：
1. 每个 Qt 控件类必须实现 `retranslateUi()`（或列入白名单：只构造一次的模态对话框）；
2. 真实页面走一轮「中 → 英 → 中」，文字集合必须与初始快照一致。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")

#: 只构造一次、用完即弃的模态 UI：切语言时它们已关闭，允许不实现 retranslateUi
ALLOWLIST = {
    ("src/plugins/reference/bind_dialog.py", "ReferencePicker"),
    ("src/plugins/reference/note_editor.py", "NoteEditor"),
    ("src/plugins/reference/note_picker.py", "NotePicker"),
    ("src/plugins/reference/overlay.py", "ReferenceOverlay"),
    ("src/plugins/reference/overlay.py", "_EntryCard"),
    ("src/plugins/reference/overlay.py", "_CloseButton"),
    ("src/plugins/reference/switch_dialog.py", "SwitchDialog"),
    ("src/core/widgets/pet_window.py", "PetCanvas"),          # 画布，无文字
    # 导航项文字由 MainWindow.set_entries() 统一重设（宿主持有 key 列表），自身不存文案
    ("src/app/nav_rail.py", "NavRail"),
}

QT_BASES = {"QWidget", "QFrame", "QDockWidget", "QMainWindow", "QDialog", "QScrollArea"}


def _widget_classes(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {getattr(base, "id", None) or getattr(base, "attr", None) for base in node.bases}
        if not bases & QT_BASES:
            continue
        methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
        yield node.name, "retranslateUi" in methods


def test_every_widget_implements_retranslate():
    missing = []
    for directory in ("src/plugins", "src/app", "src/core/widgets"):
        for file in sorted((ROOT / directory).rglob("*.py")):
            relative = str(file.relative_to(ROOT)).replace("\\", "/")
            for name, has in _widget_classes(file):
                if has or (relative, name) in ALLOWLIST:
                    continue
                missing.append(f"{relative}::{name}")
    assert not missing, "以下控件没有 retranslateUi（切语言会残留旧语言）：" + ", ".join(missing)


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Config:
    def __init__(self, language: str = "zh_CN"):
        self.data = {"ui_language": language, "prompt_language": "auto"}
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1

    def get_api_key(self):
        return ""

    def set_api_key(self, value):
        self.data["api_key"] = value

    def update(self, payload):
        self.data.update(payload)
        return True


def _i18n(config):
    from core.services.i18n import I18nService

    service = I18nService(config, ROOT / "src/assets/i18n")
    for plugin in ("clipboard", "image_toolbox", "navigator", "proofread", "reference"):
        service.register_namespace(plugin, ROOT / f"src/plugins/{plugin}/i18n")
    return service


def _texts(widget) -> set[str]:
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QTableWidget

    found = {label.text() for label in widget.findChildren(QLabel)}
    found |= {button.text() for button in widget.findChildren(QAbstractButton)}
    for combo in widget.findChildren(QComboBox):
        found |= {combo.itemText(i) for i in range(combo.count())}
    for table in widget.findChildren(QTableWidget):
        for column in range(table.columnCount()):
            header = table.horizontalHeaderItem(column)
            if header is not None:
                found.add(header.text())
    return {text for text in found if text}


def _roundtrip(widget, i18n) -> None:
    widget.retranslateUi()
    baseline = _texts(widget)
    assert baseline, "页面没有任何文字，测试无效"

    i18n.set_language("en_US")
    widget.retranslateUi()
    english = _texts(widget)
    offenders = [t for t in english if CJK.search(t) and t.strip() not in {"中文", "简体中文"}]
    assert not offenders, f"英文下仍有中文：{offenders[:6]}"

    i18n.set_language("zh_CN")
    widget.retranslateUi()
    assert _texts(widget) == baseline, "切回中文后文字与最初不一致（有控件没重译）"


def test_settings_page_roundtrip(app):
    from app.settings_page import SettingsPage

    config = _Config("zh_CN")
    page = SettingsPage(config, i18n=_i18n(config))
    _roundtrip(page, _i18n(config) if False else page.i18n)


def test_proofread_side_panel_roundtrip(app):
    from plugins.proofread.dashboard_page import DashboardPage

    config = _Config("zh_CN")
    i18n = _i18n(config)

    class _Plugin:
        def __init__(self):
            self.ctx = _Ctx()
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
            self.config = config
            self.events = type("E", (), {"emit": staticmethod(lambda *a, **k: None)})()

        def tr(self, key, /, **params):
            return i18n.tr(f"proofread.{key}", **params)

        def get_service(self, name):
            return {"i18n": i18n, "config": config}.get(name)

    page = DashboardPage(_Plugin())
    page.build_secondary_panel()
    _roundtrip(page, i18n)


def test_clipboard_page_roundtrip(app):
    from plugins.clipboard.plugin import ClipboardWidget

    config = _Config("zh_CN")
    i18n = _i18n(config)

    class _Ctx:
        def __init__(self):
            self.i18n = i18n
            self.config = config
            self.events = type("E", (), {"emit": staticmethod(lambda *a, **k: None)})()

        def tr(self, key, /, **params):
            return i18n.tr(f"clipboard.{key}", **params)

        def get_service(self, name):
            return {"i18n": i18n, "config": config}.get(name)

    widget = ClipboardWidget(_Ctx())
    if hasattr(widget, "_build_side_panel"):
        widget._build_side_panel()
    _roundtrip(widget, i18n)
