# tests/conftest.py
"""pytest 共用夹具：路径常量 + 最小 config 替身。

- 替身范式沿用 `tools/smoke_p4_pet.py:19-23` 的 `Cfg(dict)`：内核服务只依赖 `get/set`；
- `src/` 由 `pytest.ini` 的 `pythonpath` 加进 `sys.path`，此处不再重复注入；
- 需要真实用户配置/用户目录的断言**不放这里**（GUI 与真实链路由 `tools/smoke_*` 负责）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Qt 相关单测一律离屏：不需要桌面会话，也绝不能真的弹窗（smoke 之外不应出现可见窗口）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
ASSETS_DIR = SRC_DIR / "assets"
PLUGINS_DIR = SRC_DIR / "plugins"


class FakeConfig(dict):
    """config 替身：只实现 get/set，够 SkinService / ThemeService / I18nService 用。"""

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value) -> None:
        self[key] = value


@pytest.fixture
def fake_config() -> FakeConfig:
    return FakeConfig()


@pytest.fixture(scope="session")
def assets_dir() -> Path:
    return ASSETS_DIR


@pytest.fixture(scope="session")
def skins_dir() -> Path:
    return ASSETS_DIR / "skins"


@pytest.fixture(scope="session")
def themes_dir() -> Path:
    return ASSETS_DIR / "themes"


@pytest.fixture(scope="session")
def plugin_dirs() -> list[Path]:
    """自带插件的包目录（含 plugin.json 的目录），按名字排序以保证参数化稳定。"""
    return sorted(p for p in PLUGINS_DIR.iterdir() if (p / "plugin.json").is_file())


# ---------------- 服务层替身（api_client / config / i18n 测试共用） ----------------
class FakeI18n:
    """只实现 prompt_tr 的最小 i18n 替身（提示词用）。"""

    _MAP = {
        "prompt.proof.system": "sys",
        "prompt.proof.placeholder_system": "sys2",
        "prompt.proof.default": "改错：{text}",
        "prompt.translate.default": "翻译：{text} -> {target_lang}",
        "prompt.rewrite.default": "改写：{text}",
        "prompt.translate.system": "s",
        "prompt.rewrite.system": "s",
        "prompt.error": "API 调用失败",
    }

    def prompt_tr(self, key: str) -> str:
        return self._MAP.get(key, key)


class Cfg(dict):
    """普通 dict 版 config 替身（DeepSeekClient 走明文分支）。"""

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


class NoCache:
    """校对缓存替身：永远不命中、也不落盘。"""

    def get(self, key):
        return None

    def put(self, key, value, model=""):
        return None


@pytest.fixture
def api_client(monkeypatch):
    """DeepSeekClient：日志与缓存都替身化，不联网、不写盘。"""
    from core.services import api_client as api_mod

    monkeypatch.setattr(api_mod, "log_api_call", lambda *a, **k: None)
    monkeypatch.setattr(api_mod, "get_default_cache", lambda: NoCache())
    cfg = Cfg({"model": "deepseek-v4-flash", "proof_prompt": "",
               "translate_prompt": "", "rewrite_prompt": ""})
    return api_mod.DeepSeekClient(cfg, i18n=FakeI18n())


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    """把 ConfigManager 的三个模块级路径指到 tmp_path（绝不碰 src/config/）。"""
    from core.services import config_manager as cm_mod

    monkeypatch.setattr(cm_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cm_mod, "CONFIG_FILE", tmp_path / "MWindow_config.json")
    monkeypatch.setattr(cm_mod, "KEY_FILE", tmp_path / ".key")
    return tmp_path
