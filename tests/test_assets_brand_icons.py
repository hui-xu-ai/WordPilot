# tests/test_brand_assets.py
"""品牌图标资源：换成设计稿 A 后，托盘/建议窗/exe 图标都要存在且尺寸合规。"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/assets"

Pillow = pytest.importorskip("PIL.Image")


@pytest.mark.parametrize("name", ["tray_icon.png", "icon_float.png"])
def test_png_icons_are_square_512(name):
    image = Pillow.open(ASSETS / name)

    assert image.size == (512, 512), f"{name} 应是 512×512，实际 {image.size}"
    assert image.mode == "RGBA", "必须带透明通道（托盘/标题栏叠在深色背景上）"
    assert image.split()[3].getbbox() is not None, "整张图不能是全透明"


def test_ico_has_multi_sizes():
    image = Pillow.open(ASSETS / "icon.ico")
    sizes = {size for size in image.info.get("sizes", [])}

    for required in ((16, 16), (24, 24), (32, 32), (48, 48), (256, 256)):
        assert required in sizes, f"icon.ico 缺少 {required}，Windows 各场景会糊"
