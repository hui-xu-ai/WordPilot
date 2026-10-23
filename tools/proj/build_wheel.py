"""build_wheel.py —— 用**纯标准库**把 proj_core 打成 wheel（本机没装 setuptools/wheel）。

用法：python tools\\proj\\build_wheel.py [--out dist]
产物：dist/pydev_proj_core-<version>-py3-none-any.whl
安装：python -m pip install --no-deps --target <目录> <whl>   （或直接 pip install <whl>）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from proj_core import __version__

DIST_NAME = "pydev_proj_core"
PKG_NAME = "proj_core"


def _record_line(arcname: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{arcname},sha256={digest},{len(data)}"


def build(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    wheel = out_dir / f"{DIST_NAME}-{__version__}-py3-none-any.whl"
    dist_info = f"{DIST_NAME}-{__version__}.dist-info"
    files: list[tuple[str, bytes]] = []
    for py in sorted((HERE / PKG_NAME).glob("*.py")):
        files.append((f"{PKG_NAME}/{py.name}", py.read_bytes()))
    files.append((f"{dist_info}/WHEEL", (
        b"Wheel-Version: 1.0\n"
        b"Generator: proj-core-stdlib\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )))
    files.append((f"{dist_info}/METADATA", (
        "Metadata-Version: 2.1\n"
        "Name: pydev-proj-core\n"
        f"Version: {__version__}\n"
        "Summary: 项目布局/隐私/交付卫生/Wiki/依赖图/领域包（纯标准库）\n"
        "Requires-Python: >=3.11\n"
    ).encode()))
    files.append((f"{dist_info}/entry_points.txt", b"[console_scripts]\nproj = proj_core.cli:main\n"))
    record = [_record_line(name, data) for name, data in files]
    record.append(f"{dist_info}/RECORD,,")
    files.append((f"{dist_info}/RECORD", ("\n".join(record) + "\n").encode()))

    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    return wheel


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_wheel.py")
    ap.add_argument("--out", default="dist")
    args = ap.parse_args(argv)
    wheel = build(HERE / args.out)
    print(f"已生成：{wheel}（{wheel.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
