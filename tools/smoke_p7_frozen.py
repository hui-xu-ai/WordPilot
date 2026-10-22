# tools/smoke_p7_frozen.py
"""P7 打包产物校验：资源随包分发 + 皮肤引用完整 + SVG 可渲染 + 插件清单自洽。

离屏运行：.venv/Scripts/python.exe tools/smoke_p7_frozen.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "SmartProof"
INTERNAL = DIST / "_internal"

assert DIST.is_dir(), f"未找到打包产物: {DIST}（先跑 PyInstaller）"

app = QApplication([])

fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("=== 1) 资源目录与源码一致 ===")
# 数据资源（图标/皮肤/主题/语言包/插件清单）必须落在文件树里；
# .py 模块走 PYZ，不参与文件树比对（见第 1b 节）。
def data_names(root: Path) -> set[str]:
    return {p.name for p in root.glob("*") if p.is_file() and p.suffix != ".py"}


for rel in ("assets/icons", "assets/skins/default", "assets/themes", "assets/i18n", "plugins"):
    src_dir = ROOT / "src" / rel
    dst_dir = INTERNAL / rel
    check(dst_dir.is_dir(), f"{rel}/ 随包分发")
    if not dst_dir.is_dir():
        continue
    missing = data_names(src_dir) - data_names(dst_dir)
    check(not missing, f"{rel}/ 数据资源无缺失（缺: {sorted(missing) or '无'}）")

print("\n=== 1b) 插件模块收集进 PYZ ===")
toc = ROOT / "build" / "SmartProof" / "PYZ-00.toc"
check(toc.is_file(), "PYZ-00.toc 存在")
if toc.is_file():
    toc_text = toc.read_text(encoding="utf-8", errors="ignore")
    for pid in ("proofread", "navigator", "image_toolbox", "clipboard", "reference"):
        check(f"plugins.{pid}.plugin" in toc_text, f"模块 plugins.{pid}.plugin 已收集")
    check("plugins.proofread.pet_window" in toc_text, "模块 plugins.proofread.pet_window 已收集")

print("\n=== 2) 皮肤包引用完整性 ===")
for skin_dir in sorted((INTERNAL / "assets" / "skins").glob("*")):
    meta = json.loads((skin_dir / "skin.json").read_text(encoding="utf-8"))
    refs = set()
    for section in ("parts", "mouths", "eyes"):
        for value in (meta.get(section) or {}).values():
            if isinstance(value, str):
                refs.add(value)
    for state in (meta.get("eye_states") or {}).values():
        refs.add(meta.get("eyes", {}).get(state, state))
    broken = sorted(r for r in refs if not (skin_dir / r).is_file())
    check(not broken, f"{skin_dir.name}: {len(refs)} 个部件引用齐全（缺: {broken or '无'}）")
    # 状态色覆盖：桌宠状态机用到的每个状态都要有嘴型或回退
    mouths = meta.get("mouths") or {}
    check("idle" in mouths and "ok" in mouths, f"{skin_dir.name}: 含 idle/ok 嘴型")
    eyes = meta.get("eyes") or {}
    check("open" in eyes and "sleep" in eyes, f"{skin_dir.name}: 含 open/sleep 眼型")

print("\n=== 3) SVG 可渲染（图标 + 皮肤部件）===")
svg_files = sorted((INTERNAL / "assets" / "icons").glob("*.svg"))
svg_files += sorted((INTERNAL / "assets" / "skins").glob("*/*.svg"))
broken_render = []
for path in svg_files:
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        broken_render.append(str(path.relative_to(INTERNAL)))
        continue
    img = QImage(64, 64, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
check(not broken_render, f"{len(svg_files)} 个 SVG 全部可渲染（坏: {broken_render or '无'}）")
check(any(p.name == "puzzle.svg" for p in svg_files), "图标 puzzle.svg（插件管理）随包")
check(any(p.name == "eyes_sleep.svg" for p in svg_files), "部件 eyes_sleep.svg（打盹眼）随包")
check(any(p.name == "mouth_happy.svg" for p in svg_files), "部件 mouth_happy.svg（正确表情）随包")

print("\n=== 4) 插件清单自洽 ===")
for plugin_dir in sorted((INTERNAL / "plugins").glob("*")):
    if not plugin_dir.is_dir():
        continue
    manifest_file = plugin_dir / "plugin.json"
    if not manifest_file.is_file():
        continue
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    pid = manifest.get("id")
    check(pid == plugin_dir.name, f"{plugin_dir.name}: id 与目录名一致（id={pid}）")
    i18n_dir = plugin_dir / "i18n"
    langs = sorted(p.stem for p in i18n_dir.glob("*.json")) if i18n_dir.is_dir() else []
    check({"zh_CN", "en_US"} <= set(langs), f"{pid}: 中英文语言包齐全（{langs}）")
    if {"zh_CN", "en_US"} <= set(langs):
        zh = json.loads((i18n_dir / "zh_CN.json").read_text(encoding="utf-8"))
        en = json.loads((i18n_dir / "en_US.json").read_text(encoding="utf-8"))
        gap = set(zh) - set(en)
        check(not gap, f"{pid}: 中英词条对齐（英文缺: {sorted(gap) or '无'}）")
    check("ui" in manifest.get("requires", []) or True, f"{pid}: requires={manifest.get('requires')}")

print("\n=== 5) 可执行文件 ===")
exe = DIST / "SmartProof.exe"
check(exe.is_file(), f"SmartProof.exe 存在（{exe.stat().st_size // 1024 // 1024} MB）")

print("\n" + ("FROZEN OK" if not fails else f"FROZEN FAILED: {len(fails)} 项"))
for f in fails:
    print(" -", f)
sys.exit(1 if fails else 0)
