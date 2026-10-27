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
    return _SLUG.sub("_", (name or "").strip()) or "未命名"


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
    """把即将被覆盖的当前文本留一份自动备份（`自动-yyyymmdd-hhmmss`）。"""
    if not (text or "").strip():
        return ""
    name = time.strftime("自动-%Y%m%d-%H%M%S")
    saved = save_version(key, name, text, note="自动备份", root=root)
    data = _read_index(_key_dir(key, root))
    autos = [item for item in data["versions"] if item["name"].startswith("自动-")]
    for item in autos[:-keep] if len(autos) > keep else []:
        try:
            (_key_dir(key, root) / item.get("file", "")).unlink(missing_ok=True)
        except OSError:
            pass
        data["versions"] = [x for x in data["versions"] if x["name"] != item["name"]]
    _write_index(_key_dir(key, root), data)
    return saved


_RAW_KEY = re.compile(r"^(\w+\.)?prompt\.[\w.]+$")


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
