# tests/test_i18n_skin_names.py
"""宠物皮肤名按界面语言显示（用户 2026-10-28 收尾项）。

之前皮肤名来自 `skin.json` 的 `name`（作者写的中文），界面切英文也不变；
现在统一走文案库 `skin.<id>`，只受界面语言控制，清单名只作兜底。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")


class _Config:
    def __init__(self, language: str = "en_US"):
        self.data = {"ui_language": language}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    # SettingsPage 需要的配置接口
    def get_api_key(self):
        return ""

    def set_api_key(self, value):
        self.data["api_key"] = value

    def update(self, payload):
        self.data.update(payload)
        return True


def _i18n(language: str = "en_US"):
    from core.services.i18n import I18nService

    return I18nService(_Config(language), ROOT / "src/assets/i18n")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _skin_ids() -> list[str]:
    return [json.loads(path.read_text(encoding="utf-8"))["id"]
            for path in sorted((ROOT / "src/assets/skins").glob("*/skin.json"))]


def test_every_skin_has_both_languages():
    ids = _skin_ids()
    assert ids, "没有找到皮肤清单"

    zh = json.loads((ROOT / "src/assets/i18n/zh_CN.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / "src/assets/i18n/en_US.json").read_text(encoding="utf-8"))

    for skin_id in ids:
        assert zh.get(f"skin.{skin_id}"), f"{skin_id} 缺中文名"
        assert en.get(f"skin.{skin_id}"), f"{skin_id} 缺英文名"
        assert not CJK.search(en[f"skin.{skin_id}"]), f"{skin_id} 的英文名含中文"


def test_service_names_follow_ui_language():
    from core.services.skin_service import SkinService

    names = {}
    for language in ("zh_CN", "en_US"):
        service = SkinService(_Config(language), ROOT / "src/assets/skins",
                              i18n=_i18n(language))
        names[language] = {item["id"]: item["name"] for item in service.available()}

    assert names["zh_CN"]["chestnut_corgi"] == "栗子柯基"
    assert names["en_US"]["chestnut_corgi"] == "Chestnut corgi"
    assert all(not CJK.search(name) for name in names["en_US"].values()), names["en_US"]


def test_chinese_names_match_manifest():
    """中文文案与皮肤清单里的作者命名保持一致（避免两套中文名）。"""
    zh = json.loads((ROOT / "src/assets/i18n/zh_CN.json").read_text(encoding="utf-8"))
    for path in sorted((ROOT / "src/assets/skins").glob("*/skin.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert zh[f"skin.{manifest['id']}"] == manifest["name"], path.parent.name


def test_settings_combo_retranslates(app):
    from PySide6.QtWidgets import QApplication  # noqa: F401

    from app.settings_page import SettingsPage
    from core.services.skin_service import SkinService

    class _KernelSkins(SkinService):
        pass

    config = _Config("en_US")
    service = _KernelSkins(config, ROOT / "src/assets/skins", i18n=_i18n("en_US"))
    page = SettingsPage(config, i18n=_i18n("en_US"), skins=service)
    page.retranslateUi()

    labels = [page.skin_combo.itemText(i) for i in range(page.skin_combo.count())]
    assert labels and all(not CJK.search(label) for label in labels), labels
    assert page.skin_combo.itemData(0), "下拉必须仍存 id，显示才翻译"
