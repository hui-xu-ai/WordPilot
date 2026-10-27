# tests/test_prompt_store.py
"""提示词版本仓库：保存/切换/删除/恢复，以及各场景提示词互相独立。

用户要求（2026-10-27）：提示词可保存备份并切换；翻译改写页的纠错与智能纠错插件分别管理。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clean_store(tmp_path, monkeypatch):
    from core.services import prompt_store

    monkeypatch.setattr(prompt_store, "_store_dir", lambda: tmp_path / "prompts")
    yield


def test_save_and_activate_roundtrip():
    from core.services import prompt_store

    prompt_store.save_version("proof_prompt", "我的版本", "校对：{text}", activate=True)
    prompt_store.save_version("proof_prompt", "第二版", "校对2：{text}")

    names = [item["name"] for item in prompt_store.versions("proof_prompt")]
    assert names == ["我的版本", "第二版"]
    assert prompt_store.active_name("proof_prompt") == "我的版本", "首个版本自动成为当前"

    text = prompt_store.activate("proof_prompt", "第二版")
    assert text == "校对2：{text}"
    assert prompt_store.active_name("proof_prompt") == "第二版"


def test_backup_keeps_old_text_and_caps_count():
    from core.services import prompt_store

    prompt_store.save_version("rewrite_prompt", "当前", "旧内容", activate=True)
    for index in range(12):
        prompt_store.backup_active("rewrite_prompt", f"旧内容 {index}")

    autos = [item for item in prompt_store.versions("rewrite_prompt")
             if item["name"].startswith("自动-")]
    assert 0 < len(autos) <= prompt_store.AUTO_KEEP


def test_delete_keeps_at_least_one_version():
    from core.services import prompt_store

    prompt_store.save_version("translate_prompt", "唯一", "翻译：{text}", activate=True)
    assert prompt_store.delete_version("translate_prompt", "唯一") is False
    assert prompt_store.versions("translate_prompt"), "不能把唯一版本删掉"

    prompt_store.save_version("translate_prompt", "另一版", "翻译2：{text}")
    assert prompt_store.delete_version("translate_prompt", "另一版") is True
    assert prompt_store.active_name("translate_prompt") == "唯一"


def test_each_prompt_key_is_isolated():
    from core.services import prompt_store

    prompt_store.save_version("proof_prompt", "校对版", "校对：{text}", activate=True)
    prompt_store.save_version("correct_prompt", "纠错版", "纠错：{text}", activate=True)

    assert prompt_store.read("proof_prompt", "校对版") == "校对：{text}"
    assert prompt_store.read("correct_prompt", "纠错版") == "纠错：{text}"
    assert prompt_store.versions("proof_prompt")[0]["name"] == "校对版"


def test_seed_from_config_registers_existing_text():
    from core.services import prompt_store

    class _Config:
        def __init__(self):
            self._data = {"proof_prompt": "已有提示：{text}", "rewrite_prompt": ""}

        def get(self, key, default=None):
            return self._data.get(key, default)

    assert prompt_store.seed_from_config(_Config()) == 1
    assert prompt_store.active_name("proof_prompt") == "当前"
    assert prompt_store.seed_from_config(_Config()) == 0, "第二次不应重复登记"


def test_api_client_decouples_correct_from_proof():
    """correct_prompt 不再回退 proof_prompt；{lang} 会被替换。"""
    from core.services.api_client import DeepSeekClient

    class _Config:
        def __init__(self):
            self._data = {"proof_prompt": "校对：{text}", "correct_prompt": "",
                          "proof_language": "auto"}

        def get(self, key, default=None):
            return self._data.get(key, default)

    class _I18n:
        def prompt_tr(self, key, **kwargs):
            return {
                "prompt.correct.default": "纠错默认：{text}",
                "prompt.proof.default": "校对默认：{text}",
                "prompt.proof.system": "sys",
                "prompt.proof.placeholder_system": "sys",
            }.get(key, key)

    client = DeepSeekClient.__new__(DeepSeekClient)
    client.config = _Config()
    client.i18n = _I18n()
    client.last_error = ""
    client.model = "test"

    assert client.target_language("这是一段中文文本，用来判断语言。") == "中文"
    assert client.target_language("This is an English sentence for testing.") == "English"

    seen: list[str] = []

    def _capture(messages):
        seen.append(messages[1]["content"])
        return "ok"

    client._call_api = _capture               # type: ignore[assignment]
    client.last_usage = {"prompt": 0, "completion": 0, "total": 0}
    client.last_latency_ms = 0

    client.proof_text("这是一段中文。", prompt_key="correct_prompt", cache_namespace="t1")

    assert seen and "纠错默认" in seen[-1], f"应使用纠错自己的默认提示词，实际 {seen[-1][:40]}"
