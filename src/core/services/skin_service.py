# core/services/skin_service.py
"""桌宠皮肤服务：发现皮肤包、解析布局、产出可渲染的 SVG。

设计要点：
- 纯 Python，零 Qt 依赖（渲染交给界面层），因此可以在内核 boot() 阶段注册；
- 皮肤 = 一个目录：<skins_dir>/<skin_id>/skin.json + 若干 SVG 分部件；
- 分部件（body / 眼睛 / 嘴）分开是为了让「状态色」只作用在需要变色的部件上，
  也让换肤变成换目录，不必重画整只宠；
- SVG 里的颜色用占位符 $STATE / $BODY / $ACCENT，渲染前按主题令牌替换。

新增一套皮肤 = 往 assets/skins/ 丢一个目录，无需改代码、无需编译。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from infra.utils import resource_path

logger = logging.getLogger(__name__)

#: 桌宠状态 -> 主题令牌（颜色即状态：绿=无错/正确 / 红=有错 / 黄=校对中 / 灰=未启用）
STATE_TOKENS = {
    "idle": "color.state.off",     # 休息不等于校对通过
    "ok": "color.state.ok",        # 校对正确（开心笑，2 秒后回落 idle）
    "error": "color.state.error",  # 有修改建议（皱眉，等用户决策）
    "busy": "color.state.busy",
    "disabled": "color.state.off",
}
DEFAULT_STATE = "idle"
DEFAULT_SKIN_ID = "mochi_bunny"

# 新皮肤用固定的高对比状态装饰；角色本色独立于主题和状态。
STATE_COLORS = {
    "idle": "#9096a0", "ok": "#36ad59", "error": "#ef6b61",
    "busy": "#e6a232", "disabled": "#9096a0",
}


class SkinService:
    def __init__(self, config, skins_dir: Path | None = None, i18n=None) -> None:
        self.config = config
        self.skins_dir = Path(skins_dir) if skins_dir else Path(resource_path("assets/skins"))
        self._manifests: dict[str, dict] = {}
        self._svg_cache: dict[tuple[str, str], str] = {}
        # 皮肤名的语言：只受界面语言控制（文案库 skin.<id>）
        self._i18n = i18n
        self._load_all()

    # ---------------- 发现与选择 ----------------
    def _load_all(self) -> None:
        if not self.skins_dir.is_dir():
            logger.warning("皮肤目录不存在: %s", self.skins_dir)
            return
        for entry in sorted(self.skins_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest = self._read_manifest(entry)
            if manifest is not None:
                self._manifests[manifest["id"]] = manifest

    @staticmethod
    def _read_manifest(directory: Path) -> dict | None:
        path = directory / "skin.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            logger.exception("皮肤清单解析失败: %s", path)
            return None
        if not data.get("id"):
            logger.warning("皮肤清单缺少 id: %s", path)
            return None
        data["_dir"] = directory
        data.setdefault("name", data["id"])
        data.setdefault("size", {"width": 64, "height": 64})
        data.setdefault("parts", {})
        data.setdefault("layout", {})
        data.setdefault("mouths", {})
        data.setdefault("animation", {})
        return data

    @property
    def current_id(self) -> str:
        configured = self.config.get("skin", "") if self.config else ""
        if configured in self._manifests:
            return configured
        return self.default_id

    @property
    def default_id(self) -> str:
        return DEFAULT_SKIN_ID if DEFAULT_SKIN_ID in self._manifests else (
            sorted(self._manifests)[0] if self._manifests else "")

    def _display_name(self, skin_id: str, fallback: str) -> str:
        """皮肤显示名：`skin.<id>`（界面语言）优先，清单里的 name 兜底。"""
        from core.services.ui_labels import skin_label

        service = getattr(self, "_i18n", None)
        return skin_label(skin_id, fallback, service.tr if service is not None else None)

    def available(self) -> list[dict]:
        """[{id, name}]，按 id 排序，供切换菜单使用。"""
        # 名称优先取语言包 `skin.<id>`（皮肤清单里的 name 是作者语言，可能只有中文）
        return [{"id": sid, "name": self._display_name(sid, m.get("name", sid))}
                for sid, m in sorted(self._manifests.items())]

    def set_skin(self, skin_id: str) -> str:
        if skin_id not in self._manifests:
            logger.warning("皮肤不存在: %s，保持 %s", skin_id, self.current_id)
            return self.current_id
        if self.config is not None:
            self.config.set("skin", skin_id)
        return skin_id

    # ---------------- 供界面层取用 ----------------
    def size(self, skin_id: str | None = None) -> tuple[int, int]:
        manifest = self._manifest(skin_id)
        size = manifest.get("size", {}) if manifest else {}
        return int(size.get("width", 64)), int(size.get("height", 64))

    def animation(self, skin_id: str | None = None) -> dict:
        manifest = self._manifest(skin_id)
        anim = dict(manifest.get("animation", {})) if manifest else {}
        anim.setdefault("blink_interval_ms", 3400)
        anim.setdefault("blink_duration_ms", 140)
        anim.setdefault("breathe_interval_ms", 2600)
        anim.setdefault("breathe_scale", 0.04)
        return anim

    def layout(self, skin_id: str | None = None) -> dict:
        manifest = self._manifest(skin_id)
        return dict(manifest.get("layout", {})) if manifest else {}

    def status_overlay_enabled(self, skin_id: str | None = None) -> bool:
        """可选的共享光环/角标；旧皮肤保持原有绘制协议。"""
        manifest = self._manifest(skin_id)
        return bool(manifest and manifest.get("status_overlay", False))

    def part_svg(self, name: str, state: str = DEFAULT_STATE, *,
                 skin_id: str | None = None, colors: dict[str, str] | None = None) -> str:
        """取某个部件的 SVG 文本（已替换占位符），找不到返回空串。

        name: parts 里的键，或 mouths 里按状态映射到的文件名。
        """
        manifest = self._manifest(skin_id)
        if manifest is None:
            return ""
        filename = self._resolve_part(manifest, name, state)
        if not filename:
            return ""
        key = (manifest["id"], filename)
        raw = self._svg_cache.get(key)
        if raw is None:
            path = manifest["_dir"] / filename
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                logger.warning("皮肤部件读取失败: %s", path)
                return ""
            self._svg_cache[key] = raw
        return self._apply_colors(raw, colors or {})

    def _resolve_part(self, manifest: dict, name: str, state: str) -> str:
        parts = manifest.get("parts", {})
        if name == "mouth":
            mouths = manifest.get("mouths", {})
            return mouths.get(state) or mouths.get(DEFAULT_STATE) or parts.get("mouth", "")
        if name in ("eye_left", "eye_right"):
            # 眼睛按状态选开/闭：eye_states 把状态映射到 eyes 里的变体
            eye_states = manifest.get("eye_states", {})
            variant = eye_states.get(state, "open")
            eyes = manifest.get("eyes", {})
            return eyes.get(variant) or parts.get(name, "")
        return parts.get(name, "")

    def eye_variant(self, state: str, skin_id: str | None = None) -> str:
        """当前状态下眼睛的变体名（open/sleep），供界面层决定是否眨眼。"""
        manifest = self._manifest(skin_id)
        if manifest is None:
            return "open"
        return manifest.get("eye_states", {}).get(state, "open")

    # ---------------- 工具 ----------------
    def _manifest(self, skin_id: str | None = None) -> dict | None:
        sid = skin_id or self.current_id
        return self._manifests.get(sid)

    @staticmethod
    def _apply_colors(svg_text: str, colors: dict[str, str]) -> str:
        for token, value in colors.items():
            svg_text = svg_text.replace(f"${token}", value)
        return svg_text

    def reload(self) -> None:
        self._manifests.clear()
        self._svg_cache.clear()
        self._load_all()
