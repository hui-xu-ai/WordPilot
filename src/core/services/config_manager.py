"""配置管理：读写 MWindow_config.json、Fernet 加密保存 api_key、提供 get/set。

P10-B2 的三条硬要求：
1. 配置损坏时**先备份再回退默认值**（历史实现裸 ``except: pass`` 会在下一次 ``save()`` 时
   用默认值覆盖掉用户 api_key/热键/主题）；
2. api_key 解密失败**只在「明显不是密文」时**做明文迁移，否则报错返回空串——
   历史实现会把合法密文当成明文再加密一遍，一旦 ``.key`` 丢失就永久写坏原值；
3. ``save()`` 原子写（tmp + os.replace），失败时不破坏旧文件并把原因留在 ``last_save_error``。
"""
# config_manager.py
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# 程序目录下的 config 子文件夹
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    # core/services/config_manager.py -> 上溯两级为 src/ 根目录
    BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "MWindow_config.json"
KEY_FILE = CONFIG_DIR / ".key"
DEFAULT_ENDPOINT = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

DEFAULT_CONFIG = {
    "api_key": "",
    "ignore_list": [],
    "suggestion_opacity": 0.95,
    "suggestion_font_size": 12,
    "show_floating_button": True,
    "auto_show_suggestion": True,
    # 配色已由主题令牌接管；校对缓存为 SQLite 主键查询，不再需要「查询条数」配置
}

class ConfigManager:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not KEY_FILE.exists():
            self._key = Fernet.generate_key()
            KEY_FILE.write_bytes(self._key)
        else:
            self._key = KEY_FILE.read_bytes()
        self._cipher = Fernet(self._key)
        self.last_save_error = ""
        self.config = self._load_config()

    # ---------------- 读取与损坏兜底 ----------------
    def _load_config(self):
        config = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._backup_corrupt(exc)
                config = {}
            if not isinstance(config, dict):
                self._backup_corrupt(ValueError(f"顶层应为对象，实际 {type(config).__name__}"))
                config = {}
        # 合并默认值，保留已有键
        for key, value in DEFAULT_CONFIG.items():
            config.setdefault(key, value)
        # 补全其他必要默认项
        config.setdefault("api_endpoint", DEFAULT_ENDPOINT)
        config.setdefault("model", DEFAULT_MODEL)

# --修改16
        config.setdefault("temperature", 0.4)
        config.setdefault("max_tokens", 2000)
# --修改16

        # 提示词默认留空：空串时由语言包按「提示词语言」给出默认文案
        config.setdefault("proof_prompt", "")
        config.setdefault("translate_prompt", "")
        config.setdefault("rewrite_prompt", "")

        config.setdefault("debounce_ms", 300)
        config.setdefault("context_before", 1)
        config.setdefault("context_after", 1)
        config.setdefault("hotkeys", {
            "toggle_monitor": "ctrl+shift+e",
            "proof_cursor": "ctrl+shift+z",
            "translate": "ctrl+shift+t",
            "rewrite": "ctrl+shift+r"
        })
        # 外观（P2 起由 theme_service 消费；P3 起 ui_language 由 i18n 服务消费）
        config.setdefault("theme", "light")
        config.setdefault("ui_language", "zh_CN")       # 界面语言（与提示词语言解耦）
        config.setdefault("prompt_language", "zh_CN")   # 送 AI 的提示词语言，独立控制
        config.setdefault("font_family", "Microsoft YaHei")
        config.setdefault("font_size", 12)
        config.setdefault("exclude_patterns", ["参考文献", "致谢", "注释"])
        config.setdefault("max_context_len", 200)
        config.setdefault("main_on_top", False)         # 置顶
        # 窗口治理：几何记忆与各窗口置顶偏好
        config.setdefault("geometry", {})
        # 桌宠（P4）：皮肤与屏幕位置
        config.setdefault("skin", "default")
        config.setdefault("pet.position", None)

        # ---- 已废弃键的一次性清理 ----
        config.pop("proof_cache_lookup_count", None)    # SQLite 缓存无需查询条数
        config.pop("follow_mouse", None)                # 桌宠改为固定位置可拖拽
        config.pop("suggestion_bg_color", None)         # 配色已由主题令牌接管
        config.pop("suggestion_font_color", None)
        return config

    @staticmethod
    def _backup_corrupt(reason: Exception) -> str:
        """把损坏的配置文件改名留档；失败也要留日志，绝不静默丢弃现场。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.corrupt-{stamp}")
        try:
            CONFIG_FILE.replace(target)
            logger.error("配置文件损坏（%s），已备份到 %s 并回退默认值", reason, target)
            return str(target)
        except OSError:
            logger.exception("配置文件损坏（%s）且备份失败: %s", reason, CONFIG_FILE)
            return ""

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save()

    def update(self, values: dict, api_key: str | None = None) -> bool:
        """批量更新并**只落盘一次**；返回是否写盘成功（原因见 ``last_save_error``）。

        设置页一次保存会改十几项：逐项 ``set()`` 等于把配置文件重写十几次，
        中途失败就留下"一半新一半旧"的配置。
        """
        payload = dict(values)
        if api_key is not None:
            payload["api_key"] = (
                "" if not api_key else self._cipher.encrypt(api_key.encode()).decode()
            )
        self.config.update(payload)
        return self.save()

    def save(self) -> bool:
        """原子写配置；失败不破坏旧文件，返回是否成功（原因见 last_save_error）。"""
        fd, tmp_name = tempfile.mkstemp(dir=str(CONFIG_DIR), prefix=CONFIG_FILE.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, CONFIG_FILE)
            self.last_save_error = ""
            return True
        except Exception as exc:  # noqa: BLE001  磁盘满/被杀软锁/权限都可能失败
            self.last_save_error = str(exc)
            logger.exception("配置写入失败: %s", CONFIG_FILE)
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            return False

    def get_api_key(self) -> str:
        """读取 API Key（密文自动解密）。

        只有「明显不是 Fernet 密文」的值才按历史明文迁移（一次性加密回写）；
        密文解不开（典型是 ``.key`` 被删或换了机器）时返回空串并报错，
        **绝不回写**，否则会把用户唯一的 key 密文永久写坏。
        """
        value = self.config.get("api_key", "")
        if not value:
            return ""
        try:
            return self._cipher.decrypt(value.encode()).decode()
        except Exception as exc:  # noqa: BLE001  Fernet 会抛 InvalidToken/ValueError 等
            if value.startswith("gAAAAA"):
                logger.error("api_key 解密失败（疑似 config/.key 变更），已保留原密文不覆盖: %s", exc)
                return ""
            logger.warning("检测到历史明文 api_key，正在加密迁移")
            try:
                self.set_api_key(value)
            except Exception:
                logger.exception("api_key 明文迁移失败，本次仍按明文使用")
            return value

    def set_api_key(self, plain: str):
        """加密存储 API Key；传入空串则清空。"""
        if not plain:
            self.config["api_key"] = ""
        else:
            self.config["api_key"] = self._cipher.encrypt(plain.encode()).decode()
        self.save()
