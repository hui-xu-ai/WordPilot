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

kernel = Kernel()
kernel.boot()
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

# 左列 / 右列判定：卡片横向位置
xs = sorted({c.geometry().x() for c in cards})
print("不同列起点 x:", xs)
assert len(xs) == 2, f"期望两列布局，实际列起点 {xs}"

# 底部不越界
for c in cards:
    bottom = c.geometry().y() + c.geometry().height()
    print("  底部 y:", bottom, "<= 视口", dash.height())
    assert bottom <= dash.height() + 2, "卡片超出可视区域"

print("\nLAYOUT OK")
