#!/usr/bin/env python3
"""proj —— 项目布局、隐私、交付卫生与 Wiki 上下文工具（stdlib only）。

用法：
  python tools/proj/proj.py scan <root> [--json out.json]
  python tools/proj/proj.py init <root> [--apply]
  python tools/proj/proj.py push-check <root> [--mode staged|last|range] [--range A..B] [--message MSG] [--json out.json]
  python tools/proj/proj.py wiki index <root> [--out docs/wiki]
  python tools/proj/proj.py wiki search <root> <query> [--limit N] [--json]
  python tools/proj/proj.py context <root> [--task work/tasks/<id>] [--max-tokens 2500] [--json]

设计约束：
- 除 `init --apply` 与 `wiki index` 写索引外，全部只读。
- 不依赖 DSH / 第三方包；退出码：0=无硬问题，1=隐私/越界硬问题，2=用法错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .acceptance import check_report, init_report, print_accept
from .adr import build_index as build_adr_index
from .adr import print_adr
from .common import IGNORE_EXPECT
from .context import build_context, print_context
from .dups import find_duplicate_symbols, find_duplicates, impact, print_duplicates, print_impact, print_symbols
from .evaluate import print_eval, run_eval
from .evaluate import write_json as write_eval_json
from .graph import build_graph, focus, print_focus, print_graph
from .packs import DEFAULT_PACKS_DIR, get_pack, install_pack, list_packs, print_pack, print_packs, uninstall_pack
from .panel import serve as panel_serve
from .profile import detect, print_profile, write_profile
from .pushcheck import print_push_check, push_check
from .scanner import print_scan, scan
from .taskcheck import check_tasks, print_tasks
from .wiki import build_index, print_index, print_search, search


def _write_json(path: str | None, data: dict) -> None:
    if not path:
        return
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  json: {path}")


def cmd_init(root: Path, apply: bool) -> int:
    planned = ["repo", "local", "docs", "docs/architecture", "docs/adr", "docs/wiki", "work/tasks", ".dsh"]
    print(f"proj init: {root}")
    for d in planned:
        print(f"  mkdir {d}")
    print("  .gitignore 追加（若缺失）: " + ", ".join(IGNORE_EXPECT))
    if not apply:
        print("  （dry-run；加 --apply 才写入）")
        return 0
    for d in planned:
        (root / d).mkdir(parents=True, exist_ok=True)
    gi = root / ".gitignore"
    text = gi.read_text(encoding="utf-8", errors="ignore") if gi.exists() else ""
    missing = [x for x in IGNORE_EXPECT if x.rstrip("/") not in text.replace("\\", "/")]
    if missing:
        gi.write_text(text.rstrip() + "\n# proj init\n" + "\n".join(missing) + "\n", encoding="utf-8")
    print("  已写入")
    return 0


def cmd_scan(args) -> int:
    s = scan(Path(args.root), args.max_lines_warn, args.max_lines_fail)
    print_scan(s)
    _write_json(args.json_out, s.to_dict())
    return 1 if s.privacy["hardFail"] else 0


def cmd_push_check(args) -> int:
    pc = push_check(Path(args.root), args.mode, args.range, args.message or "")
    print_push_check(pc)
    _write_json(args.json_out, pc.to_dict())
    return 1 if pc.failed else 0


def cmd_wiki(args) -> int:
    root = Path(args.root)
    if args.wiki_cmd == "index":
        data = build_index(root, args.out)
        print_index(data)
        return 0
    results = search(root, args.query, args.limit)
    print_search(results, args.query)
    _write_json(args.json_out, {"query": args.query, "results": results})
    return 0 if results else 1


def cmd_context(args) -> int:
    pack = build_context(Path(args.root), args.task, args.max_tokens)
    print_context(pack)
    _write_json(args.json_out, pack.to_dict())
    return 0


def cmd_profile(args) -> int:
    root = Path(args.root)
    p = detect(root)
    print_profile(p)
    if args.write:
        out = write_profile(root, p)
        print(f"  已写入：{out}")
    _write_json(args.json_out, p.to_dict())
    return 0


def cmd_dup(args) -> int:
    root = Path(args.root)
    if args.symbols:
        data = find_duplicate_symbols(root)
        print_symbols(data)
        _write_json(args.json_out, {"symbols": data})
        return 0 if not data else 1
    clusters = find_duplicates(root, args.min_lines)
    print_duplicates(clusters)
    _write_json(args.json_out, {"clusters": [{"digest": c.digest, "lines": c.lines, "locations": c.locations} for c in clusters]})
    return 0 if not clusters else 1


def cmd_impact(args) -> int:
    data = impact(Path(args.root), args.target)
    print_impact(data)
    _write_json(args.json_out, data)
    return 0


def cmd_pack(args) -> int:
    packs_dir = Path(args.packs_dir or DEFAULT_PACKS_DIR)
    if args.pack_cmd == "list":
        packs = list_packs(packs_dir)
        print_packs(packs)
        _write_json(args.json_out, {"packs": [p.to_dict() for p in packs]})
        return 0
    if args.pack_cmd == "show":
        pack = get_pack(packs_dir, args.name)
        if not pack:
            print(f"pack 不存在：{args.name}", file=sys.stderr)
            return 2
        print_pack(pack)
        return 0
    if args.pack_cmd == "install":
        result = install_pack(packs_dir, args.name, Path(args.dest), args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("ok"):
            dest = Path(args.dest).resolve() / args.name / "skills"
            print("  注册片段（profile 补丁，按项目）：")
            print("    - id: skill-filesystem")
            print("      name: '@deepseek-ai/dsh-skill-filesystem'")
            print("      config:")
            print("        customSkillDirs:")
            print(f"          - {dest}")
            print("  注意：不注册 customSkillDirs 则不生效（第三轮 D6 实测：装好也加载不到）")
        _write_json(args.json_out, result)
        return 0 if result.get("ok") else 1
    result = uninstall_pack(Path(args.dest), args.name, args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def cmd_graph(args) -> int:
    graph = build_graph(Path(args.root))
    data = graph.to_dict()
    if args.focus:
        focused = focus(graph, args.focus, args.depth)
        print_focus(focused)
        data["focus"] = focused
    else:
        print_graph(graph)
    _write_json(args.json_out, data)
    if args.fail_on_cycle and graph.cycles:
        return 1
    return 0


def cmd_adr(args) -> int:
    data = build_adr_index(Path(args.root), args.dir, args.out, write=not args.no_write)
    print_adr(data)
    if not data.get("written", True):
        print("  （--no-write：只校验，未写 INDEX.md）")
    _write_json(args.json_out, data)
    return 0 if not data["problems"] else 1


def cmd_panel(args) -> int:
    panel_serve(Path(args.root), args.host, args.port)
    return 0


def cmd_eval(args) -> int:
    report = run_eval(Path(args.root), Path(args.preset) if args.preset else None)
    print_eval(report)
    if args.json_out:
        write_eval_json(args.json_out, report.to_dict())
    return 1 if report.failed else 0


def cmd_accept(args) -> int:
    if args.accept_cmd == "init":
        report = init_report(Path(args.root), args.run, args.force)
    else:
        report = check_report(Path(args.root), args.run)
    print_accept(report)
    if args.json_out:
        write_eval_json(args.json_out, report.to_dict())
    return 0 if not report.problems else 1


def cmd_task(args) -> int:
    report = check_tasks(Path(args.root))
    print_tasks(report)
    if args.json_out:
        write_eval_json(args.json_out, report.to_dict())
    return 0 if not report.problems else 1


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError) as exc:
        sys.stderr.write(f"proj: stdout reconfigure skipped: {exc}\n")

    ap = argparse.ArgumentParser(prog="proj")
    ap.add_argument("--version", action="version", version=f"proj-core {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("root", nargs="?", default=".")
    p_scan.add_argument("--json", dest="json_out")
    p_scan.add_argument("--max-lines-warn", type=int, default=400)
    p_scan.add_argument("--max-lines-fail", type=int, default=800)

    p_init = sub.add_parser("init")
    p_init.add_argument("root", nargs="?", default=".")
    p_init.add_argument("--apply", action="store_true")

    p_push = sub.add_parser("push-check")
    p_push.add_argument("root", nargs="?", default=".")
    p_push.add_argument("--mode", choices=["staged", "last", "range", "push"], default="staged")
    p_push.add_argument("--range", dest="range")
    p_push.add_argument("--message")
    p_push.add_argument("--json", dest="json_out")

    p_wiki = sub.add_parser("wiki")
    wiki_sub = p_wiki.add_subparsers(dest="wiki_cmd", required=True)
    p_windex = wiki_sub.add_parser("index")
    p_windex.add_argument("root", nargs="?", default=".")
    p_windex.add_argument("--out", default="docs/wiki")
    p_wsearch = wiki_sub.add_parser("search")
    p_wsearch.add_argument("root", nargs="?", default=".")
    p_wsearch.add_argument("query")
    p_wsearch.add_argument("--limit", type=int, default=10)
    p_wsearch.add_argument("--json", dest="json_out")

    p_ctx = sub.add_parser("context")
    p_ctx.add_argument("root", nargs="?", default=".")
    p_ctx.add_argument("--task")
    p_ctx.add_argument("--max-tokens", type=int, default=2500)
    p_ctx.add_argument("--json", dest="json_out")

    p_prof = sub.add_parser("profile")
    p_prof.add_argument("root", nargs="?", default=".")
    p_prof.add_argument("--write", action="store_true")
    p_prof.add_argument("--json", dest="json_out")

    p_dup = sub.add_parser("dup")
    p_dup.add_argument("root", nargs="?", default=".")
    p_dup.add_argument("--symbols", action="store_true")
    p_dup.add_argument("--min-lines", type=int, default=6)
    p_dup.add_argument("--json", dest="json_out")

    p_imp = sub.add_parser("impact")
    p_imp.add_argument("root", nargs="?", default=".")
    p_imp.add_argument("target")
    p_imp.add_argument("--json", dest="json_out")

    p_pack = sub.add_parser("pack")
    pack_sub = p_pack.add_subparsers(dest="pack_cmd", required=True)
    p_plist = pack_sub.add_parser("list")
    p_plist.add_argument("--packs-dir")
    p_plist.add_argument("--json", dest="json_out")
    p_pshow = pack_sub.add_parser("show")
    p_pshow.add_argument("name")
    p_pshow.add_argument("--packs-dir")
    p_pinst = pack_sub.add_parser("install")
    p_pinst.add_argument("name")
    p_pinst.add_argument("--dest", required=True)
    p_pinst.add_argument("--packs-dir")
    p_pinst.add_argument("--apply", action="store_true")
    p_pinst.add_argument("--json", dest="json_out")
    p_pun = pack_sub.add_parser("uninstall")
    p_pun.add_argument("name")
    p_pun.add_argument("--dest", required=True)
    p_pun.add_argument("--packs-dir")
    p_pun.add_argument("--apply", action="store_true")

    p_graph = sub.add_parser("graph")
    p_graph.add_argument("root", nargs="?", default=".")
    p_graph.add_argument("--focus")
    p_graph.add_argument("--depth", type=int, default=1)
    p_graph.add_argument("--fail-on-cycle", action="store_true")
    p_graph.add_argument("--json", dest="json_out")

    p_adr = sub.add_parser("adr")
    adr_sub = p_adr.add_subparsers(dest="adr_cmd", required=True)
    p_adr_idx = adr_sub.add_parser("index")
    p_adr_idx.add_argument("root", nargs="?", default=".")
    p_adr_idx.add_argument("--dir", default="docs/adr")
    p_adr_idx.add_argument("--out")
    p_adr_idx.add_argument("--no-write", action="store_true", help="只校验，不写 INDEX.md")
    p_adr_idx.add_argument("--json", dest="json_out")

    p_panel = sub.add_parser("panel")
    p_panel.add_argument("root", nargs="?", default=".")
    p_panel.add_argument("--host", default="127.0.0.1")
    p_panel.add_argument("--port", type=int, default=8787)

    p_eval = sub.add_parser("eval")
    p_eval.add_argument("root", nargs="?", default=".")
    p_eval.add_argument("--preset")
    p_eval.add_argument("--json", dest="json_out")

    p_accept = sub.add_parser("accept")
    accept_sub = p_accept.add_subparsers(dest="accept_cmd", required=True)
    p_acc_init = accept_sub.add_parser("init")
    p_acc_init.add_argument("root", nargs="?", default=".")
    p_acc_init.add_argument("--run")
    p_acc_init.add_argument("--force", action="store_true")
    p_acc_init.add_argument("--json", dest="json_out")
    p_acc_check = accept_sub.add_parser("check")
    p_acc_check.add_argument("root", nargs="?", default=".")
    p_acc_check.add_argument("--run", required=True)
    p_acc_check.add_argument("--json", dest="json_out")

    p_task = sub.add_parser("task")
    task_sub = p_task.add_subparsers(dest="task_cmd", required=True)
    p_task_check = task_sub.add_parser("check")
    p_task_check.add_argument("root", nargs="?", default=".")
    p_task_check.add_argument("--json", dest="json_out")

    args = ap.parse_args(argv)
    if args.cmd == "pack":
        return cmd_pack(args)
    if not hasattr(args, "root"):
        ap.print_help()
        return 2
    root = Path(args.root)
    if not root.exists():
        print(f"root not found: {root}", file=sys.stderr)
        return 2
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "init":
        return cmd_init(root, args.apply)
    if args.cmd == "push-check":
        return cmd_push_check(args)
    if args.cmd == "wiki":
        return cmd_wiki(args)
    if args.cmd == "context":
        return cmd_context(args)
    if args.cmd == "profile":
        return cmd_profile(args)
    if args.cmd == "dup":
        return cmd_dup(args)
    if args.cmd == "impact":
        return cmd_impact(args)
    if args.cmd == "graph":
        return cmd_graph(args)
    if args.cmd == "adr":
        return cmd_adr(args)
    if args.cmd == "panel":
        return cmd_panel(args)
    if args.cmd == "eval":
        return cmd_eval(args)
    if args.cmd == "accept":
        return cmd_accept(args)
    if args.cmd == "task":
        return cmd_task(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
