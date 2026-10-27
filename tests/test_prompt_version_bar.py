# tests/test_prompt_version_bar.py
"""版本条（挂在各插件提示词编辑区旁）：保存即生效、切换版本、默认版本不可删。"""
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
    yield


class _Config:
    def __init__(self):
        self.data = {"rewrite_prompt": "初始提示：{text}"}
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


def _bar(config, holder, key="rewrite_prompt", **kwargs):
    from core.widgets.prompt_version_bar import PromptVersionBar

    return PromptVersionBar(key, config, kwargs.get("tr", lambda k, **kw: k),
                            read_text=lambda: holder["text"],
                            apply_text=lambda text: holder.__setitem__("text", text),
                            prompt_tr=kwargs.get("prompt_tr"))


def test_commit_saves_config_and_creates_version(app):
    """页面「保存」= 让提示词生效 + 更新版本（不再需要另存为 + 起名）。"""
    from core.services import prompt_store

    config = _Config()
    holder = {"text": "我的改写提示：{text}"}
    bar = _bar(config, holder, prompt_tr=lambda key: "默认：{text}")

    assert bar.commit() is True

    names = [item["name"] for item in prompt_store.versions("rewrite_prompt")]
    assert "我的版本 1" in names, f"当前是默认版本时应新建一个自定义版本，实际 {names}"
    assert prompt_store.active_name("rewrite_prompt") == "我的版本 1"
    assert prompt_store.read("rewrite_prompt", "我的版本 1") == "我的改写提示：{text}"
    assert config.data["rewrite_prompt"] == "我的改写提示：{text}"
    assert config.saved >= 1


def test_second_commit_updates_same_version_with_backup(app):
    from core.services import prompt_store

    config = _Config()
    holder = {"text": "第一版：{text}"}
    bar = _bar(config, holder, prompt_tr=lambda key: "默认：{text}")
    bar.commit()

    holder["text"] = "第二版：{text}"
    bar.commit()

    names = [item["name"] for item in prompt_store.versions("rewrite_prompt")]
    assert "我的版本 1" in names and "我的版本 2" not in names, "同一版本就地更新"
    assert prompt_store.read("rewrite_prompt", "我的版本 1") == "第二版：{text}"
    assert any(name.startswith("自动-") for name in names), "覆盖前应留自动备份"


def test_empty_prompt_is_not_committed(app):
    config = _Config()
    bar = _bar(config, {"text": "   "})

    assert bar.commit() is False
    assert config.saved == 0


def test_switching_version_applies_text(app):
    from core.services import prompt_store

    prompt_store.save_version("rewrite_prompt", "版本A", "A：{text}", activate=True)
    prompt_store.save_version("rewrite_prompt", "版本B", "B：{text}")
    config = _Config()
    holder = {"text": "A：{text}"}
    bar = _bar(config, holder)

    bar.combo.setCurrentIndex(bar.combo.findData("版本B"))

    assert holder["text"] == "B：{text}"
    assert config.data["rewrite_prompt"] == "B：{text}"


def test_default_version_cannot_be_deleted(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from core.services import prompt_store

    prompt_store.save_version("rewrite_prompt", prompt_store.DEFAULT_VERSION, "默认：{text}",
                              activate=True)
    config = _Config()
    bar = _bar(config, {"text": "默认：{text}"})
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: warned.append(a[-1])))

    bar._on_delete()

    assert prompt_store.active_name("rewrite_prompt") == prompt_store.DEFAULT_VERSION
    assert warned, "应提示默认版本不能删除"
    assert prompt_store.delete_version("rewrite_prompt", prompt_store.DEFAULT_VERSION) is False


def test_reset_uses_language_default(app):
    from core.services import prompt_store

    prompt_store.save_version("rewrite_prompt", "旧", "旧：{text}", activate=True)
    config = _Config()
    holder = {"text": "旧：{text}"}
    bar = _bar(config, holder)
    bar._default_text = lambda: "默认：{text}"      # 语言包默认（测试里注入）

    bar._on_reset()

    assert holder["text"] == "默认：{text}"
    assert config.data["rewrite_prompt"] == "", "清空 config 才会回落到默认提示词"
