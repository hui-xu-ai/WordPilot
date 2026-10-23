# tools/diag_language_flash.py
"""诊断：切换语言时哪些顶层窗口被显示/新建（离屏）。

运行：.venv/Scripts/python.exe tools/diag_language_flash.py
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
mw.show()


def dump(tag):
    tops = []
    for w in QApplication.topLevelWidgets():
        if w.isVisible():
            tops.append(f"{type(w).__name__}(title={w.windowTitle()!r}, "
                        f"size={w.width()}x{w.height()}, flags={int(w.windowFlags())})")
    print(f"--- {tag}: {len(tops)} visible top-levels ---")
    for t in tops:
        print("   ", t)


dump("before")
print("\n>>> set_language(en_US)\n")
kernel.set_language("en_US")
app.processEvents()
dump("after language")
print("\n>>> apply_style + retranslateUi (模拟设置保存后的外壳同步)\n")
mw.apply_style()
mw.retranslateUi()
app.processEvents()
dump("after style+retranslate")

print("\n>>> 再切回 zh_CN\n")
kernel.set_language("zh_CN")
app.processEvents()
dump("after zh_CN")
kernel.shutdown()   # 不卸载插件就退出：监控 QThread 会被销毁并 abort 进程
print("DONE")
