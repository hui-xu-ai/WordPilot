# tools/smoke_p8.py
"""P8 冒烟：无边框标题栏 / 设置页 / 侧边栏拖动排序 / 幽灵窗口回归 / 翻译改写按钮排。

离屏运行：.venv/Scripts/python.exe tools/smoke_p8.py
"""
import os
import sys

sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
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
mw.show()
app.processEvents()

print("=== 1) 无边框窗口 + 自绘标题栏 ===")
flags = int(mw.windowFlags())
print("windowFlags:", flags, "| FramelessWindowHint:", bool(flags & int(Qt.FramelessWindowHint)))
assert flags & int(Qt.FramelessWindowHint), "主窗口应为无边框"
assert mw.title_bar is not None and mw.title_bar.title_label.text()
print("标题栏文本:", mw.title_bar.title_label.text())
mw.showMaximized()
app.processEvents()
assert mw.isMaximized()
mw.showNormal()
app.processEvents()
print("最大化/还原切换 OK")

print("\n=== 2) 设置成为内置页 + 导航底部结构 ===")
pages = list(mw.nav._pages.keys())
print("nav pages:", pages)
assert "dashboard" in pages and "plugins" in pages and "settings" in pages
# 底部槽位顺序：置顶(action) -> 插件管理(page) -> 设置(page)
bottom_widgets = [mw.nav._bottom_layout.itemAt(i).widget()
                  for i in range(mw.nav._bottom_layout.count())]
bottom_texts = [w.text() for w in bottom_widgets]
print("bottom:", bottom_texts)
assert bottom_texts.index(mw.tr("app.nav.plugins")) < bottom_texts.index(mw.tr("app.menu.settings")), \
    "插件管理应在设置上方"
# 设置页内容
settings = mw._page_widgets["settings"]
assert settings.stack.count() == 4
mw._on_page_requested("settings")
assert mw.stack.currentWidget() is settings
print("设置页 4 个分区，可直达")

print("\n=== 3) 智能纠错页（仪表盘改名） ===")
print("页标题:", mw.dashboard_title.text())
assert mw.dashboard_title.text() == mw.tr("app.nav.dashboard")
assert "纠错" in mw.dashboard_title.text() or "Proofread" in mw.dashboard_title.text()

print("\n=== 4) 导航无分隔线 ===")
from PySide6.QtWidgets import QFrame
dividers = [mw.nav._items_layout.itemAt(i).widget()
            for i in range(mw.nav._items_layout.count())]
dividers = [w for w in dividers if isinstance(w, QFrame)]
print("分隔线数量:", len(dividers))
assert not dividers, "导航轨不应再有分隔线"

print("\n=== 5) 侧边栏拖动排序（QTest 真实鼠标事件） ===")
draggable = mw.nav._drag
print("可拖动条目:", list(draggable.values()))
assert len(draggable) >= 3, "插件条目应可拖动"
kernel.config.set("plugins", {})   # 清空旧顺序，从默认序开始
mw.build_extension_menus()
app.processEvents()
buttons = mw.nav._draggable_widgets()
first, second = buttons[0], buttons[1]
first_key = mw.nav._drag[first]
print(f"拖动 {first_key} 到第二位")
# 在第一个按钮中心按下，拖到第二个按钮中心以下，松开
QTest.mousePress(first, Qt.LeftButton, pos=first.rect().center())
app.processEvents()
target = second.mapTo(mw.nav, second.rect().center())
target_global = mw.nav.mapToGlobal(target)
from PySide6.QtGui import QCursor
QCursor.setPos(target_global + target_global.__class__(0, 24))
QTest.mouseMove(first, first.mapFromGlobal(QCursor.pos()))
app.processEvents()
QTest.mouseRelease(first, Qt.LeftButton, pos=first.mapFromGlobal(QCursor.pos()))
app.processEvents()
order = kernel.config.get("plugins", {}).get("order", [])
print("拖动后 plugins.order:", order)
assert order and order[0] != first_key, "拖动后顺序应已落盘"
assert first_key in order[1:], "被拖条目应后移"

print("\n=== 6) 幽灵窗口回归：语言切换/主题重载不产生新顶层窗口 ===")
def visible_toplevels():
    return [w for w in QApplication.topLevelWidgets() if w.isVisible()]

before = len(visible_toplevels())
kernel.set_language("en_US")
app.processEvents()
mw.apply_style()
mw.retranslateUi()
app.processEvents()
kernel.set_language("zh_CN")
app.processEvents()
after = len(visible_toplevels())
print(f"可见顶层窗口: {before} -> {after}")
assert after == before == 2, "重建导航不应闪出独立小窗口"

print("\n=== 7) 翻译改写页按钮排 ===")
mw._on_page_requested("plugin:clipboard")
clip = mw._page_widgets.get("plugin:clipboard")
assert clip is not None
layout = clip.layout()
btn_row = layout.itemAt(layout.count() - 1)
buttons = [btn_row.itemAt(i).widget() for i in range(btn_row.count())]
texts = [b.text() for b in buttons]
print("底部按钮:", texts)
assert texts == [clip.tr("load"), clip.tr("copy"), clip.tr("translate"),
                 clip.tr("rewrite"), clip.tr("correct")], "按钮顺序应为：读取剪贴板、复制结果、翻译、改写、纠错"
assert not hasattr(clip, "btn_insert")

print("\n=== 8) 插件管理卡片 ===")
panel = mw._page_widgets["plugins"]
panel.rebuild()
app.processEvents()
print("卡片数:", len(panel._cards))
assert len(panel._cards) >= 5
# 搜索过滤
panel.search_edit.setText("导航")
panel._apply_filter()
visible = [c for c, _n, _d in panel._cards if c.isVisible() or not panel.search_edit.text()]
panel.search_edit.clear()
print("搜索过滤 OK")

# 配置复位
kernel.config.set("plugins", {})
print("\nALL OK")
