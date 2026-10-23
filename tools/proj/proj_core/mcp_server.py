"""proj MCP server —— 用官方 `@deepseek-ai/dsh-mcp-client`（stdio）暴露 proj-core 能力。

设计口径（见 docs/RESEARCH-PROJECT-STANDARDS-2026-10-22.md）：
- core 保持纯 stdlib、与 DSH 解耦；DSH 侧只加一行 MCP client；
- 传输 = stdio，新行分隔 JSON（兼容 Content-Length 头）；
- 工具名与 CLI 子命令一一对应，便于"人能跑、模型也能跑"。

用法：
  python tools/proj/mcp_server.py              # stdio 服务（由 DSH 拉起）
  python tools/proj/mcp_server.py --list-tools # 打印工具清单（自检）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import adr, context, graph, packs, pushcheck, scanner, wiki

SERVER_INFO = {"name": "pydev-proj", "version": "0.4.0"}
PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "proj_scan",
        "description": "扫描项目布局/隐私/垃圾/体积，返回评分与 issues。何时用：接手项目、提交前体检。不适用：只想看单个文件。",
        "inputSchema": {
            "type": "object",
            "properties": {"root": {"type": "string", "description": "项目根，默认当前工作目录"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_push_check",
        "description": "提交/推送边界检查（隐私/密钥/垃圾/大文件/提交信息）。何时用：commit 前（mode=staged）或 push 前（mode=push）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["staged", "last", "push"], "description": "默认 staged"},
                "range": {"type": "string", "description": "mode=range 时的 A..B"},
                "message": {"type": "string", "description": "提交信息（缺省读 .git/COMMIT_EDITMSG）"},
                "root": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_wiki_search",
        "description": "在 docs/wiki 索引里按关键词定位项目文档。何时用：找架构/接口/规范文档。不适用：已知文件路径时直接读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 10"},
                "root": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_context",
        "description": "生成有界上下文包（任务卡 → HANDOFF → 项目档案 → Wiki 索引 → 架构）。何时用：会话开始或换任务时恢复上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "任务目录，如 work/tasks/001"},
                "max_tokens": {"type": "integer", "description": "默认 2500"},
                "root": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_graph",
        "description": "模块依赖图与循环检测；给 focus 时返回该文件的上下游。何时用：改公共模块前评估影响面。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "description": "相对路径，如 src/app.py"},
                "depth": {"type": "integer", "description": "默认 1"},
                "root": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_adr_index",
        "description": "校验并生成 docs/adr/INDEX.md（编号连续、字段齐全）。何时用：新增/修改 ADR 后。",
        "inputSchema": {
            "type": "object",
            "properties": {"dir": {"type": "string", "description": "默认 docs/adr"}, "root": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_pack_list",
        "description": "列出可用领域包与技能。何时用：给项目选装领域技能（安全/测试/前端/后端/Agent/技能/数据）。",
        "inputSchema": {
            "type": "object",
            "properties": {"packs_dir": {"type": "string"}, "root": {"type": "string"}},
            "additionalProperties": False,
        },
    },
]


def _root(args: dict[str, Any]) -> Path:
    return Path(args.get("root") or Path.cwd()).resolve()


def call_tool(name: str, args: dict[str, Any]) -> Any:
    root = _root(args)
    if name == "proj_scan":
        return scanner.scan(root).to_dict()
    if name == "proj_push_check":
        return pushcheck.push_check(
            root, args.get("mode", "staged"), args.get("range"), args.get("message", "")
        ).to_dict()
    if name == "proj_wiki_search":
        return {"query": args["query"], "results": wiki.search(root, args["query"], int(args.get("limit", 10)))}
    if name == "proj_context":
        pack = context.build_context(root, args.get("task"), int(args.get("max_tokens", 2500)))
        return {"chars": pack.chars, "tokens": pack.tokens, "sections": pack.sections}
    if name == "proj_graph":
        g = graph.build_graph(root)
        data = g.to_dict()
        if args.get("focus"):
            data["focus"] = graph.focus(g, args["focus"], int(args.get("depth", 1)))
        return data
    if name == "proj_adr_index":
        return adr.build_index(root, args.get("dir", "docs/adr"))
    if name == "proj_pack_list":
        packs_dir = Path(args["packs_dir"]) if args.get("packs_dir") else Path(packs.DEFAULT_PACKS_DIR)
        return {"packs": [p.to_dict() for p in packs.list_packs(packs_dir)]}
    raise ValueError(f"未知工具：{name}")


def _text(data: Any, limit: int = 40_000) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + "\n…（已截断）"


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        try:
            data = call_tool(name, params.get("arguments") or {})
        except Exception as exc:  # noqa: BLE001 - MCP 需要把错误作为内容回传
            return {
                "jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": f"错误：{exc}"}], "isError": True},
            }
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": _text(data)}]}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def _iter_messages(stream) -> Any:
    while True:
        line = stream.readline()
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
            stream.readline()  # 空行
            body = stream.read(length)
            yield json.loads(body)
        else:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main(argv: list[str] | None = None) -> int:
    # MCP stdio 是 UTF-8 协议：zh-CN Windows 下 stdout 重定向默认 cp936，会让客户端解析失败
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
    args = argv if argv is not None else sys.argv[1:]
    if "--list-tools" in args:
        print(_text({"server": SERVER_INFO, "tools": [t["name"] for t in TOOLS]}))
        return 0
    out = sys.stdout
    for msg in _iter_messages(sys.stdin):
        resp = handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
