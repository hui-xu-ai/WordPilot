"""proj panel —— 本地只读架构图谱（模块聚类 + 文件下钻 + 约束生成）。

设计参考：Codex project-architecture 的模块/违规模型；可视化借鉴文献计量图谱
（聚类着色、节点大小=入度、边宽=次数、标签避让、缩放分级）。
只绑 127.0.0.1、只读、无第三方依赖。
用法：python tools/proj/panel.py [root] [--port 8787] [--host 127.0.0.1]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import arch, packs, scanner

PANEL_VERSION = "1.5"
_STARTED = time.strftime("%Y-%m-%d %H:%M:%S")

from .panel_page import PAGE


def _commands(root: Path) -> dict:
    prof = root / ".dsh" / "project-profile.json"
    if not prof.is_file():
        return {}
    try:
        return json.loads(prof.read_text(encoding="utf-8")).get("commands", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _fingerprint(report: dict) -> str:
    blob = json.dumps([report.get("modules"), report.get("files"), report.get("edges"),
                       report.get("file_edges"), report.get("violations")], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def api_arch(root: Path) -> dict:
    data = arch.build_arch(root).to_dict()
    data["fingerprint"] = _fingerprint(data)
    data["panel_version"] = PANEL_VERSION
    return data


def api_health(root: Path) -> dict:
    data = api_arch(root)
    return {"panel_version": PANEL_VERSION, "pid": os.getpid(), "root": str(root),
            "started": _STARTED, "fingerprint": data["fingerprint"],
            "modules": len(data["modules"]), "files": len(data["files"])}


def api_impact(root: Path, rev: str = "HEAD") -> dict:
    try:
        proc = subprocess.run(["git", "-C", str(root), "diff", "--name-only", rev],
                              capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"git 调用失败：{exc}"}
    if proc.returncode != 0:
        return {"error": (proc.stderr or "git diff 失败").strip()[:300]}
    changed_all = [ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines() if ln.strip()]
    report = arch.build_arch(root)
    mod_of = {f["path"]: f["module"] for f in report.files}
    changed = [p for p in changed_all if p in mod_of]
    mods = sorted({mod_of[p] for p in changed if mod_of[p]})
    up: set[str] = set()
    down: set[str] = set()
    for e in report.edges:
        if e["to"] in mods:
            up.add(e["from"])
        if e["from"] in mods:
            down.add(e["to"])
    return {"rev": rev, "changed": changed, "modules": mods,
            "upstream": sorted(up - set(mods)), "downstream": sorted(down - set(mods))}


def _file_constraint(root: Path, report, path: str) -> dict:
    f = next((x for x in report.files if x["path"] == path), None)
    if f is None:
        return {"error": f"未知文件：{path}"}
    same = [x["path"] for x in report.files if x["module"] == f["module"] and x["path"] != path][:10]
    ins = sorted({a for a, b in report.file_edges if b == path})
    outs = sorted({b for a, b in report.file_edges if a == path})
    verify = _commands(root).get("test") or "python -m pytest -q"
    md = "\n".join([
        "# 任务约束（proj panel · 文件级）",
        "",
        f"## 允许改动\n- `{path}`",
        "",
        "## 不得改动",
        f"- 同模块其他文件（{len(same)} 个）：" + (", ".join(f"`{p}`" for p in same) if same else "（无）"),
        "- 其他模块的公共入口（模块边界见 `.dsh/architecture.toml`）",
        "",
        "## 上下游（供影响判断）",
        "### 上游（依赖它）",
        *([f"- `{p}`" for p in ins] or ["- （无）"]),
        "### 下游（它依赖）",
        *([f"- `{p}`" for p in outs] or ["- （无）"]),
        "",
        "## 必须回归",
        f"- `{verify}`",
        "- `python tools\\proj\\proj.py arch check .` 违规 0",
        "- `python tools\\proj\\proj.py scan .` 无新增 FAIL",
        "",
        "## 判定",
        "- 改动只出现在「允许改动」；越界即回退并说明理由。",
        "- 回归输出贴进 `work/verify/<run>/RESULT.md`。",
    ])
    return {"file": path, "module": f["module"], "markdown": md, "markdown_lines": len(md.splitlines())}


def api_constraint(root: Path, module: str = "", file: str = "") -> dict:
    report = arch.build_arch(root)
    if file:
        return _file_constraint(root, report, file)
    target = next((m for m in report.modules if m["id"] == module), None)
    if target is None:
        return {"error": f"未知模块：{module}"}
    others = [m["id"] for m in report.modules if m["id"] != module]
    verify = _commands(root).get("test") or "python -m pytest -q"
    md = "\n".join([
        "# 任务约束（proj panel · 模块级）",
        "",
        f"## 允许改动（模块 `{module}`）",
        *[f"- `{p}`" for p in target["paths"]],
        "",
        "## 不得改动（其他模块）",
        *[f"- `{o}`" for o in others],
        "",
        "## 架构边界（允许依赖）",
        *([f"- `{a}`" for a in target["allowed"]] or ["- （无；本模块不得依赖其他模块）"]),
        "",
        "## 必须回归",
        f"- `{verify}`",
        "- `python tools\\proj\\proj.py arch check .` 违规 0",
        "- `python tools\\proj\\proj.py scan .` 无新增 FAIL",
        "",
        "## 判定",
        "- 改动只出现在「允许改动」路径内；跨模块改动先改 `.dsh/architecture.toml` 并说明。",
        "- 回归输出贴进 `work/verify/<run>/RESULT.md`。",
    ])
    return {"module": module, "markdown": md, "markdown_lines": len(md.splitlines())}


def api_scan(root: Path) -> dict:
    return scanner.scan(root).to_dict()


def api_packs(root: Path) -> dict:
    return {"packs": [p.to_dict() for p in packs.list_packs(Path(packs.DEFAULT_PACKS_DIR))]}


def make_handler(root: Path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path in ("/", "/index.html"):
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            try:
                if parsed.path == "/api/arch":
                    data = api_arch(root)
                elif parsed.path == "/api/constraint":
                    data = api_constraint(root, (qs.get("module") or [""])[0], (qs.get("file") or [""])[0])
                elif parsed.path == "/api/health":
                    data = api_health(root)
                elif parsed.path == "/api/impact":
                    data = api_impact(root, (qs.get("rev") or ["HEAD"])[0])
                elif parsed.path == "/api/scan":
                    data = api_scan(root)
                elif parsed.path == "/api/packs":
                    data = api_packs(root)
                else:
                    self.send_error(404, "not found")
                    return
            except Exception as exc:  # noqa: BLE001
                self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                return
            self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/scope":
                self.send_error(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                kind = payload.get("kind", "")
                ident = payload.get("id", "")
                task = re.sub(r"[^A-Za-z0-9_.-]", "", str(payload.get("task") or "current")) or "current"
                if kind == "module":
                    md = api_constraint(root, module=ident).get("markdown", "")
                elif kind == "file":
                    md = api_constraint(root, file=ident).get("markdown", "")
                else:
                    raise ValueError("kind 必须是 module 或 file")
                if not md:
                    raise ValueError("未生成约束文本")
                path = root / "work" / "tasks" / task / "scope.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(md, encoding="utf-8")
                data = {"path": str(path.relative_to(root)).replace("\\", "/"),
                        "bytes": len(md.encode("utf-8"))}
            except Exception as exc:  # noqa: BLE001
                data = {"error": str(exc)}
            self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("[panel] " + fmt % args + "\n")

    return Handler


def serve(root: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(root.resolve()))
    print(f"=== proj panel: http://{host}:{port}/  (root={root.resolve()})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    host, port, root = "127.0.0.1", 8787, Path(".")
    rest: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        else:
            rest.append(args[i])
            i += 1
    if rest:
        root = Path(rest[0])
    serve(root, host, port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
