# plugins/reference/notes.py
"""总库备注（多条）+ 子库单条备注的解析规则。

用户定稿的模型：
- **子库**：每条文献只有 1 条备注（同一文献在文中多处引用共用这一条）；
- **总库**：同一文献可以有多条备注（各自带短标签），供子库"挑选"；
- **跟随**：子库挑选的是总库那条的 **id**，不是复制文本 —— 总库更新后子库显示跟着变；
- 总库只有 1 条时**自动填充**，不让用户做无意义的挑选。
"""
from __future__ import annotations

import csv
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ENCODING = "utf-8-sig"
NOTE_COLUMNS = ("note_id", "ref_key", "label", "text", "pinned", "created", "updated", "archived")
BACKUP_KEEP = 5

#: 解析来源
SOURCE_CHILD = "child"        # 子库自己写的
SOURCE_INHERIT = "inherit"    # 跟随总库挑选的那条
SOURCE_AUTO = "auto"          # 总库只有一条，自动填充
SOURCE_NONE = "none"
SOURCE_DEFAULT = "default"      # 多条时默认取钉住/最近更新那条


@dataclass
class Note:
    """总库里的一条备注。"""

    note_id: str
    ref_key: str
    text: str = ""
    label: str = ""
    pinned: bool = False
    created: str = ""
    updated: str = ""
    archived: bool = False

    def to_row(self) -> dict[str, str]:
        return {
            "note_id": self.note_id, "ref_key": self.ref_key, "label": self.label,
            "text": self.text, "pinned": "1" if self.pinned else "0",
            "created": self.created, "updated": self.updated,
            "archived": "1" if self.archived else "0",
        }

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Note":
        return cls(
            note_id=(row.get("note_id") or "").strip(),
            ref_key=(row.get("ref_key") or "").strip(),
            text=row.get("text") or "",
            label=(row.get("label") or "").strip(),
            pinned=(row.get("pinned") or "0").strip() in {"1", "true", "True"},
            created=(row.get("created") or "").strip(),
            updated=(row.get("updated") or "").strip(),
            archived=(row.get("archived") or "0").strip() in {"1", "true", "True"},
        )


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _slug(label: str, index: int) -> str:
    """给没写标签的备注一个可读的兜底标签：备注 1 / 备注 2…"""
    label = (label or "").strip()
    return label or f"备注 {index}"


def resolve_note(child_note: str, inherit_note_id: str, master_notes: list[Note]) -> tuple[str, str, Note | None]:
    """算出某条文献**当前应显示的备注**：返回 (文本, 来源, 命中的总库备注)。

    优先级：子库自己写的内容 > 子库挑选的总库备注 > 总库唯一条目自动填充 > 空。
    """
    if (child_note or "").strip():
        return child_note, SOURCE_CHILD, None

    active = [note for note in master_notes if not note.archived]
    if inherit_note_id:
        for note in active:
            if note.note_id == inherit_note_id:
                return note.text, SOURCE_INHERIT, note

    if len(active) == 1:
        return active[0].text, SOURCE_AUTO, active[0]
    if active:
        # 多条且没挑：默认显示"钉住 / 最近更新"的那条，避免空白（用户反馈）
        default = pick_default_note(active)
        if default is not None:
            return default.text, SOURCE_DEFAULT, default
    return "", SOURCE_NONE, None


def pick_default_note(master_notes: list[Note]) -> Note | None:
    """多条备注里给一个首选：钉住的优先，其次最近更新。"""
    active = [note for note in master_notes if not note.archived]
    if not active:
        return None
    pinned = [note for note in active if note.pinned]
    pool = pinned or active
    return sorted(pool, key=lambda note: note.updated or note.created or "", reverse=True)[0]


class NoteService:
    """界面用的门面：子库备注（1 条）+ 总库多条 + 跟随挑选。"""

    def __init__(self, store, note_store: "NoteStore") -> None:
        self._store = store
        self._notes = note_store

    # ---------------- 读 ----------------
    def master_notes(self, ref_key: str):
        return self._notes.notes_for(ref_key)

    def inherit_id(self, ref_key: str) -> str:
        reference = self._store.get(ref_key)
        return (reference.inherit_note_id if reference else "") or ""

    def child_note(self, ref_key: str) -> str:
        reference = self._store.get(ref_key)
        return (reference.note if reference else "") or ""

    def effective(self, ref_key: str) -> tuple[str, str, "Note | None"]:
        """当前应显示的备注：(文本, 来源, 命中的总库备注)。"""
        return resolve_note(self.child_note(ref_key), self.inherit_id(ref_key),
                            self.master_notes(ref_key))

    # ---------------- 写 ----------------
    def save_child_note(self, ref_key: str, text: str) -> None:
        """子库备注（单条）：同一文献在文中多处引用共用。"""
        self._store.set_note(ref_key, note=text)

    def set_inherit(self, ref_key: str, note_id: str) -> None:
        reference = self._store.get(ref_key)
        if reference is None:
            return
        reference.inherit_note_id = note_id or ""
        self._store.upsert(reference)

    def create_master_note(self, ref_key: str, text: str, label: str = "") -> "Note":
        return self._notes.add(ref_key, text, label=label)

    def update_master_note(self, note_id: str, text: str | None = None,
                           label: str | None = None, pinned: bool | None = None):
        return self._notes.update(note_id, text=text, label=label, pinned=pinned)

    def set_pinned(self, note_id: str, pinned: bool):
        return self._notes.update(note_id, pinned=pinned)

    def archive_master_note(self, note_id: str) -> None:
        self._notes.archive(note_id)


class NoteStore:
    """总库备注的 CSV 存储（`config/references/notes.csv`）。"""

    def __init__(self, directory: str | Path | None = None) -> None:
        self._dir = Path(directory) if directory is not None else None
        self._notes: dict[str, Note] = {}
        self._counter = 0
        self.load()

    # ---------------- 路径 ----------------
    @property
    def directory(self) -> Path:
        if self._dir is None:
            from core.services import config_manager

            self._dir = Path(config_manager.CONFIG_DIR) / "references"
        return self._dir

    @property
    def path(self) -> Path:
        return self.directory / "notes.csv"

    # ---------------- 读写 ----------------
    def load(self) -> None:
        self._notes.clear()
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding=ENCODING, newline="") as handle:
                for row in csv.DictReader(handle):
                    note = Note.from_row(row)
                    if note.note_id and note.ref_key:
                        self._notes[note.note_id] = note
        except Exception as exc:                 # noqa: BLE001 - 坏文件不该拖垮插件
            logger.warning("读取总库备注失败：%s", exc)

    def _flush(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding=ENCODING, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(NOTE_COLUMNS))
            writer.writeheader()
            for note in self._notes.values():
                writer.writerow(note.to_row())
        os.replace(tmp, self.path)

    # ---------------- 查询 ----------------
    def notes_for(self, ref_key: str, include_archived: bool = False) -> list[Note]:
        notes = [note for note in self._notes.values() if note.ref_key == ref_key]
        if not include_archived:
            notes = [note for note in notes if not note.archived]
        return sorted(notes, key=lambda note: (note.pinned, note.updated), reverse=True)

    def get(self, note_id: str) -> Note | None:
        return self._notes.get(note_id)

    # ---------------- 写入 ----------------
    def add(self, ref_key: str, text: str, label: str = "", pinned: bool = False) -> Note:
        self._counter += 1
        stamp = _now()
        note = Note(
            note_id=f"n{int(time.time())}{self._counter:03d}",
            ref_key=ref_key, text=text,
            label=_slug(label, len(self.notes_for(ref_key)) + 1),
            pinned=pinned, created=stamp, updated=stamp,
        )
        self._notes[note.note_id] = note
        self._flush()
        return note

    def update(self, note_id: str, text: str | None = None, label: str | None = None,
               pinned: bool | None = None) -> Note | None:
        note = self._notes.get(note_id)
        if note is None:
            return None
        if text is not None:
            note.text = text
        if label is not None:
            note.label = label.strip()
        if pinned is not None:
            note.pinned = pinned
        note.updated = _now()
        self._flush()
        return note

    def archive(self, note_id: str) -> None:
        note = self._notes.get(note_id)
        if note is not None:
            note.archived = True
            note.updated = _now()
            self._flush()

    # ---------------- 迁移 ----------------
    def seed_from_reference_notes(self, pairs: list[tuple[str, str]]) -> int:
        """把旧版单条备注升成总库备注（每篇文献首次一条，标签「备注」）。"""
        created = 0
        for ref_key, text in pairs:
            if not (text or "").strip():
                continue
            if self.notes_for(ref_key):
                continue
            self.add(ref_key, text, label="备注")
            created += 1
        return created
