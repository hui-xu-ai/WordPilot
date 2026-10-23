#!/usr/bin/env python3
r"""兼容 shim：`python tools\proj\proj.py …` 仍可用（实现已迁入 proj_core 包）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from proj_core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
