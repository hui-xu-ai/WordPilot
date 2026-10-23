#!/usr/bin/env python3
"""兼容 shim：MCP server 实现已迁入 proj_core.mcp_server。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from proj_core.mcp_server import main

if __name__ == "__main__":
    raise SystemExit(main())
