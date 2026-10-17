"""P4 集成冒烟：桌宠作为受管窗口随插件启停，且状态随校对结果流转。

离屏运行：  .venv\\Scripts\\python.exe tools\\smoke_p4_integration.py
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
wm = k.services.get("window_manager")
print("已注册窗口:", wm.ids())

pet = wm.get(PET_ID)
print("桌宠受管:", pet is not None, "| 可见:", pet.isVisible() if pet else None)
print("桌宠状态:", pet.state() if pet else None, "| 气泡:", pet.bubble.isVisible() if pet else None)

plugin = k.capabilities.get("proofread")
print("\n=== 状态机 ===")
for state in ("busy", "error", "idle", "disabled"):
    plugin.set_state(state)
    print(f"  {state:9s} -> pet: {pet.state():9s} canvas: {pet.canvas.state_color().name()}")

print("\n=== 开关联动 ===")
plugin.set_enabled(False)
print("关闭校对 -> pet 状态:", pet.state(), "| 可见:", pet.isVisible())
plugin.set_enabled(True)
print("开启校对 -> pet 状态:", pet.state(), "| 可见:", pet.isVisible())

print("\n=== 皮肤与主题热切换 ===")
skins = k.services.get("skins")
print("皮肤:", skins.available())
k.set_theme("dark")
print("切深色后 pet 颜色:", pet.canvas.state_color().name())
k.set_theme("light")
print("切浅色后 pet 颜色:", pet.canvas.state_color().name())

print("\n=== 卸载插件应关掉桌宠 ===")
k.plugins.unload("proofread")
print("卸载后已注册窗口:", wm.ids())
print("窗口服务里还能取到桌宠:", wm.get(PET_ID))

print("\nOK")
