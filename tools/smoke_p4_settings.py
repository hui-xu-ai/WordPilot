"""P4 冒烟：设置页外观分区的皮肤下拉 + 桌宠右键菜单 + 语言切换不崩。

（P8 起设置从模态对话框改为主窗口内页面 SettingsPage）

离屏运行：  .venv\\Scripts\\python.exe tools\\smoke_p4_settings.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication      # noqa: E402

app = QApplication([])                           # noqa: F401

from core.kernel import Kernel                  # noqa: E402
from app.main_window import MainWindow          # noqa: E402
from plugins.proofread.pet_window import WINDOW_ID as PET_ID   # noqa: E402

k = Kernel()
k.boot()
mw = MainWindow(k)
k.attach_ui(mw.ui)
k.plugins.load_all()
mw.bind_plugins()

# 设置页已内置在主窗口里
page = mw._page_widgets.get("settings")
assert page is not None, "设置页应已内置"
print("设置页分区数:", page.stack.count())
print("皮肤选项:", [page.skin_combo.itemText(i) for i in range(page.skin_combo.count())],
      "| data:", [page.skin_combo.itemData(i) for i in range(page.skin_combo.count())])
print("当前选中:", page.skin_combo.currentData())

pet = k.services.get("window_manager").get(PET_ID)
print("切换前桌宠状态:", pet.state())

# 走一遍保存流程：改皮肤 -> 保存 -> 主窗口广播 skin.changed
page.skin_combo.setCurrentIndex(0)
page.save_settings()
mw._on_settings_saved()
print("保存后 config.skin:", k.config.get("skin"), "| 广播后桌宠尺寸:", pet.canvas.width())

# 语言切换（真实流程里由主窗口拉起；这里直接调用页面的重翻译方法）
k.set_language("en_US")
page.retranslateUi()
print("[EN] 设置页标题:", page.title_label.text(), "| 皮肤标签:", page.label_skin.text())
print("[EN] 桌宠 tooltip:", pet.toolTip())
k.set_language("zh_CN")
page.retranslateUi()
print("[ZH] 皮肤标签:", page.label_skin.text(), "| tooltip:", pet.toolTip())

# 主题切换后桌宠颜色跟随
k.set_theme("dark")
print("[dark] 桌宠色:", pet.canvas.state_color().name())
k.set_theme("light")
print("[light] 桌宠色:", pet.canvas.state_color().name())

print("\nOK")
