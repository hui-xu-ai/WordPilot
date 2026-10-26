# tests/test_config_manager.py
"""P10-B2：配置不丢、不坏、原子写。

历史实现裸 ``except: pass`` ⇒ 配置损坏静默回默认值，随后 ``save()`` 用默认值覆盖用户
api_key/热键/主题；``get_api_key`` 把任何解密失败都当"历史明文"再加密一遍，``.key`` 一丢就永久写坏。
"""
from __future__ import annotations

import json

from core.services import config_manager as cm_mod


def test_new_config_uses_shipped_default_skin(config_env):
    from core.services.skin_service import DEFAULT_SKIN_ID, SkinService

    manager = cm_mod.ConfigManager()
    assert manager.get("skin") == DEFAULT_SKIN_ID == "mochi_bunny"
    assert DEFAULT_SKIN_ID in {skin["id"] for skin in SkinService(manager).available()}


def test_corrupt_config_is_backed_up_not_destroyed(config_env):
    cfg_file = config_env / "MWindow_config.json"
    cfg_file.write_text("{ 这不是 JSON", encoding="utf-8")
    manager = cm_mod.ConfigManager()            # 不得抛
    backups = list(config_env.glob("MWindow_config.json.corrupt-*"))
    assert backups, "损坏的配置必须先备份再回退默认值"
    assert backups[0].read_text(encoding="utf-8") == "{ 这不是 JSON"
    assert manager.get("model")                  # 默认值仍可用


def test_api_key_ciphertext_failure_does_not_overwrite(config_env):
    manager = cm_mod.ConfigManager()
    manager.config["api_key"] = "gAAAAA-invalid-ciphertext"   # 看着像密文但解不开
    manager.save()
    assert manager.get_api_key() == ""                        # 明确告知读不出
    assert manager.config["api_key"] == "gAAAAA-invalid-ciphertext", "不得把密文写坏"


def test_plain_api_key_is_migrated_once(config_env):
    manager = cm_mod.ConfigManager()
    manager.config["api_key"] = "sk-plaintext-key"
    assert manager.get_api_key() == "sk-plaintext-key"
    assert manager.config["api_key"].startswith("gAAAAA"), "历史明文应被加密回写"


def test_save_is_atomic_and_leaves_no_temp(config_env):
    manager = cm_mod.ConfigManager()
    manager.set("theme", "dark")
    files = {p.name for p in config_env.iterdir()}
    assert not [n for n in files if n.endswith(".tmp")], f"残留临时文件: {files}"
    saved = json.loads((config_env / "MWindow_config.json").read_text(encoding="utf-8"))
    assert saved["theme"] == "dark"


def test_save_reports_failure_when_tmp_cannot_be_created(monkeypatch, tmp_path):
    """B06：mkstemp 失败也要走失败契约（返回 False + last_save_error），不能直接抛。"""
    import tempfile as _tempfile

    from core.services.config_manager import ConfigManager

    manager = ConfigManager()

    def _boom(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(_tempfile, "mkstemp", _boom)

    assert manager.save() is False, "保存失败必须返回 False"
    assert manager.last_save_error, "必须记录失败原因，供界面提示"
