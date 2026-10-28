# tools/version.py
"""版本号工具：查看 / 校验 / 同步（发版前跑一次）。

用法：
    python tools/version.py                 # 打印当前版本
    python tools/version.py --check         # 校验各处版本号一致 + CHANGELOG 有对应条目
    python tools/version.py --check --tag   # 额外校验 git tag（CI/发版用）

单一来源：`src/__version__.py`。需要与它一致的地方：
1. `CHANGELOG.md` 里要有 `## [X.Y.Z]` 标题；
2. 若存在 git tag，最新 tag 应为 `vX.Y.Z`（`--tag` 时校验）。

只做只读检查，不改任何文件（改版本号请直接编辑 `src/__version__.py`）。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src/__version__.py"
CHANGELOG = ROOT / "CHANGELOG.md"


def read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("无法从 src/__version__.py 读取 __version__")
    return match.group(1)


def changelog_has(version: str) -> bool:
    if not CHANGELOG.exists():
        return False
    text = CHANGELOG.read_text(encoding="utf-8")
    return re.search(rf"^##\s*\[?{re.escape(version)}\]?", text, re.M) is not None


def latest_tag() -> str:
    try:
        result = subprocess.run(["git", "tag", "--list", "v*", "--sort=-v:refname"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    tags = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return tags[0] if tags else ""


def main(argv: list[str]) -> int:
    version = read_version()
    if "--check" not in argv:
        print(f"{version}")
        return 0

    problems: list[str] = []
    if not changelog_has(version):
        problems.append(f"CHANGELOG.md 缺少 `## [{version}]` 条目")
    if "--tag" in argv:
        tag = latest_tag()
        if tag and tag != f"v{version}":
            problems.append(f"最新 tag 是 {tag}，与 {version} 不一致（应为 v{version}）")

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1
    print(f"[OK] 版本 {version}：CHANGELOG"
          + ("、git tag" if "--tag" in argv else "") + " 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
