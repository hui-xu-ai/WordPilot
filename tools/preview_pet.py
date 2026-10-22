"""把桌宠四种状态渲染成一张预览图，用于目视检查造型。

离屏运行：  .venv\\Scripts\\python.exe tools\\preview_pet.py
输出：      work/pet_preview.png
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt                     # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap       # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

app = QApplication([])                                    # noqa: F401

from core.services.skin_service import SkinService        # noqa: E402
from plugins.proofread.pet_window import _FALLBACK_COLORS  # noqa: E402

SCALE = 3
STATES = ("idle", "ok", "error", "busy", "disabled")
LABELS = {"idle": "打盹", "ok": "正确(开心)", "error": "有建议(等决策)", "busy": "校对中", "disabled": "未启用"}

skins = SkinService(None, None)
width, height = skins.size()
colors = {"BODY": "#ffffff", "ACCENT": "#0078d4"}

pad = 12
label_h = 22
img_w = (width * SCALE + pad * 2) * len(STATES)
img_h = height * SCALE + pad * 2 + label_h
pixmap = QPixmap(img_w, img_h)
pixmap.fill(QColor("#f3f3f3"))

painter = QPainter(pixmap)
painter.setRenderHint(QPainter.Antialiasing, True)
painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

from PySide6.QtSvg import QSvgRenderer                    # noqa: E402

for index, state in enumerate(STATES):
    color = _FALLBACK_COLORS[state]
    colors["STATE"] = color
    origin_x = pad + index * (width * SCALE + pad * 2)

    def draw(part, box):
        svg = skins.part_svg(part, state, colors=colors)
        if not svg:
            return
        renderer = QSvgRenderer(svg.encode("utf-8"))
        if not renderer.isValid():
            return
        renderer.render(painter, QRectF(origin_x + box[0] * SCALE,
                                        pad + box[1] * SCALE,
                                        box[2] * SCALE, box[3] * SCALE))

    draw("body", (0, 0, width, height))
    layout = skins.layout()
    for name in ("eye_left", "eye_right", "mouth"):
        box = layout.get(name)
        if box:
            draw(name, (box["x"], box["y"], box["w"], box["h"]))

    painter.setPen(QColor("#1b1b1b"))
    painter.drawText(QRectF(origin_x, pad + height * SCALE + 2, width * SCALE, label_h),
                     Qt.AlignHCenter | Qt.AlignVCenter, f"{LABELS[state]} / {state}")

painter.end()

out = os.path.join(ROOT, "work", "pet_preview.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
pixmap.save(out)
print("已输出:", out, pixmap.width(), "x", pixmap.height())
