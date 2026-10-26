# tools/smoke_p7.py
"""P7 冒烟：仪表盘合并 / 插件管理启停排序 / 桌宠表情状态机 / 翻译改写插件。

离屏运行：.venv/Scripts/python.exe tools/smoke_p7.py
"""
import os
import sys

sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication([])

from core.kernel import Kernel
from app.main_window import MainWindow
from _config_guard import guard

kernel = Kernel()
kernel.boot()
# 本脚本会改 plugins / ui_language：只快照恢复，绝不 set("plugins", {}) 清空用户配置
restore_config = guard(kernel.config, ["plugins", "ui_language"])
mw = MainWindow(kernel)
kernel.attach_ui(mw.ui)
kernel.plugins.load_all()
mw.bind_plugins()

print("=== 1) 骨架：无工具栏、智能纠错由插件注册 ===")
assert not hasattr(mw, "toolbar") or mw.findChild(object) is None
pages = list(mw.nav._pages.keys())
print("nav pages:", pages)
assert "monitor" not in pages, "监控页应已并入智能纠错页"
assert "dashboard" not in pages, "外壳不应再有内置的 dashboard 页"
assert "plugin:proofread" in pages and "plugins" in pages
# 智能纠错页内部自带监控面板
dash = mw._page_widgets.get("plugin:proofread")
assert dash is not None and dash.monitor_widget is not None

print("\n=== 2) 智能纠错页控件 ===")
print("proof switch:", dash.proof_switch.isCheckable(), "| cleanup btns:",
      dash.cleanup_ignore_btn.text() and dash.cleanup_log_btn.text()
      and dash.cleanup_all_btn.text())

print("\n=== 3) 插件管理：禁用/启用/排序 ===")
pm = kernel.plugins
print("初始启用:", [r.manifest.id for r in pm.all() if r.state == "active"])
# navigator 已改成主界面内容页（P10 后续），不再是"动作型工具入口"
pm.set_enabled("navigator", False)
pages_after = list(mw.nav._pages.keys())
print("禁用 navigator 后 nav pages:", pages_after)
assert "plugin:navigator" not in pages_after, "禁用后导航页应从上轨消失"
pm.set_enabled("navigator", True)
pages_back = list(mw.nav._pages.keys())
print("重新启用后 nav pages:", pages_back)
assert "plugin:navigator" in pages_back, "启用后导航页应回到上轨"
pm.set_order(["image_toolbox", "clipboard", "navigator", "proofread"])
print("plugins.order:", kernel.config.get("plugins", {}).get("order"))
pm.set_enabled("navigator", True)  # 保持配置干净

print("\n=== 4) 插件管理面板 ===")
panel = mw._page_widgets.get("plugins")
assert panel is not None
print("面板卡片数:", len(panel._cards))

print("\n=== 5) 桌宠表情状态机 ===")
proof = kernel.capabilities.get("proofread")
pet = proof.floating_btn
from core.services.skin_service import SkinService
skins = kernel.services.get("skins")
assert skins.eye_variant("idle") == "sleep", "idle 应为闭眼打盹"
assert skins.eye_variant("error") == "alert"
proof.set_state("idle")
idle_mouth = skins.part_svg("mouth", "idle")
print("idle  -> eyes:", skins.eye_variant("idle"), "| mouth svg len:", len(idle_mouth))
# 真断言：idle 必须产出嘴型，且与 ok 是不同部件（原写法 `... or True` 恒真，等于没测）
assert idle_mouth.strip(), "idle 应产出嘴型 SVG"
assert idle_mouth != skins.part_svg("mouth", "ok"), "idle 与 ok 不应共用同一嘴型"
proof._show_ok()
print("ok    ->", proof.state(), "| eyes:", skins.eye_variant(proof.state()))
import time
app.processEvents()
time.sleep(2.2)
app.processEvents()
print("2.2s 后 ->", proof.state())
assert proof.state() == "idle", "ok 表情应 2 秒后回落 idle"
proof.set_state("error")
pet.bubbleToggled.emit(False)   # 模拟用户在气泡上点了确认/忽略
print("error + 气泡关闭 ->", proof.state())
assert proof.state() == "idle", "用户决策后应回落 idle"

print("\n=== 6) 桌宠插件菜单含页面直达 ===")
entries = proof._plugin_tools()
print("pet menu entries:", [e[0] for e in entries])
assert any("翻译改写" in e[0] for e in entries), "插件页应上桌宠菜单"
assert any("导航" in e[0] for e in entries), "窗口型插件应在桌宠菜单"

print("\n=== 7) 翻译改写插件 ===")
labels = [p[0] for p in mw.ui.pages()]
print("plugin pages:", labels)
assert any("翻译改写" in l for l in labels)
clip = None
for pid, w in mw._page_widgets.items():
    if type(w).__name__ == "ClipboardWidget":
        clip = w
mw._on_page_requested("plugin:clipboard")
clip = mw._page_widgets.get("plugin:clipboard")
print("clipboard page:", type(clip).__name__ if clip else "未构造")
assert clip is not None, "翻译改写页应能构造"
# P8：纠错恢复为页面三操作之一；「插入 Word」已移除，按钮全部沉底
assert hasattr(clip, "correct_btn"), "P8 起恢复纠错按钮"
assert not hasattr(clip, "btn_insert"), "插入 Word 按钮应已移除"

# 配置复位（恢复快照，不是清空）+ 卸载插件
# 不 shutdown 就退出会让 proofread 的监控 QThread 在解释器收尾时被销毁（"QThread: Destroyed while
# thread is still running" 直接 abort 整个进程），冒烟会被判成崩溃而不是 PASS。
restore_config()
kernel.shutdown()
print("\nALL OK")
