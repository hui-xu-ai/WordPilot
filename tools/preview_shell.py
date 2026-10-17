"""把新主界面各页面渲染成预览图，用于目视检查布局。

离屏运行：  .venv\\Scripts\\python.exe tools\\preview_shell.py
输出：      work/shell_preview_*.png
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication      # noqa: E402

app = QApplication([])                           # noqa: F401

from core.kernel import Kernel                  # noqa: E402
from app.main_window import MainWindow          # noqa: E402

k = Kernel()
k.boot()
mw = MainWindow(k)
k.attach_ui(mw.ui)
k.plugins.load_all()
mw.bind_plugins()

theme = k.services.get("theme")
theme.set_apply_hook(app.setStyleSheet)
theme.reload()

mw.resize(860, 560)
mw.show()
app.processEvents()

out_dir = os.path.join(ROOT, "work")
os.makedirs(out_dir, exist_ok=True)

for name in ("dashboard", "clipboard", "plugins", "monitor"):
    mw.show_page(name)
    app.processEvents()
    path = os.path.join(out_dir, f"shell_preview_{name}.png")
    mw.grab().save(path)
    print("已输出:", path)

# 深色主题对照
k.set_theme("dark")
app.processEvents()
mw.show_page("dashboard")
app.processEvents()
mw.grab().save(os.path.join(out_dir, "shell_preview_dark.png"))
print("已输出:", os.path.join(out_dir, "shell_preview_dark.png"))

k.set_theme("light")
k.shutdown()
