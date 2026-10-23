"""proj panel —— 本地只读可视化面板（stdlib http.server，读 scan/graph/packs/wiki）。

设计口径：面板只读、只绑 127.0.0.1、与 DSH 版本解耦（浏览器直接开，不经宿主）。
除概览外提供**架构图 + 约束提示词生成**：点节点 → 生成「允许改 / 不得改 / 必须回归」的任务约束，
方便用户把 AI 的改动面框住（复制后贴进任务或写进 work/tasks/<id>/scope.md）。
用法：python tools/proj/panel.py [root] [--port 8787] [--host 127.0.0.1]
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import graph, packs, scanner, wiki

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>pydev 架构面板</title>
<style>
 body{font:14px/1.6 system-ui,Segoe UI,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:12px 20px;background:#171a21;border-bottom:1px solid #262b36;display:flex;gap:14px;align-items:center}
 header b{font-size:16px} .score{margin-left:auto;color:#7ee787}
 main{padding:16px 20px;display:grid;grid-template-columns:1fr 380px;gap:18px}
 section{margin-bottom:22px} h2{font-size:14px;color:#9aa4b2;border-bottom:1px solid #262b36;padding-bottom:6px;margin:0 0 10px}
 table{border-collapse:collapse;width:100%} td,th{padding:4px 8px;border-bottom:1px solid #1d2129;text-align:left;font-size:13px}
 .fail{color:#ff7b72}.warn{color:#d29922}.ok{color:#7ee787}
 input,button,textarea{background:#171a21;color:#e6e6e6;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font:inherit}
 button{cursor:pointer} button:hover{border-color:#58a6ff}
 pre{background:#12151b;padding:10px;border-radius:6px;overflow:auto;max-height:300px;font-size:12px;white-space:pre-wrap}
 #cv{background:#0b0d11;border:1px solid #262b36;border-radius:8px;width:100%;height:520px;display:block}
 #hint{color:#8b949e;font-size:12px}
 .chip{display:inline-block;background:#1f6feb33;border:1px solid #1f6feb;border-radius:10px;padding:1px 8px;margin:2px;font-size:12px;cursor:pointer}
 .chip.deny{background:#f8514933;border-color:#f85149}
</style></head><body>
<header><b>pydev 架构面板</b><span id="root"></span><span class="score" id="score"></span></header>
<main>
 <div>
  <section><h2>架构图（点击节点 → 生成约束）</h2>
   <canvas id="cv" width="900" height="520"></canvas>
   <p id="hint">节点=源文件；连线=import 方向。点节点后右侧生成「允许改 / 不得改 / 必须回归」。</p>
  </section>
  <section><h2>问题清单（scan）</h2><table id="issues"></table></section>
  <section><h2>Wiki 检索</h2><p><input id="q" placeholder="关键词"><button onclick="search()">搜索</button></p><pre id="wiki"></pre></section>
 </div>
 <div>
  <section><h2>当前选择</h2><div id="sel">（未选）</div>
   <p><button onclick="gen()">生成约束提示词</button> <button onclick="copyC()">复制</button></p>
  </section>
  <section><h2>约束提示词</h2><pre id="constraint">（点节点后生成）</pre></section>
  <section><h2>领域包</h2><table id="packs"></table></section>
 </div>
</main>
<script>
const api = async (p) => (await fetch(p)).json();
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
let G = null, layout = {}, sel = null;
const W = 900, H = 520, PAD = 40, NODE_W = 130, NODE_H = 22;
function rows(el, data, cols){ el.innerHTML = '<tr>' + cols.map(c=>'<th>'+c+'</th>').join('') + '</tr>' +
  data.map(r=>'<tr>' + cols.map(c=>'<td class="'+(r._level||'')+'">'+(r[c]??'')+'</td>').join('') + '</tr>').join(''); }
function short(p){ return p.length > 26 ? '…' + p.slice(-25) : p; }
function computeLayout(){
  const nodes = G.node_list.map(n=>n.path), idx = new Map(nodes.map((p,i)=>[p,i]));
  const indeg = new Map(nodes.map(p=>[p,0])); const out = new Map(nodes.map(p=>[p,[]]));
  G.edge_list.forEach(([a,b])=>{ if(!idx.has(a)||!idx.has(b)) return; indeg.set(b, (indeg.get(b)||0)+1); out.get(a).push(b); });
  const depth = new Map(nodes.map(p=>[p,0]));
  // 最长路径分层（迭代 N 轮，天然容忍环）
  for (let k=0;k<nodes.length;k++){
    for (const [a,b] of G.edge_list){ if(!idx.has(a)||!idx.has(b)) continue; depth.set(b, Math.max(depth.get(b), depth.get(a)+1)); }
  }
  const layers = new Map(); nodes.forEach(p=>{ const d=depth.get(p); (layers.get(d)||layers.set(d,[]).get(d)).push(p); });
  const maxD = Math.max(0, ...layers.keys()); const colW = (W - PAD*2) / (maxD + 1);
  layout = {};
  for (const [d, list] of layers){ const rowH = (H - PAD*2) / Math.max(1, list.length);
    list.forEach((p,i)=>{ layout[p] = { x: PAD + d*colW + 10, y: PAD + i*rowH + rowH/2 - NODE_H/2 }; }); }
  return { nodes, idx, indeg };
}
function draw(){
  ctx.clearRect(0,0,W,H); if(!G) return;
  const { nodes, idx, indeg } = computeLayout();
  ctx.strokeStyle = '#262b36'; ctx.lineWidth = 1;
  for (const [a,b] of G.edge_list){ const A=layout[a], B=layout[b]; if(!A||!B) continue;
    ctx.beginPath(); ctx.moveTo(A.x+NODE_W, A.y+NODE_H/2); ctx.bezierCurveTo((A.x+NODE_W+B.x)/2, A.y+NODE_H/2, (A.x+NODE_W+B.x)/2, B.y+NODE_H/2, B.x, B.y+NODE_H/2); ctx.stroke(); }
  for (const p of nodes){ const L=layout[p]; if(!L) continue; const isSel = p===sel;
    ctx.fillStyle = isSel ? '#1f6feb' : (indeg.get(p)===0 ? '#238636' : '#21262d');
    ctx.strokeStyle = isSel ? '#58a6ff' : '#30363d'; roundRect(ctx, L.x, L.y, NODE_W, NODE_H, 5); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#e6e6e6'; ctx.font = '11px system-ui'; ctx.fillText(short(p), L.x+6, L.y+15); }
}
function roundRect(c,x,y,w,h,r){ c.beginPath(); c.moveTo(x+r,y); c.arcTo(x+w,y,x+w,y+h,r); c.arcTo(x+w,y+h,x,y+h,r); c.arcTo(x,y+h,x,y,r); c.arcTo(x,y,x+w,y,r); c.closePath(); }
cv.addEventListener('click', async (e)=>{
  const r = cv.getBoundingClientRect(), sx = cv.width / r.width, sy = cv.height / r.height;
  const x = (e.clientX - r.left) * sx, y = (e.clientY - r.top) * sy;
  for (const [p,L] of Object.entries(layout)){ if (x>=L.x && x<=L.x+NODE_W && y>=L.y && y<=L.y+NODE_H){ sel = p; break; } }
  if (!sel) return; draw();
  const m = await api('/api/module?path=' + encodeURIComponent(sel));
  document.getElementById('sel').innerHTML = '<b>'+sel+'</b><br>下游(它依赖)：'+(m.out||[]).join(', ')+'<br>上游(依赖它)：'+(m.inn||[]).join(', ');
});
async function gen(){
  if(!sel){ return; }
  const c = await api('/api/constraint?focus=' + encodeURIComponent(sel));
  document.getElementById('constraint').textContent = c.markdown || c.error || '';
}
function copyC(){ const t=document.getElementById('constraint').textContent; navigator.clipboard && navigator.clipboard.writeText(t); }
async function boot(){
  const s = await api('/api/scan');
  document.getElementById('root').textContent = s.root;
  document.getElementById('score').textContent = 'score ' + s.score.total + '/100  privacy ' + s.score.privacy + '  size ' + s.score.size;
  rows(document.getElementById('issues'), (s.issues||[]).map(i=>({_level:i.level==='FAIL'?'fail':'warn',level:i.level,code:i.code,path:i.path,detail:i.detail})), ['level','code','path','detail']);
  G = await api('/api/graph'); draw();
  const p = await api('/api/packs');
  rows(document.getElementById('packs'), p.packs.map(x=>({name:x.name,skills:x.skills.length})), ['name','skills']);
}
async function search(){ const q=document.getElementById('q').value; const r=await api('/api/wiki?q='+encodeURIComponent(q));
  document.getElementById('wiki').textContent = r.results.map(x=>'['+x.score+'] '+x.title+' — '+x.path).join('\\n') || '(无命中)'; }
boot();
</script></body></html>
"""


def api_scan(root: Path) -> dict:
    return scanner.scan(root).to_dict()


def api_graph(root: Path) -> dict:
    g = graph.build_graph(root)
    data = g.to_dict()
    data["node_list"] = [{"path": p, "indegree": sum(1 for _, d in g.edges if d == p),
                          "outdegree": sum(1 for s, _ in g.edges if s == p)} for p in g.nodes]
    data["edge_list"] = [list(e) for e in g.edges]
    return data


def api_module(root: Path, path: str) -> dict:
    g = graph.build_graph(root)
    return {"path": path,
            "out": [b for a, b in g.edges if a == path],
            "inn": [a for a, b in g.edges if b == path]}


def api_constraint(root: Path, focus: str) -> dict:
    g = graph.build_graph(root)
    prof_path = root / ".dsh" / "project-profile.json"
    commands: dict = {}
    if prof_path.is_file():
        try:
            commands = json.loads(prof_path.read_text(encoding="utf-8")).get("commands", {})
        except (OSError, json.JSONDecodeError):
            commands = {}
    outs = [b for a, b in g.edges if a == focus]
    ins = [a for a, b in g.edges if b == focus]
    verify = commands.get("test") or commands.get("run") or "python -m pytest -q"
    md = "\n".join([
        "# 任务约束（proj panel 生成）",
        "",
        f"## 允许改动\n- `{focus}`（只改它；不顺手重构相邻文件）",
        "",
        "## 不得改动（公共接口 / 上游依赖）",
        *([f"- `{p}`" for p in sorted(set(ins))] or ["- （无上游）"]),
        "",
        "## 需要同步检查（下游）",
        *([f"- `{p}`" for p in sorted(set(outs))] or ["- （无下游）"]),
        "",
        "## 必须回归",
        f"- `{verify}`",
        "- `python tools\\proj\\proj.py scan .` 无新增 FAIL",
        "",
        "## 判定",
        "- 改动只出现在「允许改动」里；越界即回退并说明理由。",
        "- 回归命令输出原样贴进 `work/verify/<run>/RESULT.md`。",
    ])
    return {"focus": focus, "markdown": md, "markdown_lines": len(md.splitlines())}


def api_packs(root: Path) -> dict:
    return {"packs": [p.to_dict() for p in packs.list_packs(Path(packs.DEFAULT_PACKS_DIR))]}


def api_wiki(root: Path, query: str) -> dict:
    return {"query": query, "results": wiki.search(root, query, 20)}


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
                if parsed.path == "/api/scan":
                    data = api_scan(root)
                elif parsed.path == "/api/graph":
                    data = api_graph(root)
                elif parsed.path == "/api/module":
                    data = api_module(root, (qs.get("path") or [""])[0])
                elif parsed.path == "/api/constraint":
                    data = api_constraint(root, (qs.get("focus") or [""])[0])
                elif parsed.path == "/api/packs":
                    data = api_packs(root)
                elif parsed.path == "/api/wiki":
                    data = api_wiki(root, (qs.get("q") or [""])[0])
                else:
                    self.send_error(404, "not found")
                    return
            except Exception as exc:  # noqa: BLE001 - 面板需要把错误显示出来
                self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                return
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
