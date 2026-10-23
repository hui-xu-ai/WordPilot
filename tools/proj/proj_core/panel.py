"""proj panel —— 本地只读架构图谱（模块聚类 + 文件下钻 + 约束生成）。

设计参考：Codex project-architecture 的模块/违规模型；可视化借鉴文献计量图谱
（聚类着色、节点大小=入度、边宽=次数、标签避让、缩放分级）。
只绑 127.0.0.1、只读、无第三方依赖。
用法：python tools/proj/panel.py [root] [--port 8787] [--host 127.0.0.1]
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
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


PID_REL = ".dsh/panel.pid"


def _pid_path(root: Path) -> Path:
    return root.resolve() / PID_REL


def _port_in_use(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.35)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _health(port: int, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _write_pid(root: Path, port: int) -> Path:
    path = _pid_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(), "port": port, "root": str(root.resolve()),
                                "version": PANEL_VERSION, "started": _STARTED,
                                "token": os.environ.get("PANEL_TOKEN", "")},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    atexit.register(lambda: path.unlink(missing_ok=True))
    return path


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
    # 便宜端点：health 被 lifecycle 高频调用，不扫全图
    return {"service": "pydev-panel", "panel_version": PANEL_VERSION, "pid": os.getpid(),
            "root": str(root), "started": _STARTED,
            "token": os.environ.get("PANEL_TOKEN", "")}


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
    if os.environ.get("PANEL_WRITE_PID") == "1":
        _write_pid(root, port)
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


def lifecycle(argv: list[str]) -> int:
    """panel start|stop|status|restart 子命令。"""
    import argparse

    ap = argparse.ArgumentParser(prog="proj panel")
    ap.add_argument("action", choices=["start", "stop", "status", "restart"])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    url = f"http://127.0.0.1:{args.port}/"

    def do_stop() -> int:
        pf = _pid_path(root)
        if not pf.is_file():
            print("=== panel：无 PID 文件，拒绝停止（不猜进程）")
            return 2
        try:
            meta = json.loads(pf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("=== panel：PID 文件损坏，拒绝停止")
            return 2
        try:
            pid = int(meta.get("pid", 0))
        except (TypeError, ValueError):
            pid = 0
        h = _health(args.port)
        if not h:
            pf.unlink(missing_ok=True)
            print("=== panel：未在运行（已清理 PID 文件）")
            return 0
        try:
            live_pid = int(h.get("pid", 0))
        except (TypeError, ValueError):
            live_pid = 0
        checks = {
            "pid": pid > 0 and live_pid == pid,
            "root": str(h.get("root", "")).strip().lower() == str(root).strip().lower(),
            "port": int(meta.get("port", 0)) == args.port,
            "token": bool(meta.get("token")) and meta.get("token") == h.get("token"),
            "service": h.get("service") == "pydev-panel",
        }
        bad = [k for k, ok in checks.items() if not ok]
        if bad:
            print(f"=== panel：归属校验失败（{', '.join(bad)}），拒绝停止")
            return 2
        target = pid
        try:
            os.kill(target, signal.SIGTERM)
        except OSError as exc:
            print(f"=== panel：停止失败 {exc}")
            return 2
        for _ in range(30):
            if not _health(args.port, timeout=0.4):
                break
            time.sleep(0.1)
        pf.unlink(missing_ok=True)
        for _ in range(20):
            if not _port_in_use(args.port):
                break
            time.sleep(0.1)
        if _port_in_use(args.port):
            print(f"=== panel：已终止 pid={target}，但端口 {args.port} 仍被其他进程监听")
            print(f"  查看：netstat -ano | findstr :{args.port}")
            return 1
        print(f"=== panel：已停止 pid={target}")
        return 0

    if args.action == "stop":
        return do_stop()

    if args.action == "status":
        pf = _pid_path(root)
        h = _health(args.port)
        print(f"=== panel status（root={root}）")
        print(f"  pid 文件：{pf if pf.is_file() else '（无）'}")
        if h:
            print(f"  运行中：v{h.get('panel_version')} pid={h.get('pid')} 启动={h.get('started')}")
            print(f"  URL：{url}")
            if str(h.get("root", "")).strip().lower() != str(root).strip().lower():
                print(f"  [WARN] 该端口服务的是另一个项目：root={h.get('root')}")
                return 1
            if h.get("panel_version") != PANEL_VERSION:
                print(f"  [WARN] 版本不一致（代码 v{PANEL_VERSION}，进程 v{h.get('panel_version')}）⇒ restart")
                return 1
        else:
            print("  未运行")
        return 0

    if args.action == "restart":
        do_stop()

    lock_path = root / ".dsh" / "panel.lock"
    lock_fd = _acquire_lock(lock_path)
    try:
        return _start_impl(root, args, do_stop)
    finally:
        _release_lock(lock_fd, lock_path)


def _acquire_lock(path: Path) -> int | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(40):
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 30:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            time.sleep(0.15)
    return None


def _release_lock(fd: int | None, path: Path) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    path.unlink(missing_ok=True)


def _start_impl(root: Path, args, stop_fn) -> int:
    url = f"http://127.0.0.1:{args.port}/"
    # start
    h = _health(args.port)
    if h:
        if str(h.get("root", "")).strip().lower() != str(root).strip().lower():
            print(f"=== panel：端口 {args.port} 已被另一个项目占用（root={h.get('root')}），拒绝复用")
            return 2
        if h.get("panel_version") == PANEL_VERSION:
            print(f"=== panel 已在运行：{url}（v{PANEL_VERSION}）")
            if args.open:
                webbrowser.open(url)
            return 0
        if not args.force:
            print(f"=== panel 版本陈旧：进程 v{h.get('panel_version')} / 代码 v{PANEL_VERSION}")
            print("  加 --force 自动重启，或先 proj panel stop")
            return 1
        stop_fn()
    if _port_in_use(args.port):
        print(f"=== panel：端口 {args.port} 已被其他进程占用（不是本面板的 /api/health）")
        print(f"  查看占用：netstat -ano | findstr :{args.port}")
        print("  换个端口：proj panel start . --port 8788")
        return 2
    proj_dir = Path(__file__).resolve().parents[1]
    script = proj_dir / "proj.py"
    if script.is_file():
        cmd = [sys.executable, str(script), "panel", str(root), "--port", str(args.port)]
    else:  # 安装为包时的兜底
        code = ("import sys; from proj_core.panel import main; "
                f"sys.exit(main([r'{root}', '--port', '{args.port}']))")
        cmd = [sys.executable, "-c", code]
    env = dict(os.environ, PANEL_WRITE_PID="1", PANEL_TOKEN=secrets.token_hex(8))
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=str(root), env=env, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     start_new_session=sys.platform != "win32")
    for _ in range(60):
        if _health(args.port):
            break
        time.sleep(0.15)
    h = _health(args.port)
    if not h:
        print("=== panel：启动失败（端口被占或依赖缺失）")
        return 2
    print(f"=== panel 已启动：{url}（v{h.get('panel_version')} pid={h.get('pid')}）")
    if args.open:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
