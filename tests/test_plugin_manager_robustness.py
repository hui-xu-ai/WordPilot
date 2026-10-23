# tests/test_plugin_manager_robustness.py
"""P10-B5：插件管理器加固的三条契约。

审计 H8/H9/H10：
- 任一插件 `deps` 有误时 `load_all()` 直接 ``return []`` ⇒ **内置插件全部不加载**，界面成空壳；
- `activate()` 抛异常不回滚已注册的 UI/热键/订阅 ⇒ 界面留下点不动的菜单项；
- `install()` 不校验 `manifest.id` ⇒ `shutil.rmtree(USER_PLUGINS_DIR / id)` 可越界删目录。
"""
from __future__ import annotations

import json
import zipfile

import pytest

from core.plugin_manager import PluginError, PluginManager, PluginState
from core.sdk.plugin_base import PluginManifest


class StubKernel:
    """只提供 PluginManager 用到的最小面：root_dir / services / events / ui。"""

    class _Services(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

        def __contains__(self, key):
            return dict.__contains__(self, key)

        @property
        def names(self):
            return tuple(self)

    def __init__(self, root_dir, plugins=()):
        self.root_dir = root_dir
        self.services = self._Services()
        self.events = type("E", (), {"emit": staticmethod(lambda *a, **k: None)})()
        self.ui = type("U", (), {"remove_plugin": staticmethod(lambda pid: None)})()

    def __getattr__(self, item):
        raise AttributeError(item)


def _manifest(pid: str, deps: list[str] | None = None) -> PluginManifest:
    return PluginManifest(id=pid, deps=list(deps or []))


@pytest.fixture
def manager(tmp_path):
    kernel = StubKernel(tmp_path)
    return PluginManager(kernel, plugins_root=tmp_path / "plugins")


# ── 依赖排序：坏插件只影响自己 ─────────────────────────────────────
def test_missing_dep_only_fails_that_plugin(manager):
    manifests = [_manifest("good"), _manifest("broken", ["nope"]), _manifest("good2")]
    ordered, failed = manager._sort_by_deps(manifests)
    assert [m.id for m in ordered] == ["good", "good2"], "其余插件必须照常排序到位"
    assert "broken" in failed and "nope" in failed["broken"]


def test_cycle_fails_only_members(manager):
    manifests = [_manifest("a", ["b"]), _manifest("b", ["a"]), _manifest("c")]
    ordered, failed = manager._sort_by_deps(manifests)
    assert "c" in [m.id for m in ordered]
    assert {"a", "b"} <= set(failed)


def test_dependency_order_is_respected(manager):
    manifests = [_manifest("top", ["mid"]), _manifest("mid", ["base"]), _manifest("base")]
    ordered, failed = manager._sort_by_deps(manifests)
    assert not failed
    assert [m.id for m in ordered] == ["base", "mid", "top"]


def test_load_all_keeps_loading_when_one_dep_is_bad(manager, monkeypatch):
    manifests = [_manifest("good"), _manifest("broken", ["nope"])]
    monkeypatch.setattr(manager, "discover", lambda: manifests)
    loaded: list[str] = []

    def fake_load(manifest, force=False):
        loaded.append(manifest.id)
        record = manager._records.setdefault(manifest.id, _Record(manifest))
        record.state = PluginState.ACTIVE
        return record

    from core.plugin_manager import PluginRecord as _Record  # noqa: N814
    monkeypatch.setattr(manager, "load", fake_load)
    records = manager.load_all()

    assert loaded == ["good"], "坏插件不得阻断其余插件加载"
    broken = [r for r in records if r.manifest.id == "broken"]
    assert broken and broken[0].state == PluginState.FAILED
    assert "nope" in (broken[0].error or "")


# ── install：id 必须是安全目录名 ────────────────────────────────────
def _make_zip(path, plugin_id: str) -> None:
    payload = {"id": plugin_id, "name": plugin_id, "entry": "plugin:Plugin"}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{plugin_id}/plugin.json", json.dumps(payload))
        zf.writestr(f"{plugin_id}/plugin.py", "from core.sdk.plugin_base import PluginBase\n")


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "..", "con:", ""])
def test_install_rejects_unsafe_id(manager, tmp_path, monkeypatch, bad_id):
    from core import plugin_manager as pm_mod

    monkeypatch.setattr(pm_mod, "USER_PLUGINS_DIR", tmp_path / "plugins_user")
    (tmp_path / "plugins_user").mkdir()
    archive = tmp_path / "bad.zip"
    _make_zip(archive, bad_id)
    with pytest.raises((PluginError, ValueError)):
        manager.install(archive)
    assert not (tmp_path / "escape").exists()


def test_install_accepts_safe_id(manager, tmp_path, monkeypatch):
    from core import plugin_manager as pm_mod

    monkeypatch.setattr(pm_mod, "USER_PLUGINS_DIR", tmp_path / "plugins_user")
    archive = tmp_path / "ok.zip"
    _make_zip(archive, "hello-world_2")
    manifest = manager.install(archive)
    assert manifest.id == "hello-world_2"
    assert (tmp_path / "plugins_user" / "hello-world_2" / "plugin.json").is_file()
