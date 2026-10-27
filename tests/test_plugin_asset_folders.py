# tests/test_plugin_asset_folders.py
"""资产按插件分文件夹：提示词存 `config/plugins/<插件>/prompts/`，整目录永不清理。

用户要求（2026-10-27）：提示词也是资产；每个插件有专门资产文件夹，清理时按文件夹区分。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def temp_config(tmp_path, monkeypatch):
    from core.services import housekeeping, prompt_store

    monkeypatch.setattr(prompt_store, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(housekeeping, "config_dir", lambda: tmp_path)
    yield tmp_path


def test_prompt_versions_live_in_plugin_folders(temp_config):
    from core.services import prompt_store

    assert prompt_store._key_dir("proof_prompt", temp_config) == \
        temp_config / "plugins" / "proofread" / "prompts" / "proof_prompt"
    assert prompt_store._key_dir("rewrite_prompt", temp_config) == \
        temp_config / "plugins" / "clipboard" / "prompts" / "rewrite_prompt"

    prompt_store.save_version("proof_prompt", "我的版本", "校对：{text}", activate=True)

    assert (temp_config / "plugins/proofread/prompts/proof_prompt/index.json").exists()


def test_default_version_named_default(temp_config):
    from core.services import prompt_store

    class _Config:
        def get(self, key, default=None):
            return "校对：{text}" if key == "proof_prompt" else ""

    prompt_store.seed_from_config(_Config())

    assert prompt_store.active_name("proof_prompt") == prompt_store.DEFAULT_VERSION == "默认"


def test_legacy_prompt_dir_is_migrated(temp_config):
    from core.services import prompt_store

    legacy = temp_config / "prompts" / "translate_prompt"
    legacy.mkdir(parents=True)
    (legacy / "index.json").write_text(
        '{"active": "当前", "versions": [{"name": "当前", "file": "mine.md"}]}', encoding="utf-8")
    (legacy / "mine.md").write_text("翻译：{text}", encoding="utf-8")

    assert prompt_store.migrate_legacy(temp_config) == 1
    assert not legacy.exists(), "旧目录应被搬走"
    assert prompt_store.read("translate_prompt", "默认", temp_config) == "翻译：{text}"
    assert prompt_store.versions("translate_prompt", temp_config)[0]["name"] == "默认"


def test_housekeeping_never_cleans_plugin_folders(temp_config):
    from core.services import housekeeping

    plugin_dir = temp_config / "plugins" / "reference"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "notes.csv").write_text("x", encoding="utf-8")
    (plugin_dir / "app.log").write_text("log", encoding="utf-8")      # 即便叫 .log 也不清
    (plugin_dir / "cache.db-wal").write_text("wal", encoding="utf-8")
    (temp_config / "app.log").write_text("root log", encoding="utf-8")

    groups = {group.key: group for group in housekeeping.inventory(temp_config)}
    files = [str(path) for group in groups.values() for path in group.files]

    assert not any("plugins" in path for path in files), f"插件资产目录不该出现在清理清单：{files}"
    assert any(path.endswith("app.log") and "plugins" not in path for path in files)

    housekeeping.clean(["logs", "wal"], root=temp_config)
    assert (plugin_dir / "app.log").exists() and (plugin_dir / "cache.db-wal").exists()
    assert not (temp_config / "app.log").exists()
