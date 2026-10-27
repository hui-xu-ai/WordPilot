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


def _store_dir() -> Path:
    from core.services.config_manager import CONFIG_DIR

    return Path(CONFIG_DIR) / "prompts"


def _key_dir(key: str, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else _store_dir()
    return base / key


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
    return data


def _write_index(directory: Path, data: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "index.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


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


def seed_from_config(config, root: Path | None = None) -> int:
    """首次使用：把 config 里现有的提示词文本登记为「当前」版本。返回登记数量。"""
    added = 0
    for key in PROMPT_KEYS:
        text = (config.get(key) or "").strip() if config is not None else ""
        if not text:
            continue
        if versions(key, root):
            continue
        save_version(key, "当前", text, note="首次登记", root=root, activate=True)
        added += 1
    return added
