"""打包/冒烟时保护用户配置：备份 → 跑 → 还原。

背景（2026-10-28）：为了打包干净而 `Remove-Item dist\\WordPilot\\config` 把用户已经填好的
API Key 一起删掉了，用户打开后智能纠错直接 401。这个脚本让"冒烟"不再碰真实配置。

用法：
    python tools/smoke_packaged.py            # 备份 config → 启动 exe 10s → 按 PID 结束 → 还原
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _resolve_exe(argv: list[str]) -> Path:
    """产物路径：命令行 --exe 优先，其次 dist，最后 dist_new（旧 dist 被占用时的备用输出）。"""
    if "--exe" in argv:
        return Path(argv[argv.index("--exe") + 1]).resolve()
    for candidate in (ROOT / "dist/WordPilot/WordPilot.exe",
                      ROOT / "dist_new/WordPilot/WordPilot.exe"):
        if candidate.exists():
            return candidate
    return ROOT / "dist/WordPilot/WordPilot.exe"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    EXE = _resolve_exe(argv)          # noqa: N806 - 局部解析产物路径
    CONFIG = EXE.parent / "config"    # noqa: N806
    if not EXE.exists():
        print(f"[FAIL] 找不到 {EXE}，先跑 PyInstaller")
        return 1

    backup: Path | None = None
    if CONFIG.exists():
        backup = Path(tempfile.mkdtemp(prefix="wordpilot-config-")) / "config"
        shutil.copytree(CONFIG, backup)
        print(f"[OK] 已备份用户配置 → {backup}")

    if CONFIG.exists():
        shutil.rmtree(CONFIG, ignore_errors=True)

    process = subprocess.Popen([str(EXE)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    time.sleep(10)
    alive = process.poll() is None
    print(f"[{'OK' if alive else 'FAIL'}] 启动 10s：{'存活' if alive else '已退出'} (PID {process.pid})")
    if alive:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    time.sleep(1)

    # 冒烟产生的新 config 丢掉，把用户的还回去
    if CONFIG.exists():
        shutil.rmtree(CONFIG, ignore_errors=True)
    if backup is not None:
        if CONFIG.exists():
            shutil.rmtree(CONFIG, ignore_errors=True)
        shutil.copytree(backup, CONFIG)
        print(f"[OK] 已还原用户配置（含 API Key）← {backup}")
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
