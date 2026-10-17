"""P4 桌宠冒烟：皮肤发现 / 状态色 / 气泡合并 / 位置持久化。

离屏运行：  .venv\\Scripts\\python.exe tools\\smoke_p4_pet.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication      # noqa: E402

app = QApplication([])                           # noqa: F401

from core.services.skin_service import SkinService        # noqa: E402
from plugins.proofread.pet_window import PetWindow        # noqa: E402


class Cfg(dict):
    """ConfigManager 的最小替身（只需 get/set）。"""

    def set(self, key, value):
        self[key] = value


print("=== 1) 皮肤发现 ===")
skins = SkinService(None, None)
print("available:", skins.available(), "| current:", skins.current_id, "| size:", skins.size())
print("anim     :", skins.animation())
body = skins.part_svg("body", "error", colors={"STATE": "#ff0000", "BODY": "#fff", "ACCENT": "#07d"})
print("body(state=error) 含替换后的颜色:", "#ff0000" in body)

print("\n=== 2) 桌宠窗口与状态色 ===")
cfg = Cfg({"show_floating_button": True, "auto_show_suggestion": True, "suggestion_opacity": 0.95})
pet = PetWindow(cfg, theme=None, skins=skins, i18n=None, tr=lambda k, **_p: k)
pet.show()
print("size:", pet.width(), pet.height())
for state in ("idle", "error", "busy", "disabled"):
    pet.set_state(state)
    print(f"  {state:9s} -> {pet.canvas.state_color().name()}")

print("\n=== 3) 气泡合并（有差异才弹） ===")
pet.set_suggestion("这是一段有错别字得文本。", "这是一段有错别字的文本。", 0, 10)
pet.set_state("error" if pet.bubble.isVisible() else "idle")
print("有差异 -> bubble:", pet.bubble.isVisible(), "| state:", pet.state())
pet.set_suggestion("完全相同。", "完全相同。", 0, 5)
pet.set_state("error" if pet.bubble.isVisible() else "idle")
print("无差异 -> bubble:", pet.bubble.isVisible(), "| state:", pet.state())

print("\n=== 4) 位置持久化 ===")
pet.move(120, 240)
pet._save_position()
print("pet.position:", cfg.get("pet.position"))

print("\n=== 5) 换肤 ===")
skins.set_skin("default")
pet.set_services(skins=skins)
print("current skin:", skins.current_id, "| canvas:", pet.canvas.width(), pet.canvas.height())

print("\nOK")
