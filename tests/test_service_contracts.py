# tests/test_service_contracts.py
"""P10 整改（批次 A0/B1/B2/B3）的契约测试。

这三条服务原来用"同一个返回类型"表达成功与失败、并且吞掉损坏数据，测试要钉死新契约：
- `DeepSeekClient`：失败返回 `None` + `last_error`，**绝不把错误文案当内容返回**；
- `ConfigManager`：配置损坏要备份 + 不覆盖、api_key 密文不可解时不得回写覆盖、保存要原子；
- `IgnoreCacheManager`：写入原子、损坏文件备份后返回空表（不静默清空用户数据）。
"""
from __future__ import annotations

import json

import pytest

from core.services import api_client as api_mod
from core.services import config_manager as cm_mod
from core.services import ignore_cache as ig_mod


# ── 共用替身 ────────────────────────────────────────────────────────
class FakeI18n:
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
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


class NoCache:
    def get(self, key):
        return None

    def put(self, key, value, model=""):
        return None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_mod, "log_api_call", lambda *a, **k: None)
    monkeypatch.setattr(api_mod, "get_default_cache", lambda: NoCache())
    cfg = Cfg({"model": "deepseek-v4-flash", "proof_prompt": "", "translate_prompt": "",
               "rewrite_prompt": ""})
    return api_mod.DeepSeekClient(cfg, i18n=FakeI18n())


# ── B1 返回值契约 ───────────────────────────────────────────────────
def test_proof_text_returns_none_when_api_fails(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(api_mod.requests, "post", boom)
    assert client.proof_text("原句") is None
    assert "network down" in client.last_error or client.last_error


def test_proof_text_never_returns_error_message(client, monkeypatch):
    monkeypatch.setattr(api_mod.requests, "post",
                        lambda *a, **k: type("R", (), {"status_code": 500, "text": "boom"})())
    result = client.proof_text("原句")
    assert result is None, f"错误文案不得当内容返回，实际={result!r}"


def test_translate_uses_language_pack_default_when_config_blank(client, monkeypatch):
    """config 里 translate_prompt 恒存在（setdefault 空串）⇒ 必须 or 兜底到语言包默认文案。"""
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["prompt"] = json["messages"][-1]["content"]
        return type("R", (), {"status_code": 200,
                              "json": lambda self=None: {"choices": [{"message": {"content": "译文"}}]}})()

    monkeypatch.setattr(api_mod.requests, "post", fake_post)
    assert client.translate("原文") == "译文"
    assert "原文" in seen["prompt"]


def test_translate_empty_input_returns_none(client):
    assert client.translate("   ") is None


def test_test_connection_false_on_failure(client, monkeypatch):
    monkeypatch.setattr(api_mod.requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert client.test_connection() is False


# ── B2 配置：不丢、不坏、原子 ───────────────────────────────────────
@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    monkeypatch.setattr(cm_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cm_mod, "CONFIG_FILE", tmp_path / "MWindow_config.json")
    monkeypatch.setattr(cm_mod, "KEY_FILE", tmp_path / ".key")
    return tmp_path


def test_corrupt_config_is_backed_up_not_destroyed(cfg_env):
    cfg_file = cfg_env / "MWindow_config.json"
    cfg_file.write_text("{ 这不是 JSON", encoding="utf-8")
    manager = cm_mod.ConfigManager()            # 不得抛
    backups = list(cfg_env.glob("MWindow_config.json.corrupt-*"))
    assert backups, "损坏的配置必须先备份再回退默认值"
    assert backups[0].read_text(encoding="utf-8") == "{ 这不是 JSON"
    assert manager.get("model")                  # 默认值仍可用


def test_api_key_ciphertext_failure_does_not_overwrite(cfg_env):
    manager = cm_mod.ConfigManager()
    manager.config["api_key"] = "gAAAAA-invalid-ciphertext"   # 看着像密文但解不开
    manager.save()
    assert manager.get_api_key() == ""                        # 明确告知读不出
    assert manager.config["api_key"] == "gAAAAA-invalid-ciphertext", "不得把密文写坏"


def test_plain_api_key_is_migrated_once(cfg_env):
    manager = cm_mod.ConfigManager()
    manager.config["api_key"] = "sk-plaintext-key"
    assert manager.get_api_key() == "sk-plaintext-key"
    assert manager.config["api_key"].startswith("gAAAAA"), "历史明文应被加密回写"


def test_save_is_atomic_and_leaves_no_temp(cfg_env):
    manager = cm_mod.ConfigManager()
    manager.set("theme", "dark")
    files = {p.name for p in cfg_env.iterdir()}
    assert not [n for n in files if n.endswith(".tmp")], f"残留临时文件: {files}"
    assert json.loads((cfg_env / "MWindow_config.json").read_text(encoding="utf-8"))["theme"] == "dark"


# ── B3 忽略库：原子写 + 损坏备份 ────────────────────────────────────
@pytest.fixture
def ignore_env(tmp_path, monkeypatch):
    db = tmp_path / "ignore_db.json"
    monkeypatch.setattr(ig_mod, "IGNORE_DB", db)
    return db


def test_ignore_add_persists_atomically(ignore_env):
    manager = ig_mod.IgnoreCacheManager()
    manager.add_ignore("这句话我要忽略。")
    assert manager.is_ignored("这句话我要忽略。")
    assert json.loads(ignore_env.read_text(encoding="utf-8")), "写入后应可解析"
    assert not [p.name for p in ignore_env.parent.iterdir() if p.name.endswith(".tmp")]


def test_corrupt_ignore_db_is_backed_up_and_not_cleared(ignore_env):
    ignore_env.write_text("[坏的", encoding="utf-8")
    manager = ig_mod.IgnoreCacheManager()
    assert manager.ignore_hashes == []
    backups = list(ignore_env.parent.glob("ignore_db.json.bad-*"))
    assert backups, "损坏的忽略库必须先备份"
    assert backups[0].read_text(encoding="utf-8") == "[坏的"
