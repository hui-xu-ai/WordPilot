"""proj MCP server —— 用官方 `@deepseek-ai/dsh-mcp-client`（stdio）暴露 proj-core 能力。

设计口径：
- core 保持纯 stdlib、与 DSH 解耦；DSH 侧只加一行 MCP client；
- 传输 = stdio：新行分隔 JSON（主）+ Content-Length（按字节，兼容旧客户端）；
- 请求边界严格：非法消息/参数不退出，返回 JSON-RPC 错误；通知不回包；
- 慢工具（panel_*）并发执行，不阻塞后续请求；
- 结果始终是合法 JSON；超限改为结构化截断（truncated/total_chars/preview）。

用法：
  python tools/proj/mcp_server.py              # stdio 服务（由 DSH 拉起）
  python tools/proj/mcp_server.py --list-tools # 打印工具清单（自检）
"""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from . import adr, context, graph, packs, pushcheck, scanner, wiki

SERVER_INFO = {"name": "pydev-proj", "version": "0.4.0"}
PROTOCOL_VERSION = "2024-11-05"
MAX_TEXT = 40_000

TOOLS: list[dict[str, Any]] = [
    {
        "name": "proj_scan",
        "description": "扫描项目布局/隐私/体积/垃圾/忽略规则。何时用：开工前体检、提交前隐私检查。",
        "inputSchema": {
            "type": "object",
            "properties": {"root": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_push_check",
        "description": "检查待推送内容：隐私、秘密、体积、生成物。何时用：git push 或提交前。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "mode": {"type": "string", "enum": ["staged", "last", "range", "push"]},
                "range": {"type": "string"},
                "message": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_wiki_search",
        "description": "按关键词检索 docs/wiki 索引（文档定位，不读全文）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_context",
        "description": "构建有界任务上下文（HANDOFF/ADR/代码地图等片段），控制 token。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "task": {"type": "string"},
                "max_tokens": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_graph",
        "description": "文件级 import 依赖图（可 focus 某文件看上下游，可看循环）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "focus": {"type": "string"},
                "depth": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_adr_index",
        "description": "校验 docs/adr 并生成索引（编号连续/状态/日期/必备段落）。目录必须位于 root 内。",
        "inputSchema": {
            "type": "object",
            "properties": {"root": {"type": "string"}, "dir": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "panel_start",
        "description": "启动架构面板（本地 Web，127.0.0.1）。已在运行且同项目同版本时直接返回 URL。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "port": {"type": "integer"},
                "open": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "panel_stop",
        "description": "停止架构面板：PID 文件 + health 的 pid/root/port/token/service 五项一致才停，绝不按名杀。",
        "inputSchema": {
            "type": "object",
            "properties": {"root": {"type": "string"}, "port": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "panel_status",
        "description": "查询架构面板状态：是否运行、版本、pid、URL、root 是否匹配。",
        "inputSchema": {
            "type": "object",
            "properties": {"root": {"type": "string"}, "port": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "proj_pack_list",
        "description": "列出可用领域包与技能。",
        "inputSchema": {
            "type": "object",
            "properties": {"packs_dir": {"type": "string"}, "root": {"type": "string"}},
            "additionalProperties": False,
        },
    },
]

_TOOL_NAMES = {t["name"] for t in TOOLS}
_ENUMS = {"proj_push_check": {"mode": {"staged", "last", "range", "push"}}}


def _root(args: dict[str, Any]) -> Path:
    return Path(args.get("root") or Path.cwd()).resolve()


def _panel_cmd(action: str, args: dict[str, Any]) -> dict:
    proj = Path(__file__).resolve().parents[1] / "proj.py"
    port = int(args.get("port") or 8787)
    cmd = [sys.executable, str(proj), "panel", action, str(_root(args)), "--port", str(port)]
    if args.get("open"):
        cmd.append("--open")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=90, check=False)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"ok": proc.returncode == 0, "action": action, "port": port,
            "output": out[-4000:], "url": f"http://127.0.0.1:{port}/"}


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if name in {"panel_start", "panel_stop", "panel_status"}:
        return _panel_cmd(name.split("_", 1)[1], args)
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


def _schema(name: str) -> dict:
    for t in TOOLS:
        if t["name"] == name:
            return t["inputSchema"]
    return {}


def validate_args(name: str, args: Any) -> str | None:
    """按 inputSchema 做轻量校验（不引入第三方依赖）。"""
    if not isinstance(args, dict):
        return "arguments 必须是对象"
    schema = _schema(name)
    props: dict = schema.get("properties", {})
    extra = set(args) - set(props)
    if extra:
        return f"未知参数：{', '.join(sorted(extra))}"
    for key, value in args.items():
        typ = props[key].get("type")
        if typ == "string" and not isinstance(value, str):
            return f"{key} 必须是字符串"
        if typ == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"{key} 必须是整数"
        if typ == "boolean" and not isinstance(value, bool):
            return f"{key} 必须是布尔"
    for key, allowed in _ENUMS.get(name, {}).items():
        if key in args and args[key] not in allowed:
            return f"{key} 必须是 {sorted(allowed)} 之一"
    if "query" in schema.get("required", []) and not args.get("query"):
        return "query 不能为空"
    return None


def _text(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) <= MAX_TEXT:
        return text
    return json.dumps({"truncated": True, "total_chars": len(text),
                       "preview": text[:2000]}, ensure_ascii=False, indent=2)


def _err(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg: Any) -> dict | None:
    if not isinstance(msg, dict):
        return _err(None, -32600, "invalid request: message must be an object")
    if "id" not in msg:
        return None  # 通知：一律不回包
    mid = msg.get("id")
    method = msg.get("method")
    if not isinstance(method, str):
        return _err(mid, -32600, "invalid request: method must be a string")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"protocolVersion": PROTOCOL_VERSION,
                           "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params")
        if not isinstance(params, dict):
            return _err(mid, -32602, "params 必须为对象")
        name = params.get("name")
        if not isinstance(name, str) or name not in _TOOL_NAMES:
            return _err(mid, -32602, f"未知工具：{name}")
        args = params.get("arguments") or {}
        verr = validate_args(name, args)
        if verr:
            return _err(mid, -32602, verr)
        try:
            data = call_tool(name, args)
        except Exception as exc:  # noqa: BLE001 - 工具异常按 isError 回传
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": f"错误：{exc}"}], "isError": True}}
        is_error = bool(name.startswith("panel_") and isinstance(data, dict) and data.get("ok") is False)
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": _text(data)}], "isError": is_error}}
    return _err(mid, -32601, f"method not found: {method}")


def _iter_messages(buf) -> Any:
    while True:
        line = buf.readline()
        if not line:
            return
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(b"content-length:"):
            # 已读掉第一行头，重新构造读取（把首行长度解析进来）
            try:
                length = int(stripped.split(b":", 1)[1].strip())
            except ValueError:
                yield None, "invalid Content-Length"
                continue
            while True:
                extra = buf.readline()
                if not extra or not extra.strip():
                    break
            body = buf.read(length)
            yield (body, None) if len(body) == length else (None, "short body")
            continue
        yield stripped, None


def main(argv: list[str] | None = None) -> int:
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
    write_lock = threading.Lock()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def emit(resp: dict | None) -> None:
        if not resp:
            return
        with write_lock:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()

    def run(message: Any) -> None:
        try:
            emit(handle(message))
        except Exception as exc:  # noqa: BLE001 - 保证连接不因单条请求死亡
            mid = message.get("id") if isinstance(message, dict) else None
            emit(_err(mid, -32603, f"internal error: {exc}"))

    for raw, perr in _iter_messages(sys.stdin.buffer):
        if perr:
            emit(_err(None, -32700, f"parse error: {perr}"))
            continue
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            emit(_err(None, -32700, f"parse error: {exc}"))
            continue
        pool.submit(run, message)
    pool.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
