# plugins/reference/libraries.py
"""文献仓库（子库）注册表：每库一个目录，记录当前使用哪个库。

结构（`config/references/` 下）：

    libraries.json                     # {"active": 名字, "libraries": {名字: {...}}}
    libraries/<库名>/references.csv    # 该库的文献与子库备注
    libraries/<库名>/anchors.csv
    libraries/<库名>/notes.csv         # 该库的总库备注（多条）
    libraries/<库名>/index.db

历史数据（直接放在 `config/references/` 根下的四个文件）在首次启动时**移动**进默认仓库，
不做拷贝、不删内容 —— 用户资产只有一份。
"""
from __future__ import annotations

from core.services.i18n import message

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT_FILES = ("references.csv", "anchors.csv", "notes.csv", "index.db",
              "index.db-wal", "index.db-shm", "doc_scan.db")
DEFAULT_NAME = "默认库"          # 磁盘上的兜底名（历史数据都叫这个，保持兼容）


def default_library_name() -> str:
    """新建默认库时用的名字：跟随界面语言；语言包没就绪时退回 `DEFAULT_NAME`。"""
    from core.services.i18n import message

    name = message("library.default_name")
    return name if name and not name.startswith("library.") else DEFAULT_NAME


@dataclass
class Library:
    name: str
    path: Path
    last_used: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """只存**目录名**（相对 libraries_dir）：换根目录/复制数据后仍然正确。"""
        return {"path": self.path.name, "last_used": self.last_used}



def _backup_path(path: Path) -> Path:
    """给已存在的文件找一个不冲突的备份名（同目录、带时间戳）。"""
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.{stamp}.bak")
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.{stamp}-{index}.bak")
        index += 1
    return candidate

class LibraryRegistry:
    """仓库目录 + 当前激活仓库（用户可手动改绑）。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root is not None else None

    # ---------------- 路径 ----------------
    @property
    def root(self) -> Path:
        if self._root is None:
            from core.services import config_manager

            self._root = Path(config_manager.CONFIG_DIR) / "references"
        return self._root

    @property
    def registry_path(self) -> Path:
        return self.root / "libraries.json"

    @property
    def libraries_dir(self) -> Path:
        return self.root / "libraries"

    def _library_dir(self, name: str, entry: dict | None = None) -> Path:
        """库目录：兼容历史数据（存的是绝对路径时只取末级目录名）。"""
        stored = (entry or {}).get("path") or name
        return self.libraries_dir / Path(str(stored)).name

    def library_dir(self, name: str, entry: dict | None = None) -> Path:
        """公开版：库目录（供资产声明等外部用途）。"""
        return self._library_dir(name, entry)

    @property
    def bindings_path(self) -> Path:
        return self.root / "doc_bindings.json"

    # ---------------- 读写 ----------------
    def _read(self) -> dict:
        if not self.registry_path.exists():
            return {}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as exc:                 # noqa: BLE001 - 坏文件按空处理
            logger.warning("读取仓库注册表失败：%s", exc)
            return {}

    def _write(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.registry_path)

    # ---------------- 仓库操作 ----------------
    def names(self) -> list[str]:
        libraries = self._read().get("libraries", {})
        return sorted(libraries)

    def active_name(self) -> str:
        return self._read().get("active") or ""

    def active_path(self) -> Path:
        name = self.active_name()
        if name:
            entry = self._read().get("libraries", {}).get(name)
            if entry:
                return self._library_dir(name, entry)
        return self.libraries_dir / DEFAULT_NAME

    def migrate_default_name(self) -> bool:
        """把历史遗留的「默认库」改成当前语言的名字（目录、注册表、文档绑定一起改）。

        只在"库里只有这一个库、且名字正好是旧的中文名"时动手，避免误改用户的库。
        """
        target = default_library_name()
        data = self._read()
        libraries = data.get("libraries") or {}
        if len(libraries) != 1 or DEFAULT_NAME not in libraries or target == DEFAULT_NAME:
            return False

        source_dir = self.libraries_dir / DEFAULT_NAME
        target_dir = self.libraries_dir / target
        try:
            if source_dir.exists() and not target_dir.exists():
                source_dir.rename(target_dir)
        except OSError:
            logger.warning("重命名默认库目录失败，保持原名", exc_info=True)
            return False

        entry = libraries.pop(DEFAULT_NAME)
        entry["path"] = target                     # 只存目录名
        libraries[target] = entry
        data["libraries"] = libraries
        if data.get("active") == DEFAULT_NAME:
            data["active"] = target

        bindings = data.get("doc_bindings") or {}
        for doc_path, library in list(bindings.items()):
            if library == DEFAULT_NAME:
                bindings[doc_path] = target
        if bindings:
            data["doc_bindings"] = bindings

        self._write(data)
        logger.info("默认库已重命名：%s → %s", DEFAULT_NAME, target)
        return True

    def ensure_default(self) -> str:
        """首次使用：建注册表，并把根目录下的历史数据**移动**进默认库。"""
        data = self._read()
        if data.get("libraries"):
            return data.get("active") or next(iter(data["libraries"]))

        name = default_library_name()
        target = self.libraries_dir / name
        target.mkdir(parents=True, exist_ok=True)
        for filename in ROOT_FILES:
            source = self.root / filename
            if not source.exists():
                continue
            destination = target / filename
            try:
                if destination.exists():
                    # S6：别 unlink —— 注册表损坏时旧数据可能正是用户唯一的库。
                    # 改成留一份带时间戳的备份，再搬历史文件过去。
                    backup = _backup_path(destination)
                    destination.replace(backup)
                    logger.warning("迁移前已备份已有文件：%s → %s", destination, backup)
                shutil.move(str(source), str(destination))
            except OSError as exc:               # noqa: BLE001
                logger.warning("迁移旧数据失败（%s）：%s", source, exc)
        data = {"active": name, "libraries": {name: Library(name, target).to_dict()}}
        self._write(data)
        logger.info("已创建默认文献仓库：%s", target)
        return name

    def create(self, name: str) -> Path:
        name = (name or "").strip()
        if not name:
            raise ValueError(message("library.error.empty_name"))
        # S7：库名会成为目录名，必须挡掉能跳出库根的输入
        if name in (".", "..") or any(sep in name for sep in ("/", "\\")) or ".." in name:
            raise ValueError(message("library.error.invalid_name", name=name))
        data = self._read()
        libraries = data.setdefault("libraries", {})
        if name in libraries:
            return self._library_dir(name, libraries[name])
        target = self.libraries_dir / name
        target.mkdir(parents=True, exist_ok=True)
        libraries[name] = Library(name, target).to_dict()
        data["active"] = name
        self._write(data)
        return target

    def delete(self, name: str) -> Path:
        """删除仓库：目录移到 `libraries/_trash/` 下（可回滚），并从注册表移除。

        最后一个仓库不允许删（否则应用没有可用库）；删的是当前库时自动切到剩下的第一个。
        """
        name = (name or "").strip()
        data = self._read()
        libraries = dict(data.get("libraries", {}))
        if name not in libraries:
            raise KeyError(name)
        if len(libraries) <= 1:
            raise ValueError(message("library.error.keep_one"))

        directory = self._library_dir(name, libraries[name])
        trash = self.libraries_dir / "_trash"
        trash.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target: Path | None = None
        moved = False
        if directory.exists():
            # Windows 上目录里若有进程仍打开的 SQLite 句柄，rename 会 PermissionError。
            # 调用方（plugin.delete_library）会先切库把句柄关掉；这里再重试几次，
            # 仍失败就退化为"逐个删文件"，保证至少能从注册表里删掉这个库。
            import gc
            import time

            target = trash / f"{name}-{stamp}"
            if target.exists():
                target = _backup_path(target)
            # 数据结构：回收区只留**用户数据**（references.csv / notes.csv）+ 清单，
            # index.db / doc_scan.db 是可重建缓存，不进回收区 —— 否则备份又大又会留下
            # SQLite 的 -wal/-shm 尾巴（实测就是这些东西卡住"清空回收站"）。
            try:
                target.mkdir(parents=True, exist_ok=True)
                for filename in ("references.csv", "notes.csv"):
                    source = directory / filename
                    if source.exists():
                        shutil.copy2(source, target / filename)
                (target / "manifest.json").write_text(
                    json.dumps({"library": name, "deleted_at": stamp,
                                "files": sorted(p.name for p in target.iterdir()
                                                if p.is_file())},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8")
                shutil.rmtree(directory)
                moved = True
            except OSError as exc:                 # noqa: BLE001 - 目录被占用时退回整体搬移
                logger.debug("按文件备份失败（%s），退回整目录搬移", exc)
            for attempt in range(3):
                if moved:
                    break
                try:
                    directory.rename(target)
                    moved = True
                    break
                except OSError:
                    gc.collect()
                    time.sleep(0.05 * (attempt + 1))
            if not moved:
                logger.warning("目录被占用，改为逐个删除文件：%s", directory)
                # R13：既承诺"进回收区可恢复"，就不能悄悄逐文件硬删。
                raise ValueError(f"目录被占用，暂时无法移入回收区：{directory}")

        libraries.pop(name)
        data["libraries"] = libraries
        if data.get("active") == name:
            data["active"] = next(iter(libraries))
        self._write(data)
        # 该库的文档绑定一起清掉，避免下次扫描切到不存在的库
        bindings = self.doc_bindings()
        if bindings:
            self._write_bindings({path: lib for path, lib in bindings.items() if lib != name})
        logger.info("已删除文献仓库：%s（移到 %s）", name, trash)
        return target if (moved and target is not None) else trash

    def doc_bindings(self) -> dict[str, str]:
        """文档路径 → 仓库名（独立文件；首次读取时从旧 libraries.json 迁移）。"""
        if self.bindings_path.exists():
            try:
                return dict(json.loads(self.bindings_path.read_text(encoding="utf-8")))
            except Exception as exc:               # noqa: BLE001 - 坏文件按空处理
                logger.warning("读取文档绑定失败：%s", exc)
                return {}
        legacy = dict(self._read().get("doc_bindings", {}) or {})
        if legacy:                                 # 老数据迁移一次
            self._write_bindings(legacy)
            data = self._read()
            if data.pop("doc_bindings", None) is not None:
                self._write(data)
        return legacy

    def _write_bindings(self, bindings: dict[str, str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.bindings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.bindings_path)

    def bind_document(self, document_path: str, library: str) -> None:
        if not document_path or not library:
            return
        bindings = self.doc_bindings()
        bindings[document_path] = library
        self._write_bindings(bindings)

    def set_active(self, name: str) -> Path:
        data = self._read()
        libraries = data.get("libraries", {})
        if name not in libraries:
            raise KeyError(f"未知仓库：{name}")
        libraries[name]["last_used"] = time.time()
        data["active"] = name
        self._write(data)
        return self._library_dir(name, libraries[name])
