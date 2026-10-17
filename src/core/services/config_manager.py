# config_manager.py
import json
import sys
from pathlib import Path

from cryptography.fernet import Fernet

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
    "suggestion_bg_color": "#eef3ff",
    "suggestion_font_color": "#000000",
    "suggestion_opacity": 0.95,
    "suggestion_font_size": 12,
    "show_floating_button": True,
    "auto_show_suggestion": True,
    # 校对缓存为 SQLite 主键查询，不再需要「查询条数」配置
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
        self.config = self._load_config()

    def _load_config(self):
        config = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except:
                pass
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
        
        config.setdefault("proof_prompt",
            "你是中文科技论文校对专家。修正错别字、语病、标点、专业词汇拼写。校对所有单位的空格：数字与℃、%、°之间不加空格（例：2℃、5%、9°）；数字与其他单位（如m）之间加半角空格（例：10 m）。")
#--修改6
        config.setdefault("translate_prompt",
            "将以下文本翻译为{target_lang}，只输出译文：\n{text}")
        config.setdefault("rewrite_prompt",
            "按{style}风格改写以下文本，只输出改写后的内容：\n{text}")
            
        config.setdefault("debounce_ms", 500)
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
        return config

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save()

    def save(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def get_api_key(self) -> str:
        """读取 API Key（密文自动解密）。

        兼容历史明文配置：解密失败时按明文处理，并立即加密回写，
        完成一次性的明文 → 密文迁移。
        """
        value = self.config.get("api_key", "")
        if not value:
            return ""
        try:
            return self._cipher.decrypt(value.encode()).decode()
        except Exception:
            # 历史遗留的明文 Key：原样返回，并加密回写磁盘
            plain = value
            try:
                self.set_api_key(plain)
            except Exception:
                pass  # 回写失败不影响本次使用
            return plain

    def set_api_key(self, plain: str):
        """加密存储 API Key；传入空串则清空。"""
        if not plain:
            self.config["api_key"] = ""
        else:
            self.config["api_key"] = self._cipher.encrypt(plain.encode()).decode()
        self.save()