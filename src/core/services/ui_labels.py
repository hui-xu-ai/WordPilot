# core/services/ui_labels.py
"""界面显示层文案映射：**前端显示** 与 **后端数据** 解耦。

为什么需要：有些值来自后端数据（库名、皮肤 id、任务 key、版本 id），
它们必须保持语言无关（否则切语言就乱），但界面要用当前语言显示。
这里集中维护"后端值 → i18n key"的映射，之后要改显示文案只动这一处。

约定：
- 只做**显示**，绝不修改后端值；
- 映射不到的值为未知数据（用户自建的库名、自定义版本名），原样显示。
"""
from __future__ import annotations

from typing import Callable

#: 库名：历史遗留的默认库名 → i18n key（新建时已是当前语言，这里兼容旧数据）
LIBRARY_ALIASES = {
    "默认库": "library.default_name",
    "Default library": "library.default_name",
}

#: API 监控的任务名（日志里存 key）
TASK_KEYS = {
    "proof": "task.proof",
    "correct": "task.correct",
    "translate": "task.translate",
    "rewrite": "task.rewrite",
}
TASK_LEGACY_ZH = {"逐句校对": "task.proof", "整段纠错": "task.correct",
                  "翻译": "task.translate", "改写": "task.rewrite"}


def _translate(key: str, tr: Callable[..., str] | None) -> str:
    """查词；查不到时返回空串（绝不把裸 key 显示给用户）。"""
    if tr is None:
        return ""
    try:
        text = tr(key)
    except Exception:                          # noqa: BLE001 - 显示层不允许抛错
        return ""
    if not text:
        return ""
    # 插件 tr 会加命名空间前缀（library.default_name → reference.library.default_name），
    # 查不到时原样返回 key —— 这种情况要当作"没有译文"。
    if text == key or text.endswith("." + key):
        return ""
    return text


def library_label(name: str, tr: Callable[..., str] | None = None) -> str:
    """库名显示文案：已知别名走语言包，其余（用户自建库）原样显示。"""
    key = LIBRARY_ALIASES.get((name or "").strip())
    if key:
        translated = _translate(key, tr)
        if translated:
            return translated
    return name or ""


def task_label(value: str, tr: Callable[..., str] | None = None) -> str:
    """任务显示文案：接受 `task.*` key 或旧日志里的中文任务名。"""
    text = (value or "").strip()
    key = text if text.startswith("task.") else TASK_LEGACY_ZH.get(text)
    if key:
        translated = _translate(key, tr)
        if translated:
            return translated
    return text


def skin_label(skin_id: str, fallback: str = "", tr: Callable[..., str] | None = None) -> str:
    """皮肤名：`skin.<id>` 优先，清单作者写的名字兜底。"""
    translated = _translate(f"skin.{skin_id}", tr)
    return translated or fallback or skin_id


def theme_label(theme_id: str, fallback: str = "", tr: Callable[..., str] | None = None) -> str:
    """主题名：`theme.<id>` 优先。"""
    translated = _translate(f"theme.{theme_id}", tr)
    return translated or fallback or theme_id


def version_label(name: str, tr: Callable[..., str] | None = None,
                  default_name: str = "默认") -> str:
    """提示词版本名：`默认` / `mine-N` / `auto-<stamp>`，也兼容历史中文名。"""
    import re

    text = (name or "").strip()
    if not text:
        return ""
    if text in (default_name, "默认"):
        return _translate("prompt.version.default", tr) or text
    match = re.match(r"^(?:mine|我的版本)\s*[- ]?\s*(\d+)$", text)
    if match:
        translated = _translate("prompt.version.mine", tr)
        return translated.replace("{index}", match.group(1)) if translated else text
    match = re.match(r"^(?:auto|自动)-(.+)$", text)
    if match:
        translated = _translate("prompt.version.auto", tr)
        return translated.replace("{stamp}", match.group(1)) if translated else text
    return text
