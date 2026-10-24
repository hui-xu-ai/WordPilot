# plugins/reference/reference_store.py
"""参考文献数据层：**集中 CSV 是唯一事实源**（用户可随时整份替换，程序不回写文献库）。

设计要点（对应 ADR-004）：
- 两个文件：`references.csv`（文献条目 + 备注）、`anchors.csv`（文档里"哪处引用 → 哪条文献"）；
- 编码 `utf-8-sig`：Excel 双击不乱码；写盘一律 tmp + `os.replace` 原子替换，失败不毁原文件；
- 每次写前留 `*.bak-<时间戳>`（保留最近 N 份），避免误改无法回退；
- 外部替换检测（mtime + size + 首行哈希）：用户用自己维护的 CSV 覆盖后自动重新载入，**绝不覆盖回去**；
- 列不敏感：缺列补默认、用户自加的列原样保留。
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)

REFS_FILENAME = "references.csv"
ANCHORS_FILENAME = "anchors.csv"
BACKUP_KEEP = 10
ENCODING = "utf-8-sig"

REF_COLUMNS = (
    "ref_key", "aliases", "provider", "provider_id", "cite_label",
    "title", "authors", "year", "container", "doi", "url", "type",
    "tags", "starred", "note", "updated_at",
)
ANCHOR_COLUMNS = ("doc_name", "cite_label", "anchor_key", "first_para", "updated_at")

_TRUE_VALUES = {"1", "true", "yes", "y", "是", "★"}


class ReferenceStoreError(RuntimeError):
    """CSV 读写失败（被 Excel 占用、只读、磁盘满等），调用方负责提示用户。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def split_list(value: str | None) -> list[str]:
    """`a;b; c` → ['a', 'b', 'c']（空项丢弃，保持顺序）。"""
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def join_list(values: Iterable[str] | None) -> str:
    return ";".join(v for v in (values or []) if v)


def parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


def normalize_key(raw: str | None) -> str:
    return (raw or "").strip()


@dataclass
class Reference:
    """一条文献 + 用户备注。`extra` 保存用户自加的列，写回时原样输出。"""

    ref_key: str
    provider: str = ""
    provider_id: str = ""
    cite_label: str = ""
    title: str = ""
    authors: str = ""
    year: str = ""
    container: str = ""
    doi: str = ""
    url: str = ""
    type: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    starred: bool = False
    note: str = ""
    updated_at: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    # ---------- 序列化 ----------
    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Reference":
        known = set(REF_COLUMNS)
        return cls(
            ref_key=normalize_key(row.get("ref_key")),
            provider=(row.get("provider") or "").strip(),
            provider_id=(row.get("provider_id") or "").strip(),
            cite_label=(row.get("cite_label") or "").strip(),
            title=(row.get("title") or "").strip(),
            authors=(row.get("authors") or "").strip(),
            year=(row.get("year") or "").strip(),
            container=(row.get("container") or "").strip(),
            doi=(row.get("doi") or "").strip(),
            url=(row.get("url") or "").strip(),
            type=(row.get("type") or "").strip(),
            aliases=split_list(row.get("aliases")),
            tags=split_list(row.get("tags")),
            starred=parse_bool(row.get("starred")),
            note=row.get("note") or "",
            updated_at=(row.get("updated_at") or "").strip(),
            extra={k: (v or "") for k, v in row.items() if k and k not in known},
        )

    def to_row(self) -> dict[str, str]:
        row = {
            "ref_key": self.ref_key,
            "aliases": join_list(self.aliases),
            "provider": self.provider,
            "provider_id": self.provider_id,
            "cite_label": self.cite_label,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "container": self.container,
            "doi": self.doi,
            "url": self.url,
            "type": self.type,
            "tags": join_list(self.tags),
            "starred": "1" if self.starred else "0",
            "note": self.note,
            "updated_at": self.updated_at or _now(),
        }
        row.update(self.extra)
        return row

    @property
    def keys(self) -> list[str]:
        """主键 + 全部别名（任一命中即视为同一条）。"""
        return [self.ref_key, *self.aliases]


@dataclass
class Anchor:
    """文档里的一处引用锚点 → 指向哪条文献。"""

    doc_name: str
    cite_label: str
    anchor_key: str
    first_para: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Anchor":
        return cls(
            doc_name=(row.get("doc_name") or "").strip(),
            cite_label=(row.get("cite_label") or "").strip(),
            anchor_key=normalize_key(row.get("anchor_key")),
            first_para=row.get("first_para") or "",
            updated_at=(row.get("updated_at") or "").strip(),
        )

    def to_row(self) -> dict[str, str]:
        return {
            "doc_name": self.doc_name,
            "cite_label": self.cite_label,
            "anchor_key": self.anchor_key,
            "first_para": self.first_para,
            "updated_at": self.updated_at or _now(),
        }


class ReferenceStore:
    """CSV 读写与内存缓存；外部改动按指纹自动重载。"""

    def __init__(self, directory: str | Path | None = None) -> None:
        self._dir = Path(directory) if directory is not None else None
        self._refs: dict[str, Reference] = {}
        self._alias_index: dict[str, str] = {}
        self._anchors: list[Anchor] = []
        self._refs_fp: tuple[int, int, str] | None = None
        self._anchors_fp: tuple[int, int, str] | None = None
        self.load(force=True)

    # ---------------- 路径 ----------------
    @property
    def directory(self) -> Path:
        if self._dir is None:
            from core.services import config_manager  # 延迟取，便于测试替换 CONFIG_DIR

            self._dir = Path(config_manager.CONFIG_DIR) / "references"
        return self._dir

    @property
    def refs_path(self) -> Path:
        return self.directory / REFS_FILENAME

    @property
    def anchors_path(self) -> Path:
        return self.directory / ANCHORS_FILENAME

    def ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    # ---------------- 指纹与读取 ----------------
    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int, str] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        try:
            with open(path, "rb") as handle:
                head = handle.read(4096)
        except OSError:
            head = b""
        return (stat.st_mtime_ns, stat.st_size, hashlib.sha1(head).hexdigest())

    @staticmethod
    def _read_rows(path: Path) -> list[dict[str, str]]:
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding=ENCODING, newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ReferenceStoreError(f"读取 {path.name} 失败：{exc}") from exc
        except (csv.Error, UnicodeDecodeError) as exc:
            logger.warning("%s 解析异常（按容错继续）：%s", path.name, exc)
            return []

    def load(self, force: bool = False) -> bool:
        """按需重载。返回是否真的读了盘。"""
        changed = False
        refs_fp = self._fingerprint(self.refs_path)
        if force or refs_fp != self._refs_fp:
            rows = self._read_rows(self.refs_path)
            self._refs = {}
            self._alias_index = {}
            for row in rows:
                ref = Reference.from_row(row)
                if not ref.ref_key:
                    continue
                self._refs[ref.ref_key] = ref
                for alias in ref.aliases:
                    self._alias_index.setdefault(normalize_key(alias), ref.ref_key)
            self._refs_fp = refs_fp
            changed = True

        anchors_fp = self._fingerprint(self.anchors_path)
        if force or anchors_fp != self._anchors_fp:
            self._anchors = [Anchor.from_row(row) for row in self._read_rows(self.anchors_path)
                             if (row.get("anchor_key") or "").strip()]
            self._anchors_fp = anchors_fp
            changed = True
        return changed

    def reload_if_changed(self) -> bool:
        return self.load(force=False)

    def source_signature(self) -> str:
        """两个 CSV 的指纹串（派生索引用它判断是否需要重建）。"""
        refs = self._refs_fp or self._fingerprint(self.refs_path)
        anchors = self._anchors_fp or self._fingerprint(self.anchors_path)
        return f"{refs}|{anchors}"

    # ---------------- 查询 ----------------
    def all(self) -> list[Reference]:
        return list(self._refs.values())

    def __len__(self) -> int:
        return len(self._refs)

    def __iter__(self) -> Iterator[Reference]:
        return iter(self._refs.values())

    def resolve_key(self, key: str | None) -> str | None:
        """ref_key 或别名 → 规范 ref_key。"""
        normalized = normalize_key(key)
        if not normalized:
            return None
        if normalized in self._refs:
            return normalized
        return self._alias_index.get(normalized)

    def get(self, key: str | None) -> Reference | None:
        resolved = self.resolve_key(key)
        return self._refs.get(resolved) if resolved else None

    def find_by_label(self, cite_label: str) -> Reference | None:
        label = normalize_key(cite_label)
        if not label:
            return None
        for ref in self._refs.values():
            if ref.cite_label == label:
                return ref
        return None

    # ---------------- 写入 ----------------
    def upsert(self, ref: Reference, *, overwrite_note: bool = False) -> Reference:
        """按 ref_key/别名合并写入；空字段不覆盖已有值。"""
        self.reload_if_changed()
        existing_key = self.resolve_key(ref.ref_key)
        if existing_key is None:
            for alias in ref.aliases:
                existing_key = self.resolve_key(alias)
                if existing_key:
                    break
        target = self._refs.get(existing_key) if existing_key else None

        if target is None:
            ref.updated_at = ref.updated_at or _now()
            self._refs[ref.ref_key] = ref
            for alias in ref.aliases:
                self._alias_index.setdefault(normalize_key(alias), ref.ref_key)
        else:
            for name in ("provider", "provider_id", "cite_label", "title", "authors",
                         "year", "container", "doi", "url", "type"):
                value = getattr(ref, name)
                if value:
                    setattr(target, name, value)
            target.aliases = join_list(list(dict.fromkeys([*target.aliases, *ref.aliases]))).split(";") \
                if (target.aliases or ref.aliases) else []
            target.tags = list(dict.fromkeys([*target.tags, *ref.tags]))
            target.starred = target.starred or ref.starred
            if overwrite_note and ref.note:
                target.note = ref.note
            target.extra.update(ref.extra)
            target.updated_at = _now()
            for alias in target.aliases:
                self._alias_index.setdefault(normalize_key(alias), target.ref_key)
            ref = target

        self._flush_refs()
        return ref

    def set_note(self, key: str, *, note: str | None = None,
                 tags: list[str] | None = None, starred: bool | None = None) -> Reference | None:
        self.reload_if_changed()
        ref = self.get(key)
        if ref is None:
            return None
        if note is not None:
            ref.note = note
        if tags is not None:
            ref.tags = list(tags)
        if starred is not None:
            ref.starred = bool(starred)
        ref.updated_at = _now()
        self._flush_refs()
        return ref

    def delete(self, key: str) -> bool:
        self.reload_if_changed()
        resolved = self.resolve_key(key)
        if resolved is None:
            return False
        self._refs.pop(resolved, None)
        self._alias_index = {alias: owner for alias, owner in self._alias_index.items()
                             if owner != resolved}
        self._flush_refs()
        return True

    def replace_from_file(self, source: str | Path) -> None:
        """用户整份替换主表（"导入自己的 CSV"）：先备份现有文件，再原样替换并重载。

        原样替换（而不是解析后重写）是为了保留用户自加的列、列顺序与格式。
        """
        source_path = Path(source)
        if not source_path.exists():
            raise ReferenceStoreError(f"文件不存在：{source_path}")
        self.ensure_directory()
        self._backup(self.refs_path)
        try:
            data = source_path.read_bytes()
        except OSError as exc:
            raise ReferenceStoreError(f"读取 {source_path.name} 失败：{exc}") from exc
        try:
            with open(self.refs_path, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            raise ReferenceStoreError(f"写入 {self.refs_path.name} 失败：{exc}") from exc
        self.load(force=True)

    def export_to_file(self, target: str | Path) -> None:
        """把当前主表复制到用户指定位置（导出备份/分享）。"""
        target_path = Path(target)
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(self.refs_path.read_bytes() if self.refs_path.exists() else b"")
        except OSError as exc:
            raise ReferenceStoreError(f"导出失败：{exc}") from exc

    def orphan_anchors(self) -> list[Anchor]:
        """锚点指向的文献不在主表里（用户换了 CSV / 文献被删）。"""
        return [anchor for anchor in self._anchors if self.resolve_key(anchor.anchor_key) is None]

    # ---------------- 锚点 ----------------
    def anchors(self, doc_name: str | None = None) -> list[Anchor]:
        if doc_name is None:
            return list(self._anchors)
        return [anchor for anchor in self._anchors if anchor.doc_name == doc_name]

    def anchor_for(self, doc_name: str, cite_label: str) -> Anchor | None:
        for anchor in self._anchors:
            if anchor.doc_name == doc_name and anchor.cite_label == cite_label:
                return anchor
        return None

    def bind_anchor(self, doc_name: str, cite_label: str, anchor_key: str,
                    first_para: str = "") -> Anchor:
        """同一 (文档, 编号) 只保留一条，重复绑定即更新。"""
        self.reload_if_changed()
        existing = None
        for anchor in self._anchors:
            if anchor.doc_name == doc_name and anchor.cite_label == cite_label:
                existing = anchor
                break
        if existing is None:
            existing = Anchor(doc_name=doc_name, cite_label=cite_label,
                              anchor_key=normalize_key(anchor_key), first_para=first_para)
            self._anchors.append(existing)
        else:
            existing.anchor_key = normalize_key(anchor_key)
            if first_para:
                existing.first_para = first_para
        existing.updated_at = _now()
        self._flush_anchors()
        return existing

    def unbind_anchor(self, doc_name: str, cite_label: str) -> bool:
        self.reload_if_changed()
        before = len(self._anchors)
        self._anchors = [anchor for anchor in self._anchors
                         if not (anchor.doc_name == doc_name and anchor.cite_label == cite_label)]
        if len(self._anchors) == before:
            return False
        self._flush_anchors()
        return True

    # ---------------- 落盘 ----------------
    def _flush_refs(self) -> None:
        self._write_csv(self.refs_path, REF_COLUMNS, [ref.to_row() for ref in self._refs.values()])
        self._refs_fp = self._fingerprint(self.refs_path)

    def _flush_anchors(self) -> None:
        self._write_csv(self.anchors_path, ANCHOR_COLUMNS,
                        [anchor.to_row() for anchor in self._anchors])
        self._anchors_fp = self._fingerprint(self.anchors_path)

    def _write_csv(self, path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        """原子写：先写临时文件再替换；落盘前留一份备份。"""
        self.ensure_directory()
        extra_columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns and key not in extra_columns:
                    extra_columns.append(key)
        fieldnames = list(columns) + extra_columns
        self._backup(path)
        try:
            handle_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
        except OSError as exc:
            raise ReferenceStoreError(f"创建临时文件失败：{exc}") from exc
        try:
            with os.fdopen(handle_fd, "w", encoding=ENCODING, newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore",
                                        restval="")
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp_name, path)
        except OSError as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise ReferenceStoreError(f"写入 {path.name} 失败（可能被 Excel 占用）：{exc}") from exc
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    def _backup(self, path: Path) -> None:
        if not path.exists():
            return
        # 带微秒：同一秒内连续保存也是不同的文件名，否则备份会互相覆盖、轮转失效
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = path.with_name(f"{path.name}.bak-{stamp}")
        try:
            with open(path, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())
        except OSError as exc:
            logger.warning("备份 %s 失败（继续写入）：%s", path.name, exc)
            return
        self._prune_backups(path)

    @staticmethod
    def _prune_backups(path: Path) -> None:
        pattern = f"{path.name}.bak-*"
        backups = sorted(path.parent.glob(pattern))
        for old in backups[:-BACKUP_KEEP]:
            with contextlib.suppress(OSError):
                old.unlink()
