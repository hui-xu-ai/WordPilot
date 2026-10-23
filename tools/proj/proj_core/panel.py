"""proj panel —— 本地只读架构图谱（模块聚类 + 文件下钻 + 约束生成）。

设计参考：Codex project-architecture 的模块/违规模型；可视化借鉴文献计量图谱
（聚类着色、节点大小=入度、边宽=次数、标签避让、缩放分级）。
只绑 127.0.0.1、只读、无第三方依赖。
用法：python tools/proj/panel.py [root] [--port 8787] [--host 127.0.0.1]
"""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import arch, packs, scanner

PAGE = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>pydev 架构图谱</title>
<style>
 *{box-sizing:border-box} body{font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:0;background:#0b0e14;color:#e6edf3;height:100vh;overflow:hidden}
 header{padding:9px 14px;background:#11161f;border-bottom:1px solid #263041;display:flex;gap:12px;align-items:center}
 header b{font-size:15px} .muted{color:#8b949e;font-size:12px} .stat{margin-left:auto;font-size:12px;color:#9aa4b2}
 main{display:grid;grid-template-columns:minmax(0,1fr) 400px;height:calc(100vh - 45px)}
 #left{display:flex;flex-direction:column;min-width:0}
 #bar{display:flex;gap:8px;align-items:center;padding:7px 10px;border-bottom:1px solid #1c2430;flex-wrap:wrap}
 #wrap{position:relative;flex:1;min-height:0;background:#0b0e14}
 canvas{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab}
 canvas.drag{cursor:grabbing}
 #right{border-left:1px solid #263041;background:#0e131b;overflow:auto;min-width:0;padding:10px 12px}
 #right section{margin-bottom:16px;min-width:0}
 #right h3{font-size:12px;color:#8b949e;margin:0 0 7px;border-bottom:1px solid #1c2430;padding-bottom:4px;letter-spacing:.02em}
 #sel{font-size:12px;line-height:1.6;word-break:break-all}
 #constraint{background:#0b0e14;border:1px solid #1c2430;border-radius:6px;padding:9px;font-size:11.5px;white-space:pre-wrap;word-break:break-word;max-height:38vh;overflow:auto;margin:0}
 input,button{background:#0d121a;color:#e6edf3;border:1px solid #2b3648;border-radius:5px;padding:4px 8px;font:inherit;font-size:12px}
 button{cursor:pointer} button:hover{border-color:#4d8fdb}
 table{border-collapse:collapse;width:100%;font-size:12px} td,th{padding:3px 5px;border-bottom:1px solid #1c2430;text-align:left}
 .fail{color:#f85149}.warn{color:#d29922}.ok{color:#3fb950}
 .chip{display:inline-flex;align-items:center;gap:5px;border:1px solid #2b3648;border-radius:12px;padding:1px 8px;font-size:11px;cursor:pointer;user-select:none}
 .chip.off{opacity:.35;text-decoration:line-through}
 .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
 .tag{display:inline-block;background:#1f6feb22;border:1px solid #1f6feb66;border-radius:8px;padding:0 5px;margin:1px;font-size:11px;cursor:pointer}
</style></head><body>
<header><b>pydev 架构图谱</b><span class="muted" id="root"></span><span class="stat" id="stat"></span></header>
<main>
 <div id="left">
  <div id="bar">
   <input id="q" placeholder="搜索文件" size="18">
   <label class="muted"><input type="checkbox" id="cross" checked> 只画跨模块边</label>
   <label class="muted"><input type="checkbox" id="violonly"> 只看违规</label>
   <button onclick="fit()">适配</button>
   <span id="chips"></span>
  </div>
  <div id="wrap"><canvas id="cv"></canvas></div>
 </div>
 <div id="right">
  <section><h3>选中</h3><div id="sel">（点节点或模块圆）</div>
   <p><button onclick="gen()">生成约束提示词</button> <button onclick="copyC()">复制</button> <button onclick="writeScope()">写入 scope.md</button></p><div class="muted" id="wrote"></div></section>
  <section><h3>约束提示词</h3><pre id="constraint">（选文件或模块后生成）</pre></section>
  <section><h3>违规（模块对）</h3><table id="viol"></table></section>
  <section><h3>问题（scan）</h3><table id="issues"></table></section>
 </div>
</main>
<script>
const $=(id)=>document.getElementById(id); const api=async(p)=>(await fetch(p)).json();
const PALETTE=['#58a6ff','#3fb950','#d29922','#bc8cff','#39c5cf','#f778ba','#e3b341','#7ee787'];
let A=null, mods=[], files=[], edges=[], centers=new Map(), pos=new Map(), indeg=new Map();
let hidden=new Set(), sel=null, hover=null, crossOnly=true, violOnly=false, search='';
let view={k:1,x:0,y:0}, drag=null, moved=false, dpr=1, W=0, H=0;
const cv=$('cv'), ctx=cv.getContext('2d');
function base(p){ const s=p.split('/'); return s[s.length-1]; }
function color(mid){ const i=mods.findIndex(m=>m.id===mid); return PALETTE[(i<0?0:i)%PALETTE.length]; }
function isViol(a,b){ return A.violations.some(v=>v.from===a&&v.to===b); }
function resize(){ const r=cv.getBoundingClientRect(); dpr=window.devicePixelRatio||1; W=r.width; H=r.height; cv.width=W*dpr; cv.height=H*dpr; draw(); }
new ResizeObserver(resize).observe($('wrap'));

function layout(){
  mods=[...A.modules].sort((a,b)=>b.files-a.files); files=A.files; edges=A.file_edges;
  const cx=W/2, cy=H/2, R=Math.min(W,H)*0.36, n=Math.max(1,mods.length);
  centers=new Map();
  mods.forEach((m,i)=>{ const ang=-Math.PI/2 + i*2*Math.PI/n; centers.set(m.id,{x:cx+R*Math.cos(ang), y:cy+R*Math.sin(ang)}); });
  pos=new Map();
  mods.forEach(m=>{ const c=centers.get(m.id); const list=files.filter(f=>f.module===m.id);
    list.forEach((f,i)=>{ const ang=i*2.39996, rad=20+9*Math.sqrt(i); pos.set(f.path,{x:c.x+rad*Math.cos(ang), y:c.y+rad*Math.sin(ang)}); }); });
  // 力导向微调（斥力 + 边弹簧 + 模块引力）
  const list=files.filter(f=>pos.has(f.path));
  for(let it=0; it<220; it++){
    for(let i=0;i<list.length;i++) for(let j=i+1;j<list.length;j++){
      const A1=pos.get(list[i].path), B1=pos.get(list[j].path); let dx=B1.x-A1.x, dy=B1.y-A1.y; const d2=dx*dx+dy*dy+0.01, d=Math.sqrt(d2);
      if(d>80) continue; const f=1400/d2; A1.x-=dx/d*f; A1.y-=dy/d*f; B1.x+=dx/d*f; B1.y+=dy/d*f; }
    edges.forEach(([a,b])=>{ const A1=pos.get(a), B1=pos.get(b); if(!A1||!B1) return; const dx=B1.x-A1.x,dy=B1.y-A1.y,d=Math.hypot(dx,dy)||1; const f=(d-52)*0.02; A1.x+=dx/d*f; A1.y+=dy/d*f; B1.x-=dx/d*f; B1.y-=dy/d*f; });
    list.forEach(f=>{ const P=pos.get(f.path), c=centers.get(f.module); if(!c) return; P.x+=(c.x-P.x)*0.02; P.y+=(c.y-P.y)*0.02; });
  }
  fit();
}

function moduleRadius(mid){ const c=centers.get(mid); if(!c) return 24; let r=24; files.filter(f=>f.module===mid).forEach(f=>{ const P=pos.get(f.path); if(P) r=Math.max(r, Math.hypot(P.x-c.x,P.y-c.y)+16); });
  let cap=1e9; mods.forEach(o=>{ if(o.id===mid) return; const c2=centers.get(o.id); if(c2) cap=Math.min(cap, Math.hypot(c.x-c2.x,c.y-c2.y)*0.46); });
  return Math.max(22, Math.min(r,150,cap)); }

function draw(){
  if(!ctx) return; ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,W,H); ctx.fillStyle='#0b0e14'; ctx.fillRect(0,0,W,H);
  if(!A || !centers.size) return; ctx.save(); ctx.translate(view.x,view.y); ctx.scale(view.k,view.k);
  const vis=f=>!hidden.has(f.module) && (!violOnly || A.violations.some(v=>v.from===f.module||v.to===f.module));
  // 模块光晕
  mods.forEach(m=>{ if(hidden.has(m.id)) return; const c=centers.get(m.id); const r=moduleRadius(m.id);
    ctx.beginPath(); ctx.arc(c.x,c.y,r,0,Math.PI*2); ctx.fillStyle=color(m.id)+'14'; ctx.fill();
    ctx.strokeStyle=color(m.id)+'55'; ctx.lineWidth=1; ctx.stroke();
    ctx.fillStyle=color(m.id); ctx.font='12px system-ui'; ctx.textAlign='center';
    ctx.fillText(m.id+' · '+m.files, c.x, c.y-r-6); ctx.textAlign='left'; });
  // 边：跨模块聚合
  const agg=new Map();
  edges.forEach(([a,b])=>{ const fa=files.find(f=>f.path===a), fb=files.find(f=>f.path===b); if(!fa||!fb||!fa.module||!fb.module||fa.module===fb.module) return;
    const key=fa.module+'>'+fb.module; agg.set(key,(agg.get(key)||0)+1); });
  agg.forEach((n,key)=>{ const [ma,mb]=key.split('>'); if(hidden.has(ma)||hidden.has(mb)) return;
    const bad=isViol(ma,mb); if(violOnly && !bad) return; const c1=centers.get(ma), c2=centers.get(mb);
    ctx.strokeStyle=bad?'#f85149':color(ma)+'66'; ctx.lineWidth=Math.max(1,(1+Math.log2(1+n))*0.9); ctx.setLineDash(bad?[5,4]:[]);
    const mx=(c1.x+c2.x)/2, my=(c1.y+c2.y)/2-40; ctx.beginPath(); ctx.moveTo(c1.x,c1.y); ctx.quadraticCurveTo(mx,my,c2.x,c2.y); ctx.stroke(); ctx.setLineDash([]);
    let ly=my-3, ok=false; for(const dy of [0,-14,14,-28,28]){ ly=my-3+dy; ok=!files.some(f=>{ const P=pos.get(f.path); return P && Math.hypot(P.x-mx,P.y-ly)<15; }); if(ok) break; } if(ok){ ctx.fillStyle=bad?'#f85149':'#8b949e'; ctx.font='10px system-ui'; ctx.fillText('x'+n, mx, ly); } });
  if(!crossOnly){ edges.forEach(([a,b])=>{ const fa=files.find(f=>f.path===a), fb=files.find(f=>f.path===b); if(!fa||!fb||!fa.module||!fb.module||fa.module!==fb.module) return;
    if(!vis(fa)||!vis(fb)) return; const P=pos.get(a), Q=pos.get(b); if(!P||!Q) return;
    ctx.strokeStyle='rgba(110,118,129,0.18)'; ctx.lineWidth=0.6/view.k; ctx.beginPath(); ctx.moveTo(P.x,P.y); ctx.lineTo(Q.x,Q.y); ctx.stroke(); }); }
  // 节点
  const rank=[...files].sort((x,y)=>y.indeg-x.indeg); const top=new Set(rank.slice(0,10).map(f=>f.path)); const labelRects=[];
  files.forEach(f=>{ if(!vis(f)) return; const P=pos.get(f.path); if(!P) return; const r=Math.min(13, 2.6+1.5*Math.sqrt(f.indeg));
    const on = sel&&sel.kind==='file'&&sel.id===f.path;
    ctx.beginPath(); ctx.arc(P.x,P.y,r,0,Math.PI*2); ctx.fillStyle=color(f.module); ctx.globalAlpha=on?1:.88; ctx.fill(); ctx.globalAlpha=1;
    if(on){ ctx.strokeStyle='#fff'; ctx.lineWidth=2; ctx.stroke(); }
    const showLabel = on || top.has(f.path) || view.k>1.7 || (hover===f.path) || (search && f.path.toLowerCase().includes(search));
    if(showLabel){ ctx.font='10px system-ui'; const tw=ctx.measureText(base(f.path)).width, lx=P.x+r+3, ly=P.y-9, lw=tw+2, lh=12;
      const hitRect=labelRects.some(R=>lx<R.x+R.w&&lx+lw>R.x&&ly<R.y+R.h&&ly+lh>R.y);
      if(!hitRect){ labelRects.push({x:lx,y:ly,w:lw,h:lh}); ctx.fillStyle=on?'#fff':'#c9d1d9'; ctx.font='10px system-ui'; ctx.fillText(base(f.path), lx, ly+9); } } });
  ctx.restore();
}
function toWorld(ev){ const r=cv.getBoundingClientRect(); return {x:(ev.clientX-r.left-view.x)/view.k, y:(ev.clientY-r.top-view.y)/view.k}; }
function hit(w){
  for(const f of files){ if(hidden.has(f.module)) continue; const P=pos.get(f.path); if(!P) continue;
    const r=Math.max(5, Math.min(13, 2.6+1.5*Math.sqrt(f.indeg))); if(Math.hypot(w.x-P.x,w.y-P.y)<=r+3) return {kind:'file',id:f.path}; }
  for(const m of mods){ if(hidden.has(m.id)) continue; const c=centers.get(m.id), r=moduleRadius(m.id); if(Math.hypot(w.x-c.x,w.y-c.y)<=r) return {kind:'module',id:m.id}; }
  return null;
}
cv.addEventListener('wheel',(ev)=>{ ev.preventDefault(); const r=cv.getBoundingClientRect(); const mx=ev.clientX-r.left,my=ev.clientY-r.top;
  const k2=Math.min(6,Math.max(0.12,view.k*(ev.deltaY<0?1.12:0.89))), f=k2/view.k; view.x=mx-(mx-view.x)*f; view.y=my-(my-view.y)*f; view.k=k2; draw(); },{passive:false});
cv.addEventListener('pointerdown',(ev)=>{ const w=toWorld(ev), h=hit(w); moved=false; cv.setPointerCapture(ev.pointerId);
  drag={sx:ev.clientX-view.x, sy:ev.clientY-view.y}; cv.classList.add('drag'); });
cv.addEventListener('pointermove',(ev)=>{ if(drag){ moved=true; view.x=ev.clientX-drag.sx; view.y=ev.clientY-drag.sy; draw(); }
  else { const h=hit(toWorld(ev)); const id=h&&h.kind==='file'?h.id:null; if(id!==hover){ hover=id; draw(); } } });
cv.addEventListener('pointerup',()=>{ drag=null; cv.classList.remove('drag'); });
cv.addEventListener('click',(ev)=>{ const h=hit(toWorld(ev)); if(!h) return; sel=h; draw(); show(h); });
function show(h){
  if(h.kind==='module'){ const m=mods.find(x=>x.id===h.id);
    $('sel').innerHTML='<b>模块 '+m.id+'</b><br><span class="muted">'+m.files+' 文件 · 允许依赖</span> '+(m.allowed.join(', ')||'（无）')+
      '<br><span class="muted">路径</span> '+m.paths.join(', '); }
  else { const f=files.find(x=>x.path===h.id);
    $('sel').innerHTML='<b>'+f.path+'</b><br><span class="muted">模块</span> '+f.module+' · <span class="muted">入度</span> '+f.indeg+' · <span class="muted">出度</span> '+f.outdeg; }
}
async function gen(){ if(!sel) return; const q=sel.kind==='module'?('module='+encodeURIComponent(sel.id)):('file='+encodeURIComponent(sel.id));
  const c=await api('/api/constraint?'+q); $('constraint').textContent=c.markdown||c.error||''; }
function copyC(){ const t=$('constraint').textContent; if(navigator.clipboard) navigator.clipboard.writeText(t); }
function writeScope(){ if(!sel) return; fetch('/api/scope',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:sel.kind,id:sel.id})}).then(r=>r.json()).then(d=>{ $('wrote').textContent=d.path?('已写入 '+d.path):('失败：'+(d.error||'')); }); }
function fit(){ if(!files.length || !centers.size) return; let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  files.forEach(f=>{ const P=pos.get(f.path); if(!P) return; x0=Math.min(x0,P.x); y0=Math.min(y0,P.y); x1=Math.max(x1,P.x); y1=Math.max(y1,P.y); });
  mods.forEach(m=>{ const c=centers.get(m.id), r=moduleRadius(m.id); x0=Math.min(x0,c.x-r); y0=Math.min(y0,c.y-r); x1=Math.max(x1,c.x+r); y1=Math.max(y1,c.y+r); });
  const pad=36, k=Math.min((W-pad*2)/(x1-x0+1),(H-pad*2)/(y1-y0+1),1.6); view.k=Math.max(0.12,k);
  view.x=(W-(x1-x0)*view.k)/2 - x0*view.k; view.y=(H-(y1-y0)*view.k)/2 - y0*view.k; draw(); }
$('cross').onchange=(e)=>{ crossOnly=e.target.checked; draw(); };
$('violonly').onchange=(e)=>{ violOnly=e.target.checked; draw(); };
$('q').addEventListener('input',(e)=>{ search=e.target.value.trim().toLowerCase(); draw(); });
$('q').addEventListener('keydown',(e)=>{ if(e.key!=='Enter'||!search) return; const f=files.find(x=>x.path.toLowerCase().includes(search)); if(!f) return;
  sel={kind:'file',id:f.path}; show(sel); const P=pos.get(f.path); view.x=W/2-P.x*view.k; view.y=H/2-P.y*view.k; draw(); });
function chips(){ $('chips').innerHTML=mods.map(m=>'<span class="chip'+(hidden.has(m.id)?' off':'')+'" onclick="toggleMod(\''+m.id+'\')"><i class="dot" style="background:'+color(m.id)+'"></i>'+m.id+'</span>').join(' '); }
function toggleMod(id){ if(hidden.has(id)) hidden.delete(id); else hidden.add(id); chips(); draw(); }
function rows(el,data,cols){ el.innerHTML='<tr>'+cols.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+
  data.map(r=>'<tr>'+cols.map(c=>'<td class="'+(r._level||'')+'" title="'+(r[c]??'')+'">'+(r[c]??'')+'</td>').join('')+'</tr>').join(''); }
async function boot(){
  const s=await api('/api/scan'); $('root').textContent=s.root;
  A=await api('/api/arch'); mods=A.modules; files=A.files; edges=A.file_edges;
  $('stat').textContent=mods.length+' 模块 / '+files.length+' 文件 / '+edges.length+' 边 / '+A.violations.length+' 违规  score '+s.score.total;
  resize(); layout(); chips();
  rows($('viol'), (A.violations||[]).map(v=>({_level:'fail',from:v.from,to:v.to,count:v.count})), ['from','to','count']);
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
