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
 #constraint{background:#0b0e14;border:1px solid #1c2430;border-radius:6px;padding:9px;font-size:11.5px;white-space:pre-wrap;word-break:break-word;max-height:36vh;overflow:auto;margin:0}
 input,button{background:#0d121a;color:#e6edf3;border:1px solid #2b3648;border-radius:5px;padding:4px 8px;font:inherit;font-size:12px}
 button{cursor:pointer} button:hover{border-color:#4d8fdb}
 table{border-collapse:collapse;width:100%;font-size:12px} td,th{padding:3px 5px;border-bottom:1px solid #1c2430;text-align:left}
 .fail{color:#f85149}.warn{color:#d29922}.ok{color:#3fb950}
 .chip{display:inline-flex;align-items:center;gap:5px;border:1px solid #2b3648;border-radius:12px;padding:1px 8px;font-size:11px;cursor:pointer;user-select:none}
 .chip.off{opacity:.35;text-decoration:line-through}
 .chip.pick{outline:1px solid #58a6ff;background:#1f6feb22}
 .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
 #zoom{min-width:46px;text-align:center}
</style></head><body>
<header><b>pydev 架构图谱</b><span class="muted" id="ver"></span><span class="muted" id="root"></span><span class="stat" id="stat"></span></header>
<main>
 <div id="left">
  <div id="bar">
   <input id="q" placeholder="搜索文件" size="16">
   <label class="muted"><input type="checkbox" id="cross" checked> 跨模块边</label>
   <label class="muted"><input type="checkbox" id="violonly"> 只看违规</label>
   <button onclick="zoomBy(0.85)">−</button><span class="muted" id="zoom">100%</span><button onclick="zoomBy(1.18)">+</button>
   <button onclick="fit()">适配</button>
   <button onclick="collapseAll()">折叠全部</button><button onclick="expandAll()">展开全部</button>
   <button onclick="showImpact()">变更影响</button>
   <label class="muted"><input type="checkbox" id="auto" checked> 自动刷新</label>
   <span id="chips"></span><span id="pickbar"></span><span class="muted" id="hint"></span>
  </div>
  <div id="wrap"><canvas id="cv"></canvas></div>
 </div>
 <div id="right">
  <section><h3>选中</h3><div id="sel">（点节点，双击模块折叠/展开）</div>
   <p><button onclick="gen()">生成约束提示词</button> <button onclick="copyC()">复制</button> <button onclick="writeScope()">写入 scope.md</button></p>
   <div class="muted" id="wrote"></div><div class="muted" id="impact"></div><p><button id="focusBtn" onclick="toggleFocus()" style="display:none">只看它的邻居</button></p></section>
  <section><h3>约束提示词</h3><pre id="constraint">（选文件或模块后生成）</pre></section>
  <section><h3>违规（模块对 / 公开入口）</h3><table id="viol"></table></section>
  <section><h3>问题（scan）</h3><table id="issues"></table></section>
 </div>
</main>
<script>
const $=(id)=>document.getElementById(id); const api=async(p)=>(await fetch(p)).json();
const PALETTE=['#58a6ff','#3fb950','#d29922','#bc8cff','#39c5cf','#f778ba','#e3b341','#7ee787'];
let A=null, mods=[], files=[], edges=[], centers=new Map(), pos=new Map(), radii=new Map();
let hidden=new Set(), collapsed=new Set(), sel=null, hover=null, crossOnly=true, violOnly=false, search='';
let focusMod=null, focusSet=null, hoverEdge=null, edgeGeom=[], pick=new Set();
let impact=null, fp=null;
let view={k:1,x:0,y:0}, drag=null, moved=false, dpr=1, W=0, H=0;
const cv=$('cv'), ctx=cv.getContext('2d');
function base(p){ const s=p.split('/'); return s[s.length-1]; }
function color(mid){ const i=mods.findIndex(m=>m.id===mid); return PALETTE[(i<0?0:i)%PALETTE.length]; }
function depViol(a,b){ return A.violations.some(v=>(v.kind||'dependency')==='dependency'&&v.from===a&&v.to===b); }
function pubViol(mid){ return A.violations.some(v=>v.kind==='public'&&v.to===mid); }
function modVisible(m){ return !hidden.has(m.id) && (!focusSet || focusSet.has(m.id)); }
function nodeR(f){ return Math.min(13, 2.6+1.5*Math.sqrt(f.indeg)); }
function resize(){ const r=cv.getBoundingClientRect(); dpr=window.devicePixelRatio||1; W=r.width; H=r.height; cv.width=W*dpr; cv.height=H*dpr; draw(); }
new ResizeObserver(resize).observe($('wrap'));

function moduleOffsets(){
  mods=[...A.modules].sort((a,b)=>b.files-a.files); files=A.files; edges=A.file_edges;
  const cx=W/2, cy=H/2, R=Math.min(W,H)*0.32, n=Math.max(1,mods.length);
  centers=new Map(); pos=new Map();
  mods.forEach((m,i)=>{ const ang=-Math.PI/2+i*2*Math.PI/n; centers.set(m.id,{x:cx+R*Math.cos(ang), y:cy+R*Math.sin(ang)}); });
  mods.forEach(m=>{ const c=centers.get(m.id); const list=files.filter(f=>f.module===m.id);
    list.forEach((f,i)=>{ const ang=i*2.39996, rad=20+9*Math.sqrt(i); pos.set(f.path,{x:c.x+rad*Math.cos(ang), y:c.y+rad*Math.sin(ang)}); }); });
  const list=files.filter(f=>pos.has(f.path));
  for(let it=0; it<220; it++){
    for(let i=0;i<list.length;i++) for(let j=i+1;j<list.length;j++){
      const P=pos.get(list[i].path), Q=pos.get(list[j].path); let dx=Q.x-P.x, dy=Q.y-P.y; const d2=dx*dx+dy*dy+0.01, d=Math.sqrt(d2);
      if(d>80) continue; const f=1400/d2; P.x-=dx/d*f; P.y-=dy/d*f; Q.x+=dx/d*f; Q.y+=dy/d*f; }
    edges.forEach(([a,b])=>{ const P=pos.get(a), Q=pos.get(b); if(!P||!Q) return; const dx=Q.x-P.x,dy=Q.y-P.y,d=Math.hypot(dx,dy)||1; const f=(d-52)*0.02; P.x+=dx/d*f; P.y+=dy/d*f; Q.x-=dx/d*f; Q.y-=dy/d*f; });
    list.forEach(f=>{ const P=pos.get(f.path), c=centers.get(f.module); if(!c) return; P.x+=(c.x-P.x)*0.02; P.y+=(c.y-P.y)*0.02; });
  }
  // 半径（相对当前中心）
  radii=new Map();
  mods.forEach(m=>{ const c=centers.get(m.id); let r=26;
    files.filter(f=>f.module===m.id).forEach(f=>{ const P=pos.get(f.path); if(P) r=Math.max(r, Math.hypot(P.x-c.x,P.y-c.y)+18); });
    radii.set(m.id, Math.min(r,170)); });
}

function separateCenters(){
  const cx=W/2, cy=H/2, n=Math.max(1,mods.length);
  let R=Math.min(W,H)*0.30;
  for(let iter=0; iter<60; iter++){
    const cs=new Map(); mods.forEach((m,i)=>{ const ang=-Math.PI/2+i*2*Math.PI/n; cs.set(m.id,{x:cx+R*Math.cos(ang), y:cy+R*Math.sin(ang)}); });
    let ok=true;
    for(let i=0;i<mods.length&&ok;i++) for(let j=i+1;j<mods.length&&ok;j++){
      const a=cs.get(mods[i].id), b=cs.get(mods[j].id);
      if(Math.hypot(a.x-b.x,a.y-b.y) < radii.get(mods[i].id)+radii.get(mods[j].id)+14) ok=false; }
    if(ok){ // 平移文件节点随中心移动
      mods.forEach(m=>{ const old=centers.get(m.id), now=cs.get(m.id); const dx=now.x-old.x, dy=now.y-old.y;
        files.filter(f=>f.module===m.id).forEach(f=>{ const P=pos.get(f.path); if(P){ P.x+=dx; P.y+=dy; } }); });
      centers=cs; return; }
    R*=1.12;
  }
}

function layout(){ moduleOffsets(); separateCenters(); fit(); }

function nodePos(f){ if(collapsed.has(f.module)) return centers.get(f.module); return pos.get(f.path); }
function moduleLabelPos(m){ const c=centers.get(m.id), r=collapsed.has(m.id)?collapseR(m):radii.get(m.id); return {x:c.x, y:c.y-r-8}; }
function collapseR(m){ return Math.max(26, Math.min(58, 6*Math.sqrt(m.files))); }

function draw(){
  if(!ctx) return; ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,W,H); ctx.fillStyle='#0b0e14'; ctx.fillRect(0,0,W,H);
  if(!A || !centers.size) return; ctx.save(); ctx.translate(view.x,view.y); ctx.scale(view.k,view.k);
  const labelRects=[]; const circles=[];
  const visMod=m=>!hidden.has(m.id) && (!focusSet || focusSet.has(m.id)) && (!violOnly || depViolAny(m.id) || pubViol(m.id));
  const depViolAny=mid=>A.violations.some(v=>(v.kind||'dependency')==='dependency'&&(v.from===mid||v.to===mid));
  // 光晕
  mods.forEach(m=>{ if(!visMod(m)) return; const c=centers.get(m.id); const r=collapsed.has(m.id)?collapseR(m):radii.get(m.id);
    ctx.beginPath(); ctx.arc(c.x,c.y,r,0,Math.PI*2); ctx.fillStyle=color(m.id)+'12'; ctx.fill();
    ctx.strokeStyle=color(m.id)+'44'; ctx.setLineDash(collapsed.has(m.id)?[4,4]:[]); ctx.lineWidth=1; ctx.stroke(); ctx.setLineDash([]);
    circles.push({x:c.x,y:c.y,r}); });
  // 模块标签（放在光晕上方，带底）
  mods.forEach(m=>{ if(!visMod(m)) return; const p=moduleLabelPos(m); const txt=m.id+' · '+m.files+(collapsed.has(m.id)?' ▸':'');
    ctx.font='12px system-ui'; const tw=ctx.measureText(txt).width;
    ctx.fillStyle='#0b0e14cc'; ctx.fillRect(p.x-tw/2-3,p.y-12,tw+6,15);
    ctx.fillStyle=color(m.id); ctx.textAlign='center'; ctx.fillText(txt,p.x,p.y); ctx.textAlign='left';
    labelRects.push({x:p.x-tw/2-3,y:p.y-12,w:tw+6,h:15}); });
  // 聚合边
  const agg=new Map();
  edges.forEach(([a,b])=>{ const fa=files.find(f=>f.path===a), fb=files.find(f=>f.path===b); if(!fa||!fb||!fa.module||!fb.module||fa.module===fb.module) return;
    const key=fa.module+'>'+fb.module; agg.set(key,(agg.get(key)||0)+1); });
  edgeGeom=[];
  agg.forEach((n,key)=>{ const [ma,mb]=key.split('>'); if(hidden.has(ma)||hidden.has(mb)) return;
    if(focusSet && !(focusSet.has(ma)&&focusSet.has(mb))) return;
    const bad=depViol(ma,mb); if(violOnly && !bad) return; const c1=centers.get(ma), c2=centers.get(mb);
    const hot=hoverEdge===key; const mx=(c1.x+c2.x)/2, my=(c1.y+c2.y)/2-34;
    edgeGeom.push({key,c1,c2,mx,my});
    ctx.strokeStyle=hot?'#58a6ff':(bad?'#f85149':color(ma)+'55');
    ctx.lineWidth=hot?3.2:Math.max(1,(1+Math.log2(1+n))*0.85); ctx.setLineDash(bad?[5,4]:[]);
    ctx.beginPath(); ctx.moveTo(c1.x,c1.y); ctx.quadraticCurveTo(mx,my,c2.x,c2.y); ctx.stroke(); ctx.setLineDash([]);
    if(view.k>0.65 || hot){ ctx.font='10px system-ui'; const txt='x'+n; const tw=ctx.measureText(txt).width;
      for(const dy of [0,-14,14,-28,28]){ const ly=my+dy; const lx=mx-tw/2;
        const badRect=circles.some(C=>Math.hypot(lx+tw/2-C.x,ly-C.y)<C.r+4) || labelRects.some(R=>lx<R.x+R.w&&lx+tw>R.x&&ly-9<R.y+R.h&&ly+3>R.y);
        if(!badRect){ ctx.fillStyle=hot?'#58a6ff':(bad?'#f85149':'#8b949e'); ctx.fillText(txt,lx,ly); labelRects.push({x:lx,y:ly-9,w:tw,h:12}); break; } } } });
  if(!crossOnly){ edges.forEach(([a,b])=>{ const fa=files.find(f=>f.path===a), fb=files.find(f=>f.path===b); if(!fa||!fb||fa.module!==fb.module) return;
    if(!visMod(mods.find(m=>m.id===fa.module))||!visMod(mods.find(m=>m.id===fb.module))) return;
    const P=nodePos(fa), Q=nodePos(fb); if(!P||!Q) return;
    ctx.strokeStyle='rgba(110,118,129,0.16)'; ctx.lineWidth=0.6/view.k; ctx.beginPath(); ctx.moveTo(P.x,P.y); ctx.lineTo(Q.x,Q.y); ctx.stroke(); }); }
  // 折叠模块：整块显示
  mods.forEach(m=>{ if(!visMod(m)||!collapsed.has(m.id)) return; const c=centers.get(m.id), r=collapseR(m);
    ctx.beginPath(); ctx.arc(c.x,c.y,r,0,Math.PI*2); ctx.fillStyle=color(m.id)+'66'; ctx.fill(); ctx.strokeStyle=color(m.id); ctx.lineWidth=1.5; ctx.stroke();
    ctx.fillStyle='#e6edf3'; ctx.font='11px system-ui'; ctx.textAlign='center'; ctx.fillText(m.id,c.x,c.y+4); ctx.textAlign='left'; });
  if(impact && impact.modules){ impact.modules.forEach(id=>{ const c=centers.get(id); if(!c) return; const r=(collapsed.has(id)?collapseR(mods.find(m=>m.id===id)):radii.get(id))||30;
    ctx.beginPath(); ctx.arc(c.x,c.y,r+6,0,Math.PI*2); ctx.strokeStyle='#f0b72f'; ctx.lineWidth=2; ctx.setLineDash([6,4]); ctx.stroke(); ctx.setLineDash([]); }); }
  // 文件节点
  const rank=[...files].sort((x,y)=>y.indeg-x.indeg); const top=new Set(rank.slice(0,14).map(f=>f.path));
  files.forEach(f=>{ if(collapsed.has(f.module)) return; const m=mods.find(x=>x.id===f.module); if(!m||!visMod(m)) return;
    const P=pos.get(f.path); if(!P) return; const r=nodeR(f); const on=sel&&sel.kind==='file'&&sel.id===f.path;
    ctx.beginPath(); ctx.arc(P.x,P.y,r,0,Math.PI*2); ctx.fillStyle=color(f.module); ctx.globalAlpha=on?1:.9; ctx.fill(); ctx.globalAlpha=1;
    if(on){ ctx.strokeStyle='#fff'; ctx.lineWidth=2; ctx.stroke(); }
    const show=on||hover===f.path||(search&&f.path.toLowerCase().includes(search))||(top.has(f.path)&&view.k>0.55)||view.k>1.5;
    if(show){ ctx.font='10px system-ui'; const txt=base(f.path), tw=ctx.measureText(txt).width, lx=P.x+r+3, ly=P.y-8, lw=tw+2, lh=12;
      const hitNode=circles.some(C=>Math.hypot(lx+tw/2-C.x,ly+6-C.y)<C.r+3);
      const hitCirc=files.some(g=>{ const Q=pos.get(g.path); return Q && g!==f && Math.hypot(lx+tw/2-Q.x,ly+6-Q.y)<nodeR(g)+3; });
      const hitRect=labelRects.some(R=>lx<R.x+R.w&&lx+lw>R.x&&ly<R.y+R.h&&ly+lh>R.y);
      if(!hitNode&&!hitCirc&&!hitRect){ labelRects.push({x:lx,y:ly,w:lw,h:lh}); ctx.fillStyle=on?'#fff':'#c9d1d9'; ctx.fillText(txt,lx,ly+9); } } });
  ctx.restore();
}

function nearestEdge(w){
  let best=null, bd=1e9;
  edgeGeom.forEach(g=>{ for(let i=0;i<=20;i++){ const t=i/20;
    const x=(1-t)*(1-t)*g.c1.x+2*(1-t)*t*g.mx+t*t*g.c2.x;
    const y=(1-t)*(1-t)*g.c1.y+2*(1-t)*t*g.my+t*t*g.c2.y;
    const d=Math.hypot(w.x-x,w.y-y); if(d<bd){ bd=d; best=g.key; } } });
  return bd < 10/view.k ? best : null;
}
function toggleFocus(){
  if(focusMod){ focusMod=null; focusSet=null; $('focusBtn').textContent='只看它的邻居'; fit(); return; }
  if(!sel||sel.kind!=='module') return;
  focusMod=sel.id; focusSet=new Set([sel.id]);
  A.edges.forEach(e=>{ if(e.from===focusMod) focusSet.add(e.to); if(e.to===focusMod) focusSet.add(e.from); });
  $('focusBtn').textContent='显示全部';
  fit();
}
function toWorld(ev){ const r=cv.getBoundingClientRect(); return {x:(ev.clientX-r.left-view.x)/view.k, y:(ev.clientY-r.top-view.y)/view.k}; }
function hit(w){
  for(const f of files){ if(collapsed.has(f.module)||hidden.has(f.module)) continue; const P=pos.get(f.path); if(!P) continue;
    if(Math.hypot(w.x-P.x,w.y-P.y)<=nodeR(f)+3) return {kind:'file',id:f.path}; }
  for(const m of mods){ if(hidden.has(m.id)) continue; const c=centers.get(m.id), r=collapsed.has(m.id)?collapseR(m):radii.get(m.id);
    if(Math.hypot(w.x-c.x,w.y-c.y)<=r) return {kind:'module',id:m.id}; }
  return null;
}
function zoomBy(f){ const mx=W/2,my=H/2; const k2=Math.min(6,Math.max(0.12,view.k*f)); const g=k2/view.k;
  view.x=mx-(mx-view.x)*g; view.y=my-(my-view.y)*g; view.k=k2; upZoom(); draw(); }
function upZoom(){ $('zoom').textContent=Math.round(view.k*100)+'%'; }
cv.addEventListener('wheel',(ev)=>{ ev.preventDefault(); const r=cv.getBoundingClientRect(); const mx=ev.clientX-r.left,my=ev.clientY-r.top;
  const k2=Math.min(6,Math.max(0.12,view.k*(ev.deltaY<0?1.12:0.89))), g=k2/view.k; view.x=mx-(mx-view.x)*g; view.y=my-(my-view.y)*g; view.k=k2; upZoom(); draw(); },{passive:false});
cv.addEventListener('pointerdown',(ev)=>{ moved=false; cv.setPointerCapture(ev.pointerId); drag={sx:ev.clientX-view.x, sy:ev.clientY-view.y}; cv.classList.add('drag'); });
cv.addEventListener('pointermove',(ev)=>{ if(drag){ moved=true; view.x=ev.clientX-drag.sx; view.y=ev.clientY-drag.sy; draw(); }
  else { const w=toWorld(ev); const h=hit(w); const id=h&&h.kind==='file'?h.id:null;
    const ekey=nearestEdge(w); const changed=(id!==hover)||(ekey!==hoverEdge);
    hover=id; hoverEdge=ekey;
    if(ekey){ const g=edgeGeom.find(g=>g.key===ekey); $('hint').textContent=ekey.replace('>',' → '); }
    else if($('hint').textContent) $('hint').textContent='';
    if(changed) draw(); } });
cv.addEventListener('pointerup',()=>{ drag=null; cv.classList.remove('drag'); });
cv.addEventListener('click',(ev)=>{ const h=hit(toWorld(ev)); if(!h) return; sel=h; draw(); show(h); });
cv.addEventListener('dblclick',(ev)=>{ const h=hit(toWorld(ev)); if(!h) return; if(h.kind!=='module') return;
  if(collapsed.has(h.id)) collapsed.delete(h.id); else collapsed.add(h.id); layout(); show(h); });
function collapseAll(){ mods.forEach(m=>collapsed.add(m.id)); layout(); }
function expandAll(){ collapsed.clear(); layout(); }
function show(h){
  $('focusBtn').style.display = h.kind==='module' ? 'inline-block' : 'none';
  if(h.kind==='module'){ const m=mods.find(x=>x.id===h.id);
    $('sel').innerHTML='<b>模块 '+m.id+'</b><br><span class="muted">'+m.files+' 文件 · 允许依赖</span> '+(m.allowed.join(', ')||'（无）')+
      (m.public&&m.public.length?'<br><span class="muted">公开入口</span> '+m.public.join(', '):'')+
      '<br><span class="muted">路径</span> '+m.paths.join(', '); }
  else { const f=files.find(x=>x.path===h.id);
    $('sel').innerHTML='<b>'+f.path+'</b><br><span class="muted">模块</span> '+f.module+' · <span class="muted">入度</span> '+f.indeg+' · <span class="muted">出度</span> '+f.outdeg; }
}
async function gen(){ if(!sel) return; const q=sel.kind==='module'?('module='+encodeURIComponent(sel.id)):('file='+encodeURIComponent(sel.id));
  const c=await api('/api/constraint?'+q); $('constraint').textContent=c.markdown||c.error||''; }
function copyC(){ const t=$('constraint').textContent; if(navigator.clipboard) navigator.clipboard.writeText(t); }
function writeScope(){ if(!sel) return;
  fetch('/api/scope',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:sel.kind,id:sel.id})})
   .then(r=>r.json()).then(d=>{ $('wrote').textContent=d.path?('已写入 '+d.path):('失败：'+(d.error||'')); }); }
function fit(){ if(!files.length||!centers.size) return; let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  mods.forEach(m=>{ if(!modVisible(m)) return; const c=centers.get(m.id), r=collapsed.has(m.id)?collapseR(m):radii.get(m.id);
    x0=Math.min(x0,c.x-r); y0=Math.min(y0,c.y-r); x1=Math.max(x1,c.x+r); y1=Math.max(y1,c.y+r); });
  files.forEach(f=>{ const m=mods.find(x=>x.id===f.module); if(!m||!modVisible(m)) return;
    const P=pos.get(f.path); if(!P) return; x0=Math.min(x0,P.x); y0=Math.min(y0,P.y); x1=Math.max(x1,P.x); y1=Math.max(y1,P.y); });
  const pad=36, k=Math.min((W-pad*2)/(x1-x0+1),(H-pad*2)/(y1-y0+1),1.8); view.k=Math.max(0.12,k);
  view.x=(W-(x1-x0)*view.k)/2-x0*view.k; view.y=(H-(y1-y0)*view.k)/2-y0*view.k; upZoom(); draw(); }
$('cross').onchange=(e)=>{ crossOnly=e.target.checked; draw(); };
$('violonly').onchange=(e)=>{ violOnly=e.target.checked; draw(); };
$('q').addEventListener('input',(e)=>{ search=e.target.value.trim().toLowerCase(); draw(); });
$('q').addEventListener('keydown',(e)=>{ if(e.key!=='Enter'||!search) return; const f=files.find(x=>x.path.toLowerCase().includes(search)); if(!f) return;
  sel={kind:'file',id:f.path}; show(sel); const P=pos.get(f.path); view.x=W/2-P.x*view.k; view.y=H/2-P.y*view.k; draw(); });
function chips(){
  $('chips').innerHTML=mods.map(m=>'<span class="chip'+(hidden.has(m.id)?' off':'')+(pick.has(m.id)?' pick':'')+'" title="点击隐藏/显示；Ctrl/Shift+点击加入多选" onclick="chipClick(\''+m.id+'\',event)"><i class="dot" style="background:'+color(m.id)+'"></i>'+m.id+'</span>').join(' ');
  $('pickbar').innerHTML = pick.size ? '<button onclick="applyPick()">只看所选（'+pick.size+'）</button><button onclick="clearPick()">清除</button>' : '';
}
function chipClick(id, ev){
  if(ev.ctrlKey || ev.metaKey || ev.shiftKey){ if(pick.has(id)) pick.delete(id); else pick.add(id); chips(); return; }
  toggleMod(id);
}
function applyPick(){ if(!pick.size) return; focusMod=null; focusSet=new Set(pick); fit(); }
function clearPick(){ pick.clear(); focusSet=null; focusMod=null; chips(); fit(); }
function toggleMod(id){ if(hidden.has(id)) hidden.delete(id); else hidden.add(id); chips(); draw(); }
function rows(el,data,cols){ el.innerHTML='<tr>'+cols.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+
  data.map(r=>'<tr>'+cols.map(c=>'<td class="'+(r._level||'')+'" title="'+(r[c]??'')+'">'+(r[c]??'')+'</td>').join('')+'</tr>').join(''); }
async function boot(){
  const s=await api('/api/scan'); $('root').textContent=s.root;
  A=await api('/api/arch'); mods=A.modules; files=A.files; edges=A.file_edges; fp=A.fingerprint;
  $('ver').textContent='v'+(A.panel_version||'');
  $('stat').textContent=mods.length+' 模块 / '+files.length+' 文件 / '+edges.length+' 边 / '+A.violations.length+' 违规  score '+s.score.total;
  resize(); layout(); chips();
  const qp=new URLSearchParams(location.search);
  const only=(qp.get('only')||'').split(',').map(x=>x.trim()).filter(Boolean).filter(x=>mods.some(m=>m.id===x));
  if(only.length){ focusSet=new Set(only); sel={kind:'module',id:only[0]}; show(sel); draw(); }
  const fs=(qp.get('focus')||'').split(',').map(x=>x.trim()).filter(Boolean).filter(x=>mods.some(m=>m.id===x));
  if(!only.length && fs.length){ focusSet=new Set(fs);
    A.edges.forEach(e=>{ if(fs.includes(e.from)) focusSet.add(e.to); if(fs.includes(e.to)) focusSet.add(e.from); });
    sel={kind:'module',id:fs[0]}; show(sel); draw(); }
  rows($('viol'), (A.violations||[]).map(v=>({_level:'fail',kind:v.kind||'dependency',from:v.from,to:v.to,file:v.file||'',count:v.count})), ['kind','from','to','count']);
  rows($('issues'), (s.issues||[]).map(i=>({_level:i.level==='FAIL'?'fail':'warn',level:i.level,code:i.code,path:i.path})), ['level','code','path']);
  setInterval(poll, 2500);
}
async function poll(){ if(!$('auto').checked) return; try{ const r=await api('/api/arch');
  if(r.fingerprint && r.fingerprint!==fp){ fp=r.fingerprint; A=r; mods=A.modules; files=A.files; edges=A.file_edges; layout(); chips();
    rows($('viol'), (A.violations||[]).map(v=>({_level:'fail',kind:v.kind||'dependency',from:v.from,to:v.to,count:v.count})), ['kind','from','to','count']);
    if(sel) show(sel); } }catch(e){} }
async function showImpact(){ const r=await api('/api/impact');
  if(r.error){ $('impact').textContent='影响分析失败：'+r.error; return; }
  impact=r; draw();
  $('impact').textContent='变更文件 '+r.changed.length+' / 模块 '+(r.modules.join(', ')||'无')+' / 上游 '+(r.upstream.join(', ')||'无')+' / 下游 '+(r.downstream.join(', ')||'无'); }
boot();
</script></body></html>"""


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
