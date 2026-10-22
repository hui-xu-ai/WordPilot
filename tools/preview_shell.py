"""把新主界面渲染成预览图，用于目视检查布局。

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

def snap(name):
    app.processEvents()
    path = os.path.join(out_dir, f"shell_preview_{name}.png")
    mw.grab().save(path)
    print("已输出:", path)

# 窄态：智能纠错 / 翻译改写（插件页）/ 插件管理 / 设置
mw.show_page("dashboard");           snap("rail_dashboard")
mw.show_page("plugin:clipboard");    snap("rail_clipboard")
mw.show_page("plugins");             snap("rail_plugins")
mw.show_page("settings");            snap("rail_settings")

# 展开态
mw.nav.set_collapsed(False)
mw.show_page("dashboard");  snap("rail_expanded")
mw.nav.set_collapsed(True)

# 深色主题对照
k.set_theme("dark")
mw.show_page("dashboard");  snap("rail_dark")
mw.show_page("plugins");    snap("rail_plugins_dark")
k.set_theme("light")

# 复位语言与折叠状态，避免污染用户配置
k.config.set("ui_language", "zh_CN")
k.config.set("nav.collapsed", True)
k.shutdown()
