# tools/smoke_p8.py
"""P8/P9 冒烟：无边框标题栏 / 设置内置页 / 智能纠错解耦 /
侧边栏拖动排序 / 幽灵窗口回归 / 翻译改写按钮排 / SpinBox 步进箭头。

离屏运行：.venv/Scripts/python.exe tools/smoke_p8.py

注意：脚本用「快照 + 收尾恢复」保护本机 plugins 配置，
避免冒烟运行污染用户真实的插件启停与排序。
"""
import os
import sys

sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

app = QApplication([])

from app.main_window import MainWindow
from core.kernel import Kernel

kernel = Kernel()
kernel.boot()

# plugins 配置快照：脚本结束时原样恢复，避免污染用户真实启停与排序
_plugins_snapshot = kernel.config.get("plugins", {})
# 冒烟基线：临时清掉 disabled/order，让默认插件（含 proofread）全部加载，
# 否则本机若关着核心插件就测不到「智能纠错」页。
_baseline = dict(_plugins_snapshot)
_baseline.pop("disabled", None)
_baseline.pop("order", None)
kernel.config.set("plugins", _baseline)

mw = MainWindow(kernel)
kernel.attach_ui(mw.ui)
kernel.plugins.load_all()
mw.bind_plugins()
mw.show()
app.processEvents()


def _clear_order() -> None:
    """只清侧边栏顺序，保留用户的插件启停状态。"""
    cfg = dict(kernel.config.get("plugins", {}) or {})
    cfg.pop("order", None)
    kernel.config.set("plugins", cfg)


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
assert "plugin:proofread" in pages, "智能纠错应由 proofread 插件注册，而非外壳内置"
assert "plugins" in pages and "settings" in pages
assert pages[0] == "plugin:proofread", "智能纠错应排在导航最前"
# 底部槽位顺序：置顶(action) -> 插件管理(page) -> 设置(page)
bottom_widgets = [mw.nav._bottom_layout.itemAt(i).widget()
                  for i in range(mw.nav._bottom_layout.count())]
bottom_texts = [w.text() for w in bottom_widgets]
print("bottom:", bottom_texts)
assert bottom_texts.index(mw.tr("app.nav.plugins")) < bottom_texts.index(mw.tr("app.menu.settings")), \
    "插件管理应在设置上方"
# 设置页内容（API / 提示词 / 界面 / 外观 / 关于）
settings = mw._page_widgets["settings"]
assert settings.stack.count() == 4, f"设置页应有 4 个分区（API/提示词/外观/关于），实际 {settings.stack.count()}"
from __version__ import APP_NAME as _APP_NAME, __version__ as _VERSION  # noqa: E402

assert settings.about_name_value.text() == _APP_NAME, "关于页软件名应与 __version__.py 一致"
assert settings.about_version_value.text() == _VERSION, "关于页版本号应与 __version__.py 一致"
mw._on_page_requested("settings")
assert mw.stack.currentWidget() is settings
print("设置页 5 个分区（含关于），可直达")

print("\n=== 3) 智能纠错页：由插件提供、无重复页头 ===")
print("factories:", [(p, built) for p, (f, built) in mw._plugin_factories.items()])
print("built widgets:", list(mw._page_widgets.keys()))
dash = mw._page_widgets.get("plugin:proofread")
assert dash is not None, "proofread 插件应注册智能纠错页"
assert not hasattr(dash, "dashboard_title"), "页面内不应再有与侧边栏重复的大标题"
assert dash.proof_switch is not None and dash.monitor_widget is not None
assert dash.status_text.text(), "状态文字应已填充"
print("状态:", dash.status_text.text(), "| 卡片:", dash.status_card_title.text())
proof_cap = kernel.capabilities.get("proofread")
assert dash.proof_switch.isChecked() == proof_cap.is_enabled(), "开关应与插件状态一致"

print("\n=== 4) 导航无分隔线 ===")
dividers = [mw.nav._items_layout.itemAt(i).widget()
            for i in range(mw.nav._items_layout.count())]
dividers = [w for w in dividers if isinstance(w, QFrame)]
print("分隔线数量:", len(dividers))
assert not dividers, "导航轨不应再有分隔线"

print("\n=== 5) 侧边栏拖动排序（QTest 真实鼠标事件） ===")
draggable = mw.nav._drag
print("可拖动条目:", list(draggable.values()))
assert len(draggable) >= 3, "插件条目应可拖动"
_clear_order()
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

print("\n=== 8) 插件管理卡片 + 禁用联动 ===")
panel = mw._page_widgets["plugins"]
panel.rebuild()
app.processEvents()
print("卡片数:", len(panel._cards))
assert len(panel._cards) >= 5
# 搜索过滤
panel.search_edit.setText("导航")
panel._apply_filter()
panel.search_edit.clear()
print("搜索过滤 OK")

# 禁用 proofread -> 侧边栏「智能纠错」应随之消失
kernel.plugins.set_enabled("proofread", False)
app.processEvents()
pages_off = list(mw.nav._pages.keys())
print("禁用 proofread 后 nav pages:", pages_off)
assert "plugin:proofread" not in pages_off, "禁用插件后侧边栏入口应消失"
# 重新启用 -> 入口回来
kernel.plugins.set_enabled("proofread", True)
app.processEvents()
pages_on = list(mw.nav._pages.keys())
print("重新启用后 nav pages:", pages_on)
assert "plugin:proofread" in pages_on, "重新启用后侧边栏入口应恢复"

print("\n=== 9) SpinBox 步进箭头可见 ===")
theme = kernel.services.get("theme")
if theme is not None:
    style = theme.render()
    print("含 spin-up.svg:", "spin-up.svg" in style,
          "| 含 spin-down.svg:", "spin-down.svg" in style)
    assert "spin-up.svg" in style and "spin-down.svg" in style, \
        "SpinBox 箭头必须引用图标文件，否则被全局样式吃掉后看不见"

# 配置复位：恢复运行前的 plugins 配置
kernel.config.set("plugins", _plugins_snapshot)
kernel.shutdown()   # 不卸载插件就退出：监控 QThread 会被销毁并 abort 进程
print("\nALL OK")
