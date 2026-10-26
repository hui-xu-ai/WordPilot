# tests/test_skin_service.py
"""桌宠皮肤服务离线断言。

来源：`tools/smoke_p7_frozen.py:70-85`（皮肤包引用完整性、必需嘴型/眼型）、
`tools/smoke_p7.py:68-69`（eye_variant 状态映射）、
`tools/smoke_p4_pet.py:27-31`（part_svg 颜色替换）、
`tools/preview_pet.py:48-64`（5 状态 × 部件全非空）。

`SkinService` 本身零 Qt（只读 skin.json + SVG 文本），故全部可离线。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.services.skin_service import STATE_TOKENS, SkinService

PARTS = ("body", "eye_left", "eye_right", "mouth")


@pytest.fixture
def skins(fake_config, skins_dir):
    return SkinService(fake_config, skins_dir)


def test_discovers_skins_and_picks_default(skins):
    ids = [item["id"] for item in skins.available()]
    assert ids, "一套皮肤都没发现"
    assert skins.default_id in ids


def test_only_new_skins_are_shipped_and_bunny_is_default(skins):
    assert {item["id"] for item in skins.available()} == {
        "mochi_bunny", "ink_cat", "sky_whale", "page_spirit",
        "chestnut_corgi", "mint_dragon", "maple_fox", "dusk_owl"}
    assert skins.default_id == "mochi_bunny"


@pytest.mark.parametrize("old_id", ["default", "deepseek", "glass", "neon", "polygon", "whalegirl", "classic"])
def test_removed_skin_falls_back_without_writing(old_id, skins_dir, fake_config):
    fake_config["skin"] = old_id
    service = SkinService(fake_config, skins_dir)
    assert service.current_id == "mochi_bunny"
    assert fake_config["skin"] == old_id


def test_referenced_svg_files_exist(skins_dir):
    """清单里引用的每个 SVG 都必须真实存在（改名/漏打包的第一道防线）。"""
    missing: list[str] = []
    for manifest_path in skins_dir.glob("*/skin.json"):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        referenced = []
        for group in ("parts", "mouths", "eyes"):
            referenced.extend(data.get(group, {}).values())
        for name in referenced:
            if not (manifest_path.parent / name).is_file():
                missing.append(f"{manifest_path.parent.name}/{name}")
    assert not missing, f"皮肤引用了不存在的部件：{missing}"


@pytest.mark.parametrize("state", sorted(STATE_TOKENS))
def test_every_state_renders_all_parts(skins, state):
    """5 个桌宠状态 × 4 个部件都必须产出非空 SVG（缺件就是桌宠缺胳膊少腿）。"""
    empty = [part for part in PARTS if not skins.part_svg(part, state).strip()]
    assert not empty, f"状态 {state} 下这些部件为空：{empty}"


def test_eye_variant_follows_state(skins):
    assert skins.eye_variant("idle") == "sleep"
    assert skins.eye_variant("disabled") == "sleep"
    assert skins.eye_variant("error") == "alert"


def test_apply_colors_replaces_placeholders(skins):
    colored = skins.part_svg("body", "error", colors={"STATE": "#ff0000"})
    assert "#ff0000" in colored
    assert "$STATE" not in colored


def test_set_skin_persists_to_config(skins, fake_config):
    assert skins.set_skin("mochi_bunny") == "mochi_bunny"
    assert fake_config.get("skin") == "mochi_bunny"


def test_set_skin_ignores_unknown_id(skins):
    before = skins.current_id
    assert skins.set_skin("no-such-skin") == before


def test_layout_and_animation_are_usable(skins):
    assert {"eye_left", "eye_right", "mouth"} <= set(skins.layout())
    animation = skins.animation()
    assert animation["blink_interval_ms"] > 0
    assert 0 < animation["breathe_scale"] < 1


def test_state_tokens_cover_pet_fallback_colors():
    """桌宠每个状态都要能在 STATE_TOKENS 里取到主题令牌，否则渲染取不到状态色。

    `pet_window` 是 PySide6 模块：本用例需要在装了 PySide6 的项目 venv 里跑，
    纯 Qt 无关环境（如只装了 pytest 的系统 Python）跳过而不是失败。
    """
    pytest.importorskip("PySide6", reason="需要 PySide6 才能导入 pet_window 的兜底色表")
    from core.widgets.pet_window import _FALLBACK_COLORS

    assert set(_FALLBACK_COLORS) <= set(STATE_TOKENS)


@pytest.mark.parametrize("skin_id", ["mochi_bunny", "ink_cat", "sky_whale", "page_spirit",
                                     "chestnut_corgi", "mint_dragon", "maple_fox", "dusk_owl"])
def test_new_skins_have_distinct_expressions_and_valid_svg(skins, skin_id):
    from PySide6.QtSvg import QSvgRenderer

    ids = {item["id"] for item in skins.available()}
    assert skin_id in ids
    assert skins.status_overlay_enabled(skin_id)
    for state in STATE_TOKENS:
        for part in PARTS:
            svg = skins.part_svg(part, state, skin_id=skin_id, colors={"STATE": "#ef6b61"})
            assert svg and QSvgRenderer(svg.encode("utf-8")).isValid(), (skin_id, state, part)
    for part in ("eye_left", "mouth"):
        assert skins.part_svg(part, "ok", skin_id=skin_id) != skins.part_svg(part, "error", skin_id=skin_id)
