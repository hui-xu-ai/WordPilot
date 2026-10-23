"""proj panel —— 本地只读可视化面板（stdlib http.server，读 scan/graph/packs/wiki）。

设计口径：面板只读、只绑 127.0.0.1、与 DSH 版本解耦（浏览器直接开，不经宿主）。
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
<html lang="zh"><head><meta charset="utf-8"><title>pydev 项目面板</title>
<style>
 body{font:14px/1.6 system-ui,Segoe UI,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:14px 20px;background:#171a21;border-bottom:1px solid #262b36;display:flex;gap:16px;align-items:center}
 header b{font-size:16px} .score{margin-left:auto;color:#7ee787}
 main{padding:20px;max-width:1100px} section{margin-bottom:28px}
 h2{font-size:15px;color:#9aa4b2;border-bottom:1px solid #262b36;padding-bottom:6px}
 table{border-collapse:collapse;width:100%} td,th{padding:4px 8px;border-bottom:1px solid #1d2129;text-align:left;font-size:13px}
 .fail{color:#ff7b72}.warn{color:#d29922}.ok{color:#7ee787}
 input,button{background:#171a21;color:#e6e6e6;border:1px solid #30363d;border-radius:6px;padding:6px 10px}
 pre{background:#12151b;padding:10px;border-radius:6px;overflow:auto;max-height:320px}
</style></head><body>
<header><b>pydev 项目面板</b><span id="root"></span><span class="score" id="score"></span></header>
<main>
 <section><h2>问题清单（scan）</h2><table id="issues"></table></section>
 <section><h2>依赖（graph）</h2><div id="graph"></div></section>
 <section><h2>Wiki 检索</h2><p><input id="q" placeholder="关键词"><button onclick="search()">搜索</button></p><pre id="wiki"></pre></section>
 <section><h2>领域包</h2><table id="packs"></table></section>
</main>
<script>
const api = async (p) => (await fetch(p)).json();
function rows(el, data, cols){ el.innerHTML = '<tr>' + cols.map(c=>'<th>'+c+'</th>').join('') + '</tr>' +
  data.map(r=>'<tr>' + cols.map(c=>'<td class="'+(r._level||'')+'">'+(r[c]??'')+'</td>').join('') + '</tr>').join(''); }
async function boot(){
  const s = await api('/api/scan');
  document.getElementById('root').textContent = s.root;
  document.getElementById('score').textContent = 'score ' + s.score.total + '/100  privacy ' + s.score.privacy + '  size ' + s.score.size;
  rows(document.getElementById('issues'), (s.issues||[]).map(i=>({_level:i.level==='FAIL'?'fail':'warn',level:i.level,code:i.code,path:i.path,detail:i.detail})), ['level','code','path','detail']);
  const g = await api('/api/graph');
  document.getElementById('graph').innerHTML = '<p>节点 ' + g.nodes + ' / 内部依赖 ' + g.edges + ' / 循环 ' + (g.cycles||[]).length + '</p>' +
    '<pre>' + (g.top_imported||[]).map(t=>t.indegree + '  ' + t.path).join('\\n') + '</pre>';
  const p = await api('/api/packs');
  rows(document.getElementById('packs'), p.packs.map(x=>({name:x.name,skills:x.skills.length,list:x.skills.join(', ')})), ['name','skills','list']);
}
async function search(){ const q=document.getElementById('q').value; const r=await api('/api/wiki?q='+encodeURIComponent(q));
  document.getElementById('wiki').textContent = r.results.map(x=>'['+x.score+'] '+x.title+' — '+x.path).join('\\n') || '(无命中)'; }
boot();
</script></body></html>
"""


def api_scan(root: Path) -> dict:
    return scanner.scan(root).to_dict()


def api_graph(root: Path) -> dict:
    return graph.build_graph(root).to_dict()


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
            if parsed.path in ("/", "/index.html"):
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            try:
                if parsed.path == "/api/scan":
                    data = api_scan(root)
                elif parsed.path == "/api/graph":
                    data = api_graph(root)
                elif parsed.path == "/api/packs":
                    data = api_packs(root)
                elif parsed.path == "/api/wiki":
                    data = api_wiki(root, (parse_qs(parsed.query).get("q") or [""])[0])
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
