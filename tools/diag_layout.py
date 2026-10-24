# tools/diag_layout.py
"""布局体检：打印智能纠错页各卡片的几何，确认两列布局生效、内容不被裁切。

离屏运行：.venv/Scripts/python.exe tools/diag_layout.py
"""
import os
import sys

sys.path.insert(0, "src")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

app = QApplication([])

from app.main_window import MainWindow
from core.kernel import Kernel
from _config_guard import guard

kernel = Kernel()
kernel.boot()
# 诊断需要"全部插件都加载"的基线，但结束后必须恢复用户原值（不能只 pop 掉 disabled 就跑）
restore_config = guard(kernel.config, ["plugins"])
cfg = dict(kernel.config.get("plugins", {}) or {})
cfg.pop("disabled", None)      # 冒烟基线：加载全部默认插件
kernel.config.set("plugins", cfg)
mw = MainWindow(kernel)
kernel.attach_ui(mw.ui)
kernel.plugins.load_all()
mw.bind_plugins()
mw.resize(1100, 700)
mw.show()
app.processEvents()

dash = mw._page_widgets.get("plugin:proofread")
assert dash is not None, "智能纠错页未构造"

print("页面尺寸:", dash.width(), "x", dash.height())
cards = [w for w in dash.findChildren(QWidget) if w.objectName() == "contentCard"]
print("卡片数:", len(cards))
for c in cards:
    g = c.geometry()
    print(f"  卡片 pos=({g.x()},{g.y()}) size={g.width()}x{g.height()}")

# 布局判定：主区单列上下排（状态卡在上、API 监控在下），不再有页面级长滚动条
xs = sorted({c.geometry().x() for c in cards})
print("不同列起点 x:", xs)
assert len(xs) == 1, f"主区应为单列上下排（状态在上、监控在下），实际列起点 {xs}"
assert len(cards) >= 2, f"状态卡与监控卡都应在主区，实际 {len(cards)} 张"
from PySide6.QtWidgets import QScrollArea  # noqa: E402

assert dash.findChild(QScrollArea) is None, "页面级不应再套 QScrollArea（长滚动条）"

print("=== 右侧抽屉 ===")
panel = dash.build_secondary_panel()
wanted_open = bool(kernel.config.get("ui.side_panel.open", False))
print("次要面板:", type(panel).__name__, "| 抽屉可见:", not mw.side_panel.isHidden(),
      "| 展开:", mw.side_panel.is_open(), "| 配置期望:", wanted_open)
assert panel is not None, "智能纠错必须提供次要界面（校对参数/缓存清洗）"
assert mw.side_panel.is_open() is wanted_open, "抽屉展开状态必须与配置一致（用户偏好要记住）"

# 底部不越界
for c in cards:
    bottom = c.geometry().y() + c.geometry().height()
    print("  底部 y:", bottom, "<= 视口", dash.height())
    assert bottom <= dash.height() + 2, "卡片超出可视区域"

restore_config()
kernel.shutdown()

print("\nLAYOUT OK")
