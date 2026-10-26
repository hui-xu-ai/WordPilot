# tests/test_plugin_manager_external.py
"""S3/S4/S5：外装插件的相对导入、升级后重载、依赖失败传递。

审查证据（2026-10-25-strict-audit）：
- S3 入口名是 user_plugins.<id>，却没建父包/__path__ → `from .helper import X` 报
  No module named 'user_plugins'；
- S4 升级覆盖磁盘文件后仍执行旧代码（sys.modules 复用），清单 2.0 而运行值 1.0；
- S5 dep 加载失败时 dependent 仍显示 active。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def manager(tmp_path):
    from core.plugin_manager import PluginManager

    class _Kernel:
        services = {}
        root_dir = tmp_path

    mgr = PluginManager(_Kernel(), plugins_root=tmp_path / "plugins")
    mgr._plugin_dirs = {}                      # 不读真实插件目录
    yield mgr
    for name in [n for n in sys.modules if n.startswith("user_plugins.")]:
        sys.modules.pop(name, None)


def _make_user_plugin(root: Path, plugin_id: str, version: str = "1.0") -> Path:
    directory = root / plugin_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "helper.py").write_text(f'VALUE = "{version}"\n', encoding="utf-8")
    (directory / "plugin.py").write_text(
        "from .helper import VALUE\n\n\nclass Plugin:\n    version = VALUE\n", encoding="utf-8")
    return directory


def _manifest(plugin_id: str, deps=None):
    from core.sdk.plugin_base import PluginManifest

    manifest = PluginManifest(id=plugin_id, name=plugin_id, entry="plugin:Plugin",
                              deps=list(deps or []))
    manifest._user_provided = True
    return manifest


def test_relative_import_inside_user_plugin(manager, tmp_path, monkeypatch):
    """S3：外装插件可以用相对导入引用同目录模块。"""
    directory = _make_user_plugin(tmp_path, "demo")
    monkeypatch.setattr(manager, "_plugin_dir", lambda manifest: directory)

    module = manager._import_entry_module(_manifest("demo"))

    assert module.Plugin.version == "1.0"


def test_upgrade_takes_effect_without_restart(manager, tmp_path, monkeypatch):
    """S4：磁盘上的插件升级后，再次导入必须拿到新代码。"""
    directory = _make_user_plugin(tmp_path, "demo", "1.0")
    monkeypatch.setattr(manager, "_plugin_dir", lambda manifest: directory)

    first = manager._import_entry_module(_manifest("demo"))
    assert first.Plugin.version == "1.0"

    _make_user_plugin(tmp_path, "demo", "2.0.0")        # 模拟安装覆盖文件（内容变化）
    second = manager._import_entry_module(_manifest("demo"))

    assert second.Plugin.version == "2.0.0", "升级后仍跑旧代码（sys.modules 未失效）"


def test_dependent_fails_when_dependency_not_active(manager, tmp_path, monkeypatch):
    """S5：依赖插件没激活时，依赖方不能显示为 active。"""
    from core.plugin_manager import PluginState

    base = _manifest("base")
    dependent = _manifest("dependent", deps=["base"])

    monkeypatch.setattr(manager, "_is_enabled", lambda manifest: True)
    monkeypatch.setattr(manager, "_register_plugin_i18n", lambda manifest: None)

    # base 先失败（缺内核服务），dependent 继而被判失败
    base.requires = ["missing_service"]
    manager.load(base)
    record = manager.load(dependent)

    assert record.state == PluginState.FAILED, f"dependent 状态错：{record.state}"
    assert "base" in (record.error or "")
