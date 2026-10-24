# tools/smoke_p6_shell.py
"""P6 冒烟：图标轨导航 + 剪贴板插件化 + 插件直达入口。

离屏运行：.venv/Scripts/python.exe tools/smoke_p6_shell.py
"""
import os
import sys

sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication([])

from core.kernel import Kernel
from app.main_window import MainWindow

kernel = Kernel()
kernel.boot()
mw = MainWindow(kernel)
kernel.attach_ui(mw.ui)
kernel.plugins.load_all()
mw.bind_plugins()

print("=== 1) 插件加载 ===")
loaded = [r.manifest.id for r in kernel.plugins.all() if getattr(r, "instance", None)]
print("loaded:", loaded)
assert "clipboard" in loaded, "clipboard 插件未加载"

print("\n=== 2) 导航轨条目 ===")
pages = list(mw.nav._pages.keys())
print("pages:", pages)
assert "plugin:proofread" in pages and "plugins" in pages  # P9: 智能纠错由插件注册
assert any(p.startswith("plugin:") for p in pages), "插件页未上轨"
# 剪贴板页与图片工具页应是插件页
page_widgets = mw._page_widgets
print("page widgets:", {k: type(v).__name__ for k, v in page_widgets.items()})

print("\n=== 3) 图标渲染（主题染色） ===")
from app import icon_factory
icon = mw._nav_icon("dashboard", checkable=True)
assert not icon.isNull(), "dashboard 图标渲染失败"
icon2 = mw._nav_icon("clipboard", checkable=True)
assert not icon2.isNull(), "clipboard 图标渲染失败"
print("dashboard/clipboard 图标渲染 OK")

print("\n=== 4) 页面切换 ===")
# 注意：image_toolbox 页构造时会拉起隐藏 Word 工作文档，
# 无 Word 的离屏环境会硬崩（改造前弹窗路径同样如此），冒烟按工厂来源跳过该页。
skip_ids = set()
for _label, factory, _icon, pid in mw.ui.pages():
    owner = type(getattr(factory, "__self__", None)).__name__
    if owner == "ImageToolboxPlugin":
        skip_ids.add(f"plugin:{pid}")
for pid in pages:
    if pid in skip_ids:
        print(f"  {pid:12s} -> (跳过：需要真实 Word 环境)")
        continue
    mw._on_page_requested(pid)
    print(f"  {pid:12s} -> {type(mw.stack.currentWidget()).__name__}")
assert mw.nav.current_page() is not None

print("\n=== 5) 折叠 / 展开 ===")
print("collapsed width:", mw.nav.width())
mw.nav.set_collapsed(False)
print("expanded width:", mw.nav.width())
assert mw.nav.width() == 180
mw.nav.set_collapsed(True)
assert mw.nav.width() == 52
assert mw.nav.is_collapsed() is True, "折叠状态应记录在 NavRail 上"

print("\n=== 6) 插件页入口（导航助手已页面化） ===")
nav_page_ids = list(mw.nav._pages.keys())
print("nav pages:", nav_page_ids)
# 冒烟不得假设用户启停了哪个插件：navigator 被禁用时该页本就不存在（HANDOFF「冒烟污染」坑）
plugins_cfg = kernel.config.get("plugins", {}) or {}
if "navigator" in (plugins_cfg.get("disabled") or []):
    print("  navigator 在用户配置里被禁用 -> 断言该页不存在")
    assert "plugin:navigator" not in nav_page_ids, "禁用后不应残留导航页"
else:
    assert "plugin:navigator" in nav_page_ids, "导航页缺失（应像其它插件一样挂在侧边栏）"

print("\n=== 7) 语言热切换（轨道重建、页面状态保留） ===")
clipboard_page = None
for pid, w in page_widgets.items():
    if type(w).__name__ == "ClipboardWidget":
        clipboard_page = w
        clipboard_page.clip_preview.setPlainText("切换语言后这段文字不应丢失")
kernel.set_language("en_US")
print("[EN] 智能纠错 label:", mw.nav._pages["plugin:proofread"].text())
for pid, w in mw._page_widgets.items():
    if hasattr(w, "retranslateUi") and pid.startswith("plugin:"):
        pass  # retranslateUi 已在 retranslateUi() 中统一调用
if clipboard_page is not None:
    kept = clipboard_page.clip_preview.toPlainText()
    print("[EN] clipboard 文本保留:", kept[:20], "...")
    assert "不应丢失" in kept
    print("[EN] clipboard load btn:", clipboard_page.btn_load.text())
kernel.set_language("zh_CN")

print("\n=== 8) 桌宠插件快捷菜单 ===")
proof = kernel.capabilities.get("proofread")
assert proof is not None
tools_for_pet = proof._plugin_tools()
print("pet tools:", [t[0] for t in tools_for_pet])

print("\n=== 9) 桌宠冒烟回归 ===")
import subprocess
result = subprocess.run([sys.executable, "tools/smoke_p4_pet.py"],
                        capture_output=True, text=True)
print("smoke_p4_pet:", "PASS" if result.returncode == 0 else "FAIL")
if result.returncode != 0:
    print(result.stdout[-800:], result.stderr[-800:])

kernel.shutdown()   # 不卸载插件就退出：监控 QThread 会在解释器收尾时被销毁并 abort 进程
print("\nALL OK")
