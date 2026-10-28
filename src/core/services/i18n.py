# core/services/i18n.py
"""国际化服务：JSON 语言包 + tr() 查词 + 运行时热切换。

设计约定：
- 语言包是纯 JSON，新增一门语言 = 丢一个文件，无需编译步骤；
- key 采用「域.功能.元素」命名，插件词条自动加 <plugin_id>. 命名空间前缀，
  插件之间不会撞键；
- 回退链：指定语言 -> 基准语言(zh_CN) -> key 本身（并记录 warning）；
- tr(key) 不传参时不做格式化，避免把提示词里的 {text} 当成占位符误处理；
- 界面语言（ui_language）与提示词语言（prompt_language）相互独立：
  tr() 走界面语言，prompt_tr() 走提示词语言，二者互不影响；
- 服务本身零 Qt 依赖，切换语言后由 app 层拉起界面重翻译。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from infra.utils import resource_path

logger = logging.getLogger(__name__)

BASE_LANGUAGE = "zh_CN"
#: 语言代码 -> 语言自身的显示名（用于切换下拉框，始终以本地名呈现）
LANGUAGE_NAMES = {
    "zh_CN": "简体中文",
    "en_US": "English",
}


class I18nService:
    def __init__(self, config, i18n_dir: Path | None = None) -> None:
        self.config = config
        self.i18n_dir = Path(i18n_dir) if i18n_dir else Path(resource_path("assets/i18n"))
        self._packs: dict[str, dict[str, dict[str, str]]] = {}   # lang -> ns -> {key: text}
        self._namespaces: dict[str, Path] = {}                   # ns -> 语言包目录
        self._load_core()

    # ---------------- 语言 ----------------
    @property
    def current(self) -> str:
        """界面语言。"""
        return self.config.get("ui_language", BASE_LANGUAGE) if self.config else BASE_LANGUAGE

    @property
    def prompt_language(self) -> str:
        """提示词语言 == 界面语言（唯一标准）。

        用户 2026-10-28 明确：界面上所有文字（含内置默认提示词）只看界面语言。
        `prompt_language` 配置键已废弃——即使旧配置里写着 `zh_CN`，也不再影响任何文案；
        校对语言 / 翻译目标 / 纠错语言只用于判断待处理文本的语言，与显示无关。
        """
        return self.current

    def _active_langs(self) -> tuple[str, ...]:
        """需要常驻内存的语言：界面语言 + 提示词语言 + 基准语言（去重保序）。"""
        seen: dict[str, None] = {}
        for lang in (self.current, BASE_LANGUAGE):
            seen.setdefault(lang, None)
        return tuple(seen)

    def available(self) -> list[str]:
        """已安装的语言（以基准语言包存在为准，可扩展）。"""
        langs = set()
        for path in self.i18n_dir.glob("*.json"):
            langs.add(path.stem)
        for ns_dir in self._namespaces.values():
            for path in ns_dir.glob("*.json"):
                langs.add(path.stem)
        langs.add(BASE_LANGUAGE)
        return sorted(langs)

    def display_name(self, lang: str) -> str:
        return LANGUAGE_NAMES.get(lang, lang)

    def set_language(self, lang: str) -> str:
        if lang not in self.available():
            logger.warning("语言包不存在: %s，保持 %s", lang, self.current)
            return self.current
        if self.config is not None:
            self.config.set("ui_language", lang)
        self._load_core()
        return self.current

    # ---------------- 查词 ----------------
    def tr(self, key: str, /, **params) -> str:
        """按界面语言查词。"""
        return self._translate(self.current, key, **params)

    def prompt_tr(self, key: str, /, **params) -> str:
        """按提示词语言查词（发给 AI 的指令文案专用）。"""
        return self._translate(self.prompt_language, key, **params)

    def _translate(self, lang: str, message_key: str, **params) -> str:
        """查词并填充占位符。

        形参名不叫 `key`：语言包里存在 `{key}` 这类占位符，
        同名会与 `**params` 撞成 `TypeError: got multiple values for argument`。
        """
        text = self._lookup(lang, message_key)
        if params:
            try:
                return text.format(**params)
            except Exception:
                logger.debug("语言包占位符格式化失败: %s", message_key, exc_info=True)
        return text

    def _lookup(self, lang: str, key: str) -> str:
        for candidate in (lang, BASE_LANGUAGE):
            pack = self._packs.get(candidate)
            if not pack:
                continue
            hit = self._find(pack, key)
            if hit is not None:
                return hit
        logger.warning("缺少翻译: [%s] %s", lang, key)
        return key

    @staticmethod
    def _find(pack: dict[str, dict[str, str]], key: str) -> str | None:
        """先在完整 key 里找，再按命名空间逐级回退（app.menu.tools -> menu.tools）。"""
        for ns, entries in pack.items():
            if ns and key.startswith(ns + "."):
                value = entries.get(key[len(ns) + 1:])
                if value is not None:
                    return value
        root = pack.get("")
        if root and key in root:
            return root[key]
        return None

    # ---------------- 命名空间（插件语言包） ----------------
    def register_namespace(self, ns: str, directory: Path) -> None:
        """注册插件私有语言包目录；目录不存在则静默跳过。"""
        directory = Path(directory)
        if not directory.is_dir():
            return
        self._namespaces[ns] = directory
        for lang in self._packs:
            self._packs[lang].pop(ns, None)
        self._load_namespace(ns, directory)

    def unregister_namespace(self, ns: str) -> None:
        self._namespaces.pop(ns, None)
        for pack in self._packs.values():
            pack.pop(ns, None)

    # ---------------- 加载 ----------------
    def _load_core(self) -> None:
        """加载内核/外壳语言包，并重新加载已注册的插件命名空间。"""
        langs = self._active_langs()
        for lang in langs:
            pack: dict[str, dict[str, str]] = {"": {}}
            pack[""] = self._read(self.i18n_dir / f"{lang}.json")
            self._packs[lang] = pack
        for ns, directory in self._namespaces.items():
            self._load_namespace(ns, directory)

    def _load_namespace(self, ns: str, directory: Path) -> None:
        for lang in self._active_langs():
            entries = self._read(directory / f"{lang}.json")
            if entries:
                self._packs.setdefault(lang, {"": {}})[ns] = entries

    @staticmethod
    def _read(path: Path) -> dict[str, str]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            logger.exception("语言包解析失败: %s", path)
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str)}


# ---------------- 模块级消息（异常/状态文本，可被界面直接显示） ----------------
_message_service = None
#: 服务未就绪时的中文兜底（保证异常信息不会变成裸 key）
_FALLBACK = {}


def set_message_service(service) -> None:
    """由内核在启动时注入 i18n 服务，供无 Qt/无 ctx 的核心模块取文案。"""
    global _message_service
    _message_service = service


def register_fallback(entries: dict[str, str]) -> None:
    """登记中文兜底文案（一般直接用语言包内容，测试里可注入）。"""
    _FALLBACK.update(entries)


def message(key: str, **params) -> str:
    """取一条用户可见文案：i18n 服务 → 兜底表 → key 本身。"""
    templates = []
    if _message_service is not None:
        templates.append(_message_service.tr(key))
    if key in _FALLBACK:
        templates.append(_FALLBACK[key])
    for template in templates:
        if template and template != key:
            try:
                return template.format(**params)
            except Exception:                  # noqa: BLE001 - 占位符不匹配就原样返回
                return template
    return key
