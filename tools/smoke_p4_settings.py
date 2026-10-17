"""P4 冒烟：设置页外观页的皮肤下拉 + 桌宠右键菜单 + 语言切换不崩。

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
from app.settings_dialog import SettingsDialog  # noqa: E402
from plugins.proofread.pet_window import WINDOW_ID as PET_ID   # noqa: E402

k = Kernel()
k.boot()
mw = MainWindow(k)
k.attach_ui(mw.ui)
k.plugins.load_all()
mw.bind_plugins()

dlg = SettingsDialog(k.config, mw, theme=k.services.get("theme"),
                     i18n=k.services.get("i18n"), skins=k.services.get("skins"))
idx = dlg.tabs.indexOf(dlg.tabs.findChild(type(dlg)) if False else dlg.tabs.widget(3))
print("外观页标题:", dlg.tabs.tabText(3))
print("皮肤选项:", [dlg.skin_combo.itemText(i) for i in range(dlg.skin_combo.count())],
      "| data:", [dlg.skin_combo.itemData(i) for i in range(dlg.skin_combo.count())])
print("当前选中:", dlg.skin_combo.currentData())

pet = k.services.get("window_manager").get(PET_ID)
print("切换前桌宠状态:", pet.state())

# 走一遍保存流程：改皮肤 -> 保存 -> 主窗口广播 skin.changed
dlg.skin_combo.setCurrentIndex(0)
dlg.save_settings()
mw_kernel_skin = mw.kernel.set_skin(mw.config.get("skin", "default"))
print("保存后 config.skin:", k.config.get("skin"), "| 广播后桌宠尺寸:", pet.canvas.width())

# 语言切换（真实流程里由主窗口拉起；这里直接调用对话框的重翻译方法）
k.set_language("en_US")
dlg.retranslateUi()
print("[EN] 外观页:", dlg.tabs.tabText(3), "| 皮肤标签:", dlg.label_skin.text())
print("[EN] 桌宠 tooltip:", pet.toolTip())
k.set_language("zh_CN")
dlg.retranslateUi()
print("[ZH] 皮肤标签:", dlg.label_skin.text(), "| tooltip:", pet.toolTip())

# 主题切换后桌宠颜色跟随
k.set_theme("dark")
print("[dark] 桌宠色:", pet.canvas.state_color().name())
k.set_theme("light")
print("[light] 桌宠色:", pet.canvas.state_color().name())

print("\nOK")
