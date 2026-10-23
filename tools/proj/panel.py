#!/usr/bin/env python3
"""兼容 shim：面板实现已迁入 proj_core.panel。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from proj_core.panel import main

if __name__ == "__main__":
    raise SystemExit(main())
