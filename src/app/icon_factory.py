# app/icon_factory.py
"""SVG 图标工厂。

图标统一放在 assets/icons/<name>.svg，用 Fluent 线条风格绘制，
描边颜色写作占位符 ``$COLOR``——这里按主题令牌现场替换后渲染成
QPixmap，因此图标颜色永远跟随当前主题（深/浅自动适配），
选中/普通两态用 QIcon 的 On/Off 模式承载。

设计约定：
- 图标只画 24x24 视框的线条稿，不带任何业务语义；
- 颜色只允许来自主题令牌（color.text.secondary / color.accent 等）；
- 渲染结果按 (名称, 颜色, 尺寸) 缓存；切换主题后调用 clear_cache()。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from infra.utils import resource_path

_pixmap_cache: dict[tuple[str, str, int], QPixmap] = {}


def clear_cache() -> None:
    """主题切换后调用，丢弃旧的染色结果。"""
    _pixmap_cache.clear()


def _render(name: str, color: str, size: int) -> QPixmap:
    key = (name, color, size)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached

    pm = QPixmap()
    path = Path(resource_path(f"assets/icons/{name}.svg"))
    try:
        data = path.read_text(encoding="utf-8").replace("$COLOR", color)
    except OSError:
        _pixmap_cache[key] = pm
        return pm

    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
    if not renderer.isValid():
        _pixmap_cache[key] = pm
        return pm

    # 2 倍像素渲染 + devicePixelRatio，保证高 DPI 下线条锐利
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    _pixmap_cache[key] = pm
    return pm


def icon(name: str, color: str, size: int = 20) -> QIcon:
    """单色图标。"""
    result = QIcon()
    result.addPixmap(_render(name, color, size))
    return result


def checkable_icon(name: str, off_color: str, on_color: str, size: int = 20) -> QIcon:
    """可选中图标：Off 态用次要文本色，On 态用主题强调色。"""
    result = QIcon()
    result.addPixmap(_render(name, off_color, size), QIcon.Normal, QIcon.Off)
    result.addPixmap(_render(name, on_color, size), QIcon.Normal, QIcon.On)
    result.addPixmap(_render(name, on_color, size), QIcon.Active, QIcon.Off)
    return result
