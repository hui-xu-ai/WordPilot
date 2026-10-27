# tests/test_prompt_version_bar.py
"""版本条（挂在各插件提示词编辑区旁）：另存 / 切换 / 恢复默认都真的写回 config。"""
from __future__ import annotations

import pytest


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    from core.services import prompt_store

    monkeypatch.setattr(prompt_store, "_store_dir", lambda: tmp_path / "prompts")
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


def _bar(config, holder, key="rewrite_prompt"):
    from core.widgets.prompt_version_bar import PromptVersionBar

    return PromptVersionBar(key, config, lambda k, **kw: k,
                            read_text=lambda: holder["text"],
                            apply_text=lambda text: holder.__setitem__("text", text))


def test_save_version_writes_config(app, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    config = _Config()
    holder = {"text": "我的改写提示：{text}"}
    bar = _bar(config, holder)

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("我的版本", True)))
    bar._on_save()

    from core.services import prompt_store

    assert [item["name"] for item in prompt_store.versions("rewrite_prompt")] == ["我的版本"]
    assert prompt_store.active_name("rewrite_prompt") == "我的版本"
    assert config.data["rewrite_prompt"] == "我的改写提示：{text}"
    assert config.saved >= 1
    assert bar.combo.currentData() == "我的版本"


def test_switching_version_applies_text(app):
    from core.services import prompt_store

    prompt_store.save_version("rewrite_prompt", "版本A", "A：{text}", activate=True)
    prompt_store.save_version("rewrite_prompt", "版本B", "B：{text}")
    config = _Config()
    holder = {"text": "A：{text}"}
    bar = _bar(config, holder)

    index = bar.combo.findData("版本B")
    bar.combo.setCurrentIndex(index)

    assert holder["text"] == "B：{text}"
    assert config.data["rewrite_prompt"] == "B：{text}"


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
