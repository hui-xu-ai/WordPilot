# tests/conftest.py
"""pytest 共用夹具：路径常量 + 最小 config 替身。

- 替身范式沿用 `tools/smoke_p4_pet.py:19-23` 的 `Cfg(dict)`：内核服务只依赖 `get/set`；
- `src/` 由 `pytest.ini` 的 `pythonpath` 加进 `sys.path`，此处不再重复注入；
- 需要真实用户配置/用户目录的断言**不放这里**（GUI 与真实链路由 `tools/smoke_*` 负责）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

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
