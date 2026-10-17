# core/sdk/plugin_base.py
"""插件清单与插件基类。

插件包约定：
    plugins/<plugin_id>/
        plugin.json      清单
        plugin.py        入口类（默认名，可用 entry 覆盖）
        ...              插件自有的逻辑与界面

plugin.json 字段：
    id                 插件唯一 id（必填，与目录名一致）
    name               显示名
    version            语义化版本
    description        简介
    author             作者
    entry              入口 "模块名:类名"
    requires           依赖的内核服务名列表
    deps               依赖的其它插件 id 列表
    enabled_by_default 是否默认启用

示例：
    {
      "id": "navigator",
      "name": "Word 导航助手",
      "version": "1.0.0",
      "description": "扫描文档标题/图表/公式并快速跳转",
      "author": "Xu Hui",
      "entry": "plugin:NavigatorPlugin",
      "requires": ["config", "word_bridge"],
      "deps": [],
      "enabled_by_default": true
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sdk.context import PluginContext

_VALID_FIELDS = {
    "id", "name", "entry", "version", "description",
    "author", "requires", "deps", "enabled_by_default",
}


@dataclass
class PluginManifest:
    id: str
    name: str = ""
    entry: str = "plugin:Plugin"
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    requires: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    enabled_by_default: bool = True

    @classmethod
    def from_file(cls, path: Path) -> "PluginManifest":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not raw.get("id"):
            raise ValueError(f"插件清单缺少 id 字段: {path}")
        raw.setdefault("name", raw["id"])
        return cls(**{k: v for k, v in raw.items() if k in _VALID_FIELDS})

    @property
    def entry_module(self) -> str:
        return self.entry.split(":", 1)[0]

    @property
    def entry_class(self) -> str:
        _, _, cls_name = self.entry.partition(":")
        return cls_name or "Plugin"


class PluginBase:
    """插件基类。

    生命周期：实例化 -> activate(ctx) -> ... -> deactivate()

    activate 中做三件事：
        1) ctx.get_service(...) 取内核服务；
        2) ctx.ui.add_tool(...) 注册界面入口；
        3) ctx.register_capability(...) 暴露能力给外壳或其它插件。

    deactivate 中必须释放：后台线程、窗口、热键、定时器。

    多语言：直接用 self.tr("xxx.yyy")，key 会自动带上本插件的命名空间，
    语言包放在 plugins/<plugin_id>/i18n/<lang>.json，由内核自动注册。
    未激活（ctx 为空）时 tr() 原样返回 key，保证插件在离屏/测试环境下不炸。
    """

    def __init__(self) -> None:
        self.ctx: "PluginContext" | None = None
        self._unsubscribes: list = []

    def activate(self, ctx: "PluginContext") -> None:
        self.ctx = ctx

    def deactivate(self) -> None:
        # 先退订，避免残留处理器持有已销毁的对象
        for unsubscribe in self._unsubscribes:
            try:
                unsubscribe()
            except Exception:
                pass
        self._unsubscribes.clear()
        self.ctx = None

    def subscribe(self, event: str, handler) -> None:
        """订阅内核事件；deactivate 时自动退订，防止插件卸载后仍被回调。"""
        if self.ctx is None:
            return
        self._unsubscribes.append(self.ctx.events.subscribe(event, handler))

    def tr(self, key: str, /, **params) -> str:
        """带插件命名空间的查词；ctx 缺失时原样返回 key。"""
        if self.ctx is None:
            return key
        return self.ctx.tr(key, **params)
