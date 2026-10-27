# tests/test_assets_brand_icons.py
"""品牌图标资源：03-jade 稿已复制进打包资源目录，尺寸规格合规。

用户要求（2026-10-27）：用 work/design/2026-10-27-logo-free 的 03-jade.png / 03-jade.ico，
并复制到程序打包专用资源目录，避免打包后路径失效。
"""
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


def test_brand_assets_are_packaged():
    """打包清单必须收录这三枚资源 —— 否则 exe 换机器后图标路径失效。"""
    spec = (ROOT / "WordPilot.spec").read_text(encoding="utf-8")

    assert "src/assets/tray_icon.png" in spec
    assert "src/assets/icon_float.png" in spec
    assert "src/assets/icon.ico" in spec


def test_design_source_still_available():
    """设计稿原件保留在 work/design 下（不入包），方便日后换肤/改稿。"""
    design = ROOT / "work/design/2026-10-27-logo-free"

    assert (design / "03-jade.png").is_file()
    assert (design / "03-jade.ico").is_file()
