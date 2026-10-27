# tests/test_prompt_version_bar_default.py
"""默认提示词必须取应用级语言包（`prompt.*`），不能拿插件命名空间去查。

现场（用户截图）：校对框里显示 `proofread.prompt.proof.default`，
版本下拉显示 `2proofread.prompt.version.act` —— 插件 `tr()` 带命名空间，查 `prompt.*` 只会原样
返回 key，于是 key 被当成提示词写进了版本文件。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def qapp():
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

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        pass


def _bar(prompt_tr=None, tr=None):
    from core.widgets.prompt_version_bar import PromptVersionBar

    return PromptVersionBar("proof_prompt", _Config(), tr or (lambda key, **kw: key),
                            read_text=lambda: "", apply_text=lambda text: None,
                            prompt_tr=prompt_tr)


def test_default_text_uses_app_prompt_translation(qapp):
    bar = _bar(prompt_tr=lambda key: "你是{lang}学术论文校对专家。{text}")

    assert bar._default_text() == "你是{lang}学术论文校对专家。{text}"


def test_never_writes_raw_key_as_prompt(qapp):
    """插件 tr 查不到时返回 key —— 这种情况必须返回空，不能把 key 当提示词。"""
    bar = _bar(prompt_tr=None, tr=lambda key, **kw: f"proofread.{key}")

    assert bar._default_text() == ""


def test_raw_key_versions_are_repaired(qapp, temp_store):
    from core.services import prompt_store

    # 模拟早期写坏的版本：内容就是原始 key
    prompt_store.save_version("correct_prompt", "默认", "clipboard.prompt.correct.default",
                              activate=True)
    prompt_store.save_version("rewrite_prompt", "默认", "按{style}风格改写：{text}", activate=True)

    removed = prompt_store.repair_bogus_versions()

    assert removed == 1
    assert prompt_store.versions("correct_prompt") == []
    assert [item["name"] for item in prompt_store.versions("rewrite_prompt")] == ["默认"]
