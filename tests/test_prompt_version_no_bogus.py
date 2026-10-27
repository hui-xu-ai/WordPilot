# tests/test_prompt_version_no_bogus.py
"""「我的版本 1」不该被自动创建。

现场（2026-10-27 截图）：英文界面里校对提示词框显示中文默认文案，版本下拉出现
「我的版本 1 · 当前」——用户什么都没改。
根因：切语言时 `refresh_language()` 会把编辑框换成新的默认文案，这触发 textChanged →
去抖自动保存 → `commit()` 发现当前是「默认」，就新建了一个"我的版本 1"（内容＝默认文案）。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    from core.services import prompt_store

    monkeypatch.setattr(prompt_store, "_config_dir", lambda: tmp_path)
    yield tmp_path


class _Config:
    def __init__(self):
        self.data = {"proof_prompt": ""}
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


def _bar(config, holder, default_text="系统默认 {text}"):
    from core.widgets.prompt_version_bar import PromptVersionBar

    return PromptVersionBar("proof_prompt", config, lambda key, **kw: key,
                            read_text=lambda: holder["text"],
                            apply_text=lambda text: holder.__setitem__("text", text),
                            prompt_tr=lambda key: default_text)


def test_commit_with_default_text_does_not_create_version(app):
    from core.services import prompt_store

    config = _Config()
    holder = {"text": "系统默认 {text}"}
    bar = _bar(config, holder)

    assert bar.commit() is True

    names = [item["name"] for item in prompt_store.versions("proof_prompt")]
    assert "mine-1" not in names, names
    assert config.data["proof_prompt"] == "", "默认文案不该被固化进 config"


def test_commit_with_edited_text_creates_version(app):
    from core.services import prompt_store

    config = _Config()
    holder = {"text": "我改过的 {text}"}
    bar = _bar(config, holder)

    bar.commit()

    names = [item["name"] for item in prompt_store.versions("proof_prompt")]
    assert "mine-1" in names
    assert config.data["proof_prompt"] == "我改过的 {text}"


def test_language_refresh_then_autosave_creates_no_version(app):
    """模拟真实链路：切语言刷新默认文案 → 触发自动保存 → 不该产生新版本。"""
    from core.services import prompt_store

    config = _Config()
    holder = {"text": "中文默认 {text}"}
    texts = {"value": "中文默认 {text}"}
    from core.widgets.prompt_version_bar import PromptVersionBar

    bar = PromptVersionBar("proof_prompt", config, lambda key, **kw: key,
                           read_text=lambda: holder["text"],
                           apply_text=lambda text: holder.__setitem__("text", text),
                           prompt_tr=lambda key: texts["value"])
    texts["value"] = "English default {text}"
    bar.refresh_language()

    bar.commit()          # 自动保存拿到的是刚刷新的默认文案

    names = [item["name"] for item in prompt_store.versions("proof_prompt")]
    assert "mine-1" not in names, names


def test_redundant_versions_are_cleaned(app):
    from core.services import prompt_store

    prompt_store.save_version("proof_prompt", "mine-1", "系统默认 {text}", activate=True)
    prompt_store.save_version("proof_prompt", "mine-2", "真的改过 {text}")

    removed = prompt_store.drop_redundant_versions("proof_prompt",
                                                  lambda key: "系统默认 {text}")

    names = [item["name"] for item in prompt_store.versions("proof_prompt")]
    assert removed == 1
    assert "mine-1" not in names
    assert "mine-2" in names, "真正改过的版本必须保留"
