"""proj panel —— 本地只读架构面板（模块级监管，stdlib http.server）。

设计参考：Codex project-architecture 的 model.toml + 违规检查（只借原则）。
- `/api/arch`：模块、实际依赖、违规（声明模型来自 `.dsh/architecture.toml`）；
- `/api/constraint?module=<id>`：生成「允许改 / 不得改 / 必须回归」约束提示词；
- `/api/scan` `/api/packs`：概览。
只绑 127.0.0.1、只读、无第三方依赖。
用法：python tools/proj/panel.py [root] [--port 8787] [--host 127.0.0.1]
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import arch, packs, scanner

PAGE = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>pydev 架构面板</title>
<style>
 *{box-sizing:border-box} body{font:14px/1.55 system-ui,Segoe UI,sans-serif;margin:0;background:#0d1117;color:#e6edf3;height:100vh;overflow:hidden}
 header{padding:10px 16px;background:#161b22;border-bottom:1px solid #30363d;display:flex;gap:14px;align-items:center}
 header b{font-size:15px} .muted{color:#8b949e;font-size:12px} .score{margin-left:auto;color:#3fb950;font-size:12px}
 main{display:grid;grid-template-columns:minmax(0,1fr) 420px;height:calc(100vh - 47px)}
 #left{display:flex;flex-direction:column;min-width:0}
 #bar{display:flex;gap:10px;align-items:center;padding:8px 12px;border-bottom:1px solid #21262d;flex-wrap:wrap}
 #wrap{position:relative;flex:1;min-height:0;background:#0b0e14}
 canvas{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab}
 canvas.drag{cursor:grabbing}
 #right{border-left:1px solid #30363d;background:#0f141b;overflow:auto;min-width:0;padding:12px 14px}
 #right section{margin-bottom:18px;min-width:0}
 #right h3{font-size:13px;color:#8b949e;margin:0 0 8px;border-bottom:1px solid #21262d;padding-bottom:4px}
 #sel{font-size:12px;line-height:1.6;word-break:break-all}
 #files{font-size:12px;max-height:220px;overflow:auto;margin:0;padding-left:18px;word-break:break-all}
 #constraint{background:#0b0e14;border:1px solid #21262d;border-radius:6px;padding:10px;font-size:12px;white-space:pre-wrap;word-break:break-word;max-height:40vh;overflow:auto;margin:0}
 input,button{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:5px 9px;font:inherit;font-size:12px}
 button{cursor:pointer} button:hover{border-color:#58a6ff}
 table{border-collapse:collapse;width:100%;font-size:12px} td,th{padding:3px 6px;border-bottom:1px solid #1d2129;text-align:left}
 .fail{color:#f85149}.warn{color:#d29922}.ok{color:#3fb950}
</style></head><body>
<header><b>pydev 架构面板</b><span class="muted" id="root"></span><span class="muted" id="model"></span><span class="score" id="score"></span></header>
<main>
 <div id="left">
  <div id="bar"><span class="muted" id="stat"></span><button onclick="fit()">适配</button><span class="muted">滚轮缩放 / 拖空白平移 / 点模块看详情</span></div>
  <div id="wrap"><canvas id="cv"></canvas></div>
 </div>
 <div id="right">
  <section><h3>当前模块</h3><div id="sel">（点模块）</div><p><button onclick="gen()">生成约束提示词</button> <button onclick="copyC()">复制</button></p></section>
  <section><h3>模块内文件</h3><ol id="files"></ol></section>
  <section><h3>约束提示词</h3><pre id="constraint">（选模块后生成）</pre></section>
  <section><h3>违规</h3><table id="viol"></table></section>
  <section><h3>问题清单（scan）</h3><table id="issues"></table></section>
 </div>
</main>
<script>
const $=(id)=>document.getElementById(id); const api=async(p)=>(await fetch(p)).json();
let A=null, mods=[], edges=[], sel=null, pos=new Map();
let view={k:1,x:0,y:0}, drag=null, moved=false, dpr=1, W=0, H=0;
const cv=$('cv'), ctx=cv.getContext('2d'); const BW=180, BH=58;
function resize(){ const r=cv.getBoundingClientRect(); dpr=window.devicePixelRatio||1; W=r.width; H=r.height; cv.width=W*dpr; cv.height=H*dpr; draw(); }
new ResizeObserver(resize).observe($('wrap'));
function layout(){
  mods=A.modules; edges=A.edges; pos=new Map();
  const n=mods.length, cols=Math.max(1,Math.ceil(Math.sqrt(n))), rows=Math.ceil(n/cols);
  const gx=W?(W-80)/cols:220, gy=H?(H-80)/rows:150;
  mods.forEach((m,i)=>{ const c=i%cols, r=Math.floor(i/cols); pos.set(m.id,{x:40+c*gx+10, y:40+r*gy+10}); });
  $('stat').textContent=mods.length+' 模块 / '+edges.length+' 条模块依赖 / '+A.violations.length+' 条违规';
  fit();
}
function draw(){
  if(!ctx) return; ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,W,H); ctx.fillStyle='#0b0e14'; ctx.fillRect(0,0,W,H);
  if(!A) return; ctx.save(); ctx.translate(view.x,view.y); ctx.scale(view.k,view.k);
  const viol=new Set(A.violations.map(v=>v.from+'>'+v.to));
  edges.forEach(e=>{ const a=pos.get(e.from), b=pos.get(e.to); if(!a||!b) return;
    const bad=viol.has(e.from+'>'+e.to); const hot=sel&&(e.from===sel||e.to===sel);
    ctx.strokeStyle=bad?'#f85149':(hot?'#58a6ff':'rgba(110,118,129,0.5)'); ctx.lineWidth=Math.max(1,2/view.k);
    ctx.setLineDash(bad?[6,4]:[]); ctx.beginPath();
    const x1=a.x+BW/2, y1=a.y+BH/2, x2=b.x+BW/2, y2=b.y+BH/2;
    ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke(); ctx.setLineDash([]);
    if(view.k>0.5){ ctx.fillStyle=bad?'#f85149':'#8b949e'; ctx.font='11px system-ui';
      ctx.fillText('x'+e.count, (x1+x2)/2+4, (y1+y2)/2-4); } });
  mods.forEach(m=>{ const P=pos.get(m.id); if(!P) return; const on=m.id===sel;
    ctx.fillStyle=on?'#1f6feb':(m.status==='implemented'?'#21262d':'#2d2a1a');
    ctx.strokeStyle=on?'#58a6ff':(m.status==='implemented'?'#30363d':'#d29922');
    roundRect(ctx,P.x,P.y,BW,BH,7); ctx.fill(); ctx.stroke();
    ctx.fillStyle='#e6edf3'; ctx.font='13px system-ui'; ctx.fillText(m.id, P.x+10, P.y+22);
    ctx.fillStyle='#8b949e'; ctx.font='11px system-ui'; ctx.fillText(m.files+' files', P.x+10, P.y+40); });
  ctx.restore();
}
function roundRect(c,x,y,w,h,r){ c.beginPath(); c.moveTo(x+r,y); c.arcTo(x+w,y,x+w,y+h,r); c.arcTo(x+w,y+h,x,y+h,r); c.arcTo(x,y+h,x,y,r); c.arcTo(x,y,x+w,y,r); c.closePath(); }
function toWorld(ev){ const r=cv.getBoundingClientRect(); return {x:(ev.clientX-r.left-view.x)/view.k, y:(ev.clientY-r.top-view.y)/view.k}; }
function hit(w){ for(const m of mods){ const P=pos.get(m.id); if(P&&w.x>=P.x&&w.x<=P.x+BW&&w.y>=P.y&&w.y<=P.y+BH) return m.id; } return null; }
cv.addEventListener('wheel',(ev)=>{ ev.preventDefault(); const r=cv.getBoundingClientRect(); const mx=ev.clientX-r.left,my=ev.clientY-r.top;
  const k2=Math.min(4,Math.max(0.15,view.k*(ev.deltaY<0?1.12:0.89))), f=k2/view.k; view.x=mx-(mx-view.x)*f; view.y=my-(my-view.y)*f; view.k=k2; draw(); },{passive:false});
cv.addEventListener('pointerdown',(ev)=>{ const w=toWorld(ev), p=hit(w); moved=false; cv.setPointerCapture(ev.pointerId);
  drag=p?{kind:'node',id:p,ox:w.x,oy:w.y}:{kind:'pan',sx:ev.clientX-view.x,sy:ev.clientY-view.y}; cv.classList.add('drag'); });
cv.addEventListener('pointermove',(ev)=>{ if(!drag) return; moved=true;
  if(drag.kind==='pan'){ view.x=ev.clientX-drag.sx; view.y=ev.clientY-drag.sy; }
  else { const w=toWorld(ev), P=pos.get(drag.id); if(P){ P.x+=w.x-drag.ox; P.y+=w.y-drag.oy; drag.ox=w.x; drag.oy=w.y; } }
  draw(); });
cv.addEventListener('pointerup',()=>{ const was=drag; drag=null; cv.classList.remove('drag'); if(was&&was.kind==='node'&&!moved) select(was.id); });
async function select(id){ sel=id; draw(); const m=mods.find(x=>x.id===id);
  $('sel').innerHTML='<b>'+m.id+'</b> <span class="muted">('+m.status+')</span><br>'+ (m.description||'') +
    '<br><span class="muted">路径</span> '+m.paths.join(', ')+'<br><span class="muted">允许依赖</span> '+(m.allowed.join(', ')||'（无）');
  $('files').innerHTML=(m.file_list||[]).map(f=>'<li>'+f+'</li>').join('')||'<li>（无）</li>'; }
async function gen(){ if(!sel) return; const c=await api('/api/constraint?module='+encodeURIComponent(sel)); $('constraint').textContent=c.markdown||c.error||''; }
function copyC(){ const t=$('constraint').textContent; if(navigator.clipboard) navigator.clipboard.writeText(t); }
function fit(){ if(!mods.length) return; let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  mods.forEach(m=>{ const P=pos.get(m.id); if(!P) return; x0=Math.min(x0,P.x); y0=Math.min(y0,P.y); x1=Math.max(x1,P.x+BW); y1=Math.max(y1,P.y+BH); });
  const pad=40, k=Math.min((W-pad*2)/(x1-x0+1),(H-pad*2)/(y1-y0+1),1.5); view.k=Math.max(0.15,k);
  view.x=pad-x0*view.k; view.y=pad-y0*view.k; draw(); }
function rows(el,data,cols){ el.innerHTML='<tr>'+cols.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+
  data.map(r=>'<tr>'+cols.map(c=>'<td class="'+(r._level||'')+'" title="'+(r[c]??'')+'">'+(r[c]??'')+'</td>').join('')+'</tr>').join(''); }
async function boot(){
  const s=await api('/api/scan'); $('root').textContent=s.root;
  $('score').textContent='score '+s.score.total+'/100  privacy '+s.score.privacy;
  A=await api('/api/arch'); $('model').textContent=A.declared?'模型：已声明':'模型：未声明（推导）';
  resize(); layout();
  rows($('viol'), (A.violations||[]).map(v=>({_level:'fail',from:v.from,to:v.to,count:v.count,reason:v.reason})), ['from','to','count']);
  rows($('issues'), (s.issues||[]).map(i=>({_level:i.level==='FAIL'?'fail':'warn',level:i.level,code:i.code,path:i.path})), ['level','code','path']);
}
boot();
</script></body></html>
"""


def _commands(root: Path) -> dict:
    prof = root / ".dsh" / "project-profile.json"
    if not prof.is_file():
        return {}
    try:
        return json.loads(prof.read_text(encoding="utf-8")).get("commands", {})
    except (OSError, json.JSONDecodeError):
        return {}


def api_arch(root: Path) -> dict:
    return arch.build_arch(root).to_dict()


def api_constraint(root: Path, module: str) -> dict:
    report = arch.build_arch(root)
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
                    data = api_constraint(root, (qs.get("module") or [""])[0])
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
