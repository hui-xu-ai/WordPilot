# core/services/prompt_store.py
"""提示词版本仓库：保存多份自定义提示词、备份与切换。

用户要求（2026-10-27）：
- 三个（现在是四个）提示词的默认文案支持多语言，且**提示词语言**与**校对语言**解耦；
- 提示词可以「保存备份 + 切换」，方便用户维护自己的版本；
- 翻译改写页的「纠错」与智能纠错插件的提示词**分别保存、分别管理**。

存储结构（全部在 `config/prompts/` 下，按提示词键分目录）：
    prompts/
      proof_prompt/
        index.json          {"active": "我的版本", "versions": [{name, created, note, file}]}
        默认.md             原件文本（文件名由版本名清洗而来）
        我的版本.md
      correct_prompt/ …
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: 允许版本管理的提示词键（= config 里的键名）
PROMPT_KEYS = ("proof_prompt", "correct_prompt", "translate_prompt", "rewrite_prompt")

#: 自动备份最多保留几个（保存新内容时把旧内容留一份）
AUTO_KEEP = 10

_SLUG = re.compile(r"[^\w\u4e00-\u9fff.-]+")

#: 系统自带版本的名称（用户要求：叫「默认」，一眼看出是软件自带的）
DEFAULT_VERSION = "默认"

#: 每个提示词归哪个插件管 —— 资产按插件分文件夹存放，清理时不易混淆
PROMPT_OWNER = {
    "proof_prompt": "proofread",
    "correct_prompt": "clipboard",
    "translate_prompt": "clipboard",
    "rewrite_prompt": "clipboard",
}


def plugin_asset_dir(plugin_id: str, root: Path | None = None) -> Path:
    """某个插件的资产根目录：`config/plugins/<插件>/`（插件私有数据都在这里）。"""
    base = Path(root) if root is not None else Path(_config_dir())
    return base / "plugins" / plugin_id


def _config_dir() -> Path:
    from core.services.config_manager import CONFIG_DIR

    return Path(CONFIG_DIR)


def _store_dir(root: Path | None = None) -> Path:
    """兼容旧调用：所有提示词的公共根（实际按插件分目录存）。"""
    return (Path(root) if root is not None else _config_dir()) / "plugins"


def _key_dir(key: str, root: Path | None = None) -> Path:
    owner = PROMPT_OWNER.get(key, "core")
    return plugin_asset_dir(owner, root) / "prompts" / key


def _slug(name: str) -> str:
    from core.services.i18n import message

    return _SLUG.sub("_", (name or "").strip()) or message("prompt.version.untitled")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_index(directory: Path) -> dict:
    path = directory / "index.json"
    if not path.exists():
        return {"active": "", "versions": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                     # noqa: BLE001 - 坏文件不该拖垮界面
        logger.warning("提示词索引损坏（%s）：%s", path, exc)
        return {"active": "", "versions": []}
    data.setdefault("active", "")
    data.setdefault("versions", [])
    # 旧命名「当前」→ 系统自带的「默认」（用户要求：默认一眼看出是软件自带的）
    if data.get("active") == "当前":
        data["active"] = DEFAULT_VERSION
        for item in data["versions"]:
            if item.get("name") == "当前":
                item["name"] = DEFAULT_VERSION
        _write_index(directory, data)
    return data


def _write_index(directory: Path, data: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "index.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def migrate_legacy(root: Path | None = None) -> int:
    """把旧的 `config/prompts/<key>/` 整个搬到 `config/plugins/<插件>/prompts/<key>/`。"""
    import shutil

    base = Path(root) if root is not None else _config_dir()
    legacy_root = base / "prompts"
    if not legacy_root.exists():
        return 0
    moved = 0
    for directory in sorted(p for p in legacy_root.iterdir() if p.is_dir()):
        key = directory.name
        if key not in PROMPT_KEYS:
            continue
        target = _key_dir(key, root)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(directory), str(target))
            moved += 1
        except OSError as exc:               # noqa: BLE001
            logger.warning("迁移旧提示词目录失败（%s）：%s", directory, exc)
    try:
        if not any(legacy_root.iterdir()):
            legacy_root.rmdir()
    except OSError:
        pass
    if moved:
        logger.info("已把 %d 个旧提示词目录迁到 plugins/<插件>/prompts/", moved)
    return moved


def versions(key: str, root: Path | None = None) -> list[dict]:
    """列出某个提示词的所有版本（含 active 标记）。"""
    directory = _key_dir(key, root)
    data = _read_index(directory)
    active = data.get("active", "")
    items = []
    for item in data.get("versions", []):
        entry = dict(item)
        entry["active"] = entry.get("name") == active
        items.append(entry)
    return items


def active_name(key: str, root: Path | None = None) -> str:
    return _read_index(_key_dir(key, root)).get("active", "")


def read(key: str, name: str, root: Path | None = None) -> str:
    """读某个版本的文本；找不到返回空串。"""
    directory = _key_dir(key, root)
    data = _read_index(directory)
    for item in data.get("versions", []):
        if item.get("name") == name:
            path = directory / item.get("file", "")
            try:
                return path.read_text(encoding="utf-8")
            except OSError as exc:               # noqa: BLE001
                logger.warning("读取提示词版本失败（%s）：%s", path, exc)
                return ""
    return ""


def save_version(key: str, name: str, text: str, note: str = "",
                 root: Path | None = None, activate: bool = False) -> str:
    """保存一个版本（同名覆盖）；`activate=True` 时同时设为当前版本。返回版本名。"""
    if key not in PROMPT_KEYS:
        raise ValueError(f"不支持的提示词：{key}")
    directory = _key_dir(key, root)
    directory.mkdir(parents=True, exist_ok=True)
    data = _read_index(directory)
    name = (name or "").strip() or f"版本 {len(data.get('versions', [])) + 1}"
    filename = f"{_slug(name)}.md"

    existing = next((item for item in data["versions"] if item.get("name") == name), None)
    if existing is None:
        data["versions"].append({"name": name, "created": _now(), "note": note,
                                 "file": filename})
    else:
        existing["note"] = note or existing.get("note", "")
        existing["created"] = _now()
        existing["file"] = filename
    (directory / filename).write_text(text, encoding="utf-8")
    if activate or not data.get("active"):
        data["active"] = name
    _write_index(directory, data)
    logger.info("提示词版本已保存：%s / %s", key, name)
    return name


def activate(key: str, name: str, root: Path | None = None) -> str:
    """把某个版本设为当前；返回其文本（调用方负责写回 config）。"""
    directory = _key_dir(key, root)
    data = _read_index(directory)
    if not any(item.get("name") == name for item in data["versions"]):
        raise KeyError(name)
    data["active"] = name
    _write_index(directory, data)
    return read(key, name, root)


def delete_version(key: str, name: str, root: Path | None = None) -> bool:
    """删除一个版本（至少保留一个；删的是当前版本时自动切到最新的那个）。"""
    directory = _key_dir(key, root)
    data = _read_index(directory)
    if name == DEFAULT_VERSION:
        return False                          # 系统自带的「默认」不允许删除（防误删）
    target = next((item for item in data["versions"] if item.get("name") == name), None)
    if target is None or len(data["versions"]) <= 1:
        return False
    try:
        (directory / target.get("file", "")).unlink(missing_ok=True)
    except OSError as exc:                       # noqa: BLE001
        logger.debug("删除版本文件失败：%s", exc)
    data["versions"] = [item for item in data["versions"] if item.get("name") != name]
    if data.get("active") == name:
        data["active"] = data["versions"][-1]["name"]
    _write_index(directory, data)
    return True


def backup_active(key: str, text: str, root: Path | None = None,
                  keep: int = AUTO_KEEP) -> str:
    """把即将被覆盖的当前文本留一份自动备份（`auto-yyyymmdd-hhmmss`，语言无关 id）。"""
    if not (text or "").strip():
        return ""
    name = time.strftime("auto-%Y%m%d-%H%M%S")
    saved = save_version(key, name, text, note="自动备份", root=root)
    data = _read_index(_key_dir(key, root))
    autos = [item for item in data["versions"]
             if item["name"].startswith(("auto-", "自动-"))]
    for item in autos[:-keep] if len(autos) > keep else []:
        try:
            (_key_dir(key, root) / item.get("file", "")).unlink(missing_ok=True)
        except OSError:
            pass
        data["versions"] = [x for x in data["versions"] if x["name"] != item["name"]]
    _write_index(_key_dir(key, root), data)
    return saved


_RAW_KEY = re.compile(r"^(\w+\.)?prompt\.[\w.]+$")

#: 每个提示词对应的"系统自带默认文案"i18n key
DEFAULT_KEYS = {
    "proof_prompt": "prompt.proof.default",
    "correct_prompt": "prompt.correct.default",
    "translate_prompt": "prompt.translate.default",
    "rewrite_prompt": "prompt.rewrite.default",
}


def default_keys(key: str) -> str:
    return DEFAULT_KEYS.get(key, "")


def default_text(key: str, prompt_tr=None, tr=None) -> str:
    """系统自带的默认提示词正文（查不到、或查成原始 key 时返回空串）。"""
    i18n_key = default_keys(key)
    if not i18n_key:
        return ""
    text = ""
    if callable(prompt_tr):
        try:
            text = (prompt_tr(i18n_key) or "").strip()
        except Exception:                     # noqa: BLE001 - 翻译服务异常时退回插件 tr
            text = ""
    if not text and callable(tr):
        text = (tr(i18n_key) or "").strip()
    if not text or text == i18n_key or _RAW_KEY.fullmatch(text):
        return ""
    return text


def effective_text(key: str, config, prompt_tr=None) -> str:
    """界面**应该显示**的提示词正文 —— 与真正发给模型的保持一致。

    优先级：`config[键]`（用户保存过的）→ 语言包里的系统默认。
    编辑框必须按它初始化，否则 config 为空时框里空白（用户现场：
    「纠错提示词」「校对提示词」显示为空，其实系统本来就有默认文案）。
    """
    text = (config.get(key) or "").strip() if config is not None else ""
    if text:
        # config 里若是**某个语言的系统默认**（早期"保存"把默认文案写死了），
        # 视为未自定义 → 返回当前界面语言的默认，避免英文界面里出现中文提示词。
        if _normalised(text) in default_text_variants(key, prompt_tr):
            return default_text(key, prompt_tr)
        return text
    return default_text(key, prompt_tr)


#: 历史版本里内置过的默认提示词（**原样**保存进 config 的都不算用户自定义）
LEGACY_DEFAULTS = {
    "proof_prompt": (
        "你是中文科技论文校对专家。修正错别字、语病、标点、专业词汇拼写。校对所有单位的空格："
        "数字与℃、%、°之间不加空格（例：2℃、5%、9°）；数字与其他单位（如m）之间加半角空格"
        "（例：10 m）。\n原文：{text}",
        # 早期"保存"把默认文案存进 config 时不带 `原文：{text}` 尾巴
        "你是中文科技论文校对专家。修正错别字、语病、标点、专业词汇拼写。校对所有单位的空格："
        "数字与℃、%、°之间不加空格（例：2℃、5%、9°）；数字与其他单位（如m）之间加半角空格"
        "（例：10 m）。",
    ),
    "correct_prompt": (
        "你是中文科技论文校对专家。下面是一整段或整篇文本，请修正错别字、语病、标点、专业词汇拼写，"
        "保持原有段落与换行结构，不要改写句式、不要增删内容；只输出校对后的全文。\n{text}",
    ),
    "translate_prompt": (
        "将以下文本翻译为{target_lang}，只输出译文：\n{text}",
        "将以下文本翻译为{target_lang}，只输出译文：",
    ),
    "rewrite_prompt": (
        "按{style}风格改写以下文本，只输出改写后的内容：\n{text}",
        "按{style}风格改写以下文本，只输出改写后的内容：",
    ),
}


def _normalised(text: str) -> str:
    """比较用：去掉**全部**空白（保存提示词时空白/换行常被折叠或补删）。"""
    return "".join((text or "").split())


def migrate_stale_defaults(config, root: Path | None = None) -> int:
    """清掉 config 里"历史内置默认"的拷贝，让新的默认提示词生效。

    来由（用户现场）：早期插件保存时把**当时的默认文案**写进了 config
    （`MWindow_config.json` 里能看到 `按{style}风格改写…`），而 config 优先级高于语言包，
    于是软件更新了默认提示词，界面与请求里用的还是老文本。
    只清"与已知旧默认完全一致（忽略空白）"的值，用户自己改过的一律保留。
    """
    cleared = 0
    for key, legacy in LEGACY_DEFAULTS.items():
        value = _normalised(config.get(key) or "")
        if not value:
            continue
        if any(value == _normalised(item) for item in legacy):
            config.set(key, "")
            cleared += 1
            logger.info("已移除 %s 里的旧版内置默认提示词（将使用新版默认）", key)
    if cleared:
        try:
            config.save()
        except Exception as exc:              # noqa: BLE001 - 保存失败不阻断启动
            logger.warning("写回配置失败：%s", exc)
    return cleared


def default_text_variants(key: str, prompt_tr=None) -> set[str]:
    """收集该提示词在**所有语言包**里的默认文案（归一化后），用于识别空壳版本。"""
    i18n_key = default_keys(key)
    if not i18n_key:
        return set()
    variants: set[str] = set()
    current = _normalised(default_text(key, prompt_tr))
    if current:
        variants.add(current)
    try:
        from infra.utils import resource_path

        i18n_dir = Path(resource_path("assets/i18n"))
        for pack in sorted(i18n_dir.glob("*.json")):
            try:
                data = json.loads(pack.read_text(encoding="utf-8"))
            except Exception:                  # noqa: BLE001 - 单个语言包坏了不影响判定
                continue
            value = _normalised(str(data.get(i18n_key, "")))
            if value and not _RAW_KEY.fullmatch(value):
                variants.add(value)
    except Exception:                          # noqa: BLE001
        pass
    return variants


def drop_frozen_defaults(config, prompt_tr=None, root: Path | None = None) -> int:
    """把 config 里"等于某个语言默认文案"的提示词清空，让它重新跟随系统默认。

    为什么需要：早先「保存」会把当时的默认文案写进 config；用户后来切换语言，
    框里就一直显示旧语言（如英文界面里出现中文默认提示词），且以后软件升级默认词也不生效。
    """
    cleared = 0
    for key in PROMPT_KEYS:
        value = _normalised(config.get(key) or "")
        if not value:
            continue
        if value in default_text_variants(key, prompt_tr):
            config.set(key, "")
            cleared += 1
            logger.info("%s 里存的是系统默认提示词的旧语言副本，已清空", key)
    if cleared:
        try:
            config.save()
        except Exception as exc:               # noqa: BLE001
            logger.warning("写回配置失败：%s", exc)
    return cleared


def drop_redundant_versions(key: str, prompt_tr=None, root: Path | None = None) -> int:
    """删掉"内容与系统默认完全一样"的自定义版本（没有信息量，且是误建的）。

    只删非「默认」、非自动备份的版本；删除后若当前版本被删，切回「默认」。
    """
    variants = default_text_variants(key, prompt_tr)
    if not variants:
        return 0
    directory = _key_dir(key, root)
    if not directory.exists():
        return 0

    data = _read_index(directory)
    removed = 0
    for item in list(data.get("versions", [])):
        name = item.get("name", "")
        if name == DEFAULT_VERSION or name.startswith("自动-"):
            continue
        try:
            content = (directory / item.get("file", "")).read_text(encoding="utf-8")
        except OSError:
            continue
        if _normalised(content) not in variants:
            continue
        try:
            (directory / item.get("file", "")).unlink(missing_ok=True)
        except OSError:
            continue
        data["versions"] = [x for x in data["versions"] if x["name"] != name]
        if data.get("active") == name:
            data["active"] = DEFAULT_VERSION if DEFAULT_VERSION in [x["name"] for x in data["versions"]] else ""
        removed += 1
    if removed:
        _write_index(directory, data)
        logger.info("已清理 %s 下 %d 个与默认内容相同的多余版本", key, removed)
    return removed


def ensure_default_version(key: str, prompt_tr=None, root: Path | None = None) -> str:
    """保证「默认」版本存在，且内容 = **当前**系统自带文案（软件更新后自动跟进）。

    注意：只更新「默认」这一条，不改变当前选中的版本；用户自己的版本一律不动。
    """
    text = default_text(key, prompt_tr)
    if not text:
        return ""
    names = [item["name"] for item in versions(key, root)]
    if DEFAULT_VERSION in names:
        if (read(key, DEFAULT_VERSION, root) or "").strip() != text:
            save_version(key, DEFAULT_VERSION, text, note="系统自带", root=root)
        return DEFAULT_VERSION
    save_version(key, DEFAULT_VERSION, text, note="系统自带", root=root, activate=not names)
    return DEFAULT_VERSION


def repair_bogus_versions(root: Path | None = None) -> int:
    """删掉"内容是原始 i18n key"的坏版本。

    来由：早期实现用**插件**命名空间查默认文案（`proofread.prompt.proof.default` 查不到 →
    原样返回 key），于是把 key 当成提示词写进了版本（用户现场截图）。
    """
    removed = 0
    for key in PROMPT_KEYS:
        directory = _key_dir(key, root)
        if not directory.exists():
            continue
        data = _read_index(directory)
        changed = False
        for item in list(data.get("versions", [])):
            try:
                text = (directory / item.get("file", "")).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not _RAW_KEY.fullmatch(text):
                continue
            try:
                (directory / item.get("file", "")).unlink(missing_ok=True)
            except OSError:
                pass
            data["versions"] = [x for x in data["versions"] if x["name"] != item["name"]]
            if data.get("active") == item["name"]:
                data["active"] = data["versions"][-1]["name"] if data["versions"] else ""
            removed += 1
            changed = True
        if changed:
            _write_index(directory, data)
    if removed:
        logger.info("已清理 %d 个内容为原始 key 的坏提示词版本", removed)
    return removed


def seed_from_config(config, root: Path | None = None) -> int:
    """首次使用：把 config 里现有的提示词文本登记为「默认」版本。返回登记数量。"""
    added = 0
    for key in PROMPT_KEYS:
        text = (config.get(key) or "").strip() if config is not None else ""
        if not text:
            continue
        if versions(key, root):
            continue
        save_version(key, DEFAULT_VERSION, text, note="系统自带", root=root,
                     activate=True)
        added += 1
    return added
