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
SOURCE_CHILD = "child"          # 子库已保存的内容（自行创建或采用后复制）
SOURCE_AVAILABLE = "available"  # 子库为空，总库有可选内容（**不代表已采用**）
SOURCE_NONE = "none"


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


def resolve_note(child_note: str, inherit_note_id: str,
                 master_notes: list[Note]) -> tuple[str, str, Note | None]:
    """算出某条文献**当前应显示的备注**：返回 (文本, 来源, 总库候选中被采用的那条)。

    规则（需求 C2）：
    - 子库已有内容 → 显示子库内容（来源 child）；
    - 子库为空但总库有内容 → **不自动填**，返回空文本 + available（界面只提示"有 N 条可选"）；
    - 都没有 → none。
    """
    if (child_note or "").strip():
        adopted = next((note for note in master_notes
                        if inherit_note_id and note.note_id == inherit_note_id), None)
        return child_note, SOURCE_CHILD, adopted

    active = [note for note in master_notes if not note.archived]
    if active:
        return "", SOURCE_AVAILABLE, None
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
    """界面门面：子库备注（1 条，存在文献行上）+ **全局**总库备注（多条）。"""

    def __init__(self, store, master_store: "NoteStore") -> None:
        self._store = store
        self._master = master_store

    # ---------------- 读 ----------------
    def master_notes(self, ref_key: str):
        """总库备注（跨子库共享）。"""
        return self._master.notes_for(ref_key)

    def available_count(self, ref_key: str) -> int:
        """总库里可选的备注条数（子库为空时界面提示"有 N 条可选"）。"""
        if self.child_note(ref_key).strip():
            return 0
        return len([note for note in self.master_notes(ref_key) if not note.archived])

    def inherit_id(self, ref_key: str) -> str:
        reference = self._store.get(ref_key)
        return (reference.inherit_note_id if reference else "") or ""

    def child_note(self, ref_key: str) -> str:
        reference = self._store.get(ref_key)
        return (reference.note if reference else "") or ""

    def effective(self, ref_key: str) -> tuple[str, str, "Note | None"]:
        """当前应显示的备注：(文本, 来源, 采用来源的总库备注)。"""
        return resolve_note(self.child_note(ref_key), self.inherit_id(ref_key),
                            self.master_notes(ref_key))

    def adopted_diff(self, ref_key: str) -> str:
        """子库采用的备注与总库当前内容不一致时，返回总库那份文本（供"查看差异"）。"""
        adopted_id = self.inherit_id(ref_key)
        if not adopted_id or not self.child_note(ref_key).strip():
            return ""
        current = next((note for note in self.master_notes(ref_key)
                        if note.note_id == adopted_id), None)
        if current is None:
            return ""
        return "" if current.text.strip() == self.child_note(ref_key).strip() else current.text

    # ---------------- 写 ----------------
    def save_child_note(self, ref_key: str, text: str) -> None:
        """子库备注（单条）：同一文献在本文多处引用共用。"""
        self._store.set_note(ref_key, note=text)

    def adopt_master_note(self, ref_key: str, note_id: str) -> None:
        """用户显式采用总库某条：把文本复制进子库，并记录来源 ID（供差异提示）。"""
        note = next((item for item in self.master_notes(ref_key)
                     if item.note_id == note_id), None)
        if note is None:
            return
        self._store.set_note(ref_key, note=note.text)
        reference = self._store.get(ref_key)
        if reference is not None:
            reference.inherit_note_id = note_id
            self._store.upsert(reference)

    def publish_to_master(self, ref_key: str, label: str = "") -> "Note | None":
        """用户点击"保存到总库"：把当前子库备注发布成一条总库备注。"""
        text = self.child_note(ref_key).strip()
        if not text:
            return None
        note = self._master.add(ref_key, text, label=label or "来自子库")
        reference = self._store.get(ref_key)
        if reference is not None:
            reference.inherit_note_id = note.note_id
            self._store.upsert(reference)
        return note

    def create_master_note(self, ref_key: str, text: str, label: str = "") -> "Note":
        return self._master.add(ref_key, text, label=label)

    def update_master_note(self, note_id: str, text: str | None = None,
                           label: str | None = None, pinned: bool | None = None):
        return self._master.update(note_id, text=text, label=label, pinned=pinned)

    def set_pinned(self, note_id: str, pinned: bool):
        return self._master.update(note_id, pinned=pinned)

    def archive_master_note(self, note_id: str) -> None:
        self._master.archive(note_id)


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
    def notes_all(self) -> list[Note]:
        """全部备注（用于"总库是否为空"的判断）。"""
        return list(self._notes.values())

    def reset(self) -> None:
        """清空内存并落盘为空表（文件本身已被备份/移走）。"""
        self._notes.clear()
        self._counter = 0
        self._flush()

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
