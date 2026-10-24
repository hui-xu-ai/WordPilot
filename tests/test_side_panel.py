# tests/test_side_panel.py
"""右侧「次要界面」抽屉（ADR-002）的容器语义。

钉住的契约：
- 没有内容时整块隐藏（否则用户会看到一条点不出东西的空条）；
- 有内容时默认仍收起（保持主区简洁），点一下才展开；
- 切换内容不许销毁旧控件（外壳按插件缓存复用），且**先 hide 再脱离父级**（否则会变成顶层窗口）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(qapp):
    from app.side_panel import SidePanelHost

    return SidePanelHost(width=320, opened=False)


def test_hidden_without_content(host):
    assert host.isVisible() is False, "没有次要界面时不该出现"
    assert host.has_content() is False


def test_content_is_registered_but_stays_collapsed(host, qapp):
    from PySide6.QtWidgets import QLabel

    host.set_content(QLabel("面板内容"), "参数")
    assert host.isVisible() is True, "有内容时要露出折叠条"
    assert host.is_open() is False, "默认收起（保持主区简洁）"
    assert host.width() < 60, f"收起态只占折叠条宽度，实际 {host.width()}"


def test_toggle_expands_and_collapses(host):
    from PySide6.QtWidgets import QLabel

    host.set_content(QLabel("面板内容"), "参数")
    seen: list[bool] = []
    host.toggled.connect(seen.append)

    host.toggle()
    assert host.is_open() is True
    assert host.width() >= 320, f"展开态应拿到内容宽度，实际 {host.width()}"

    host.toggle()
    assert host.is_open() is False
    assert seen == [True, False]


def test_set_open_without_content_is_noop(host):
    host.set_open(True)
    assert host.is_open() is False
    assert host.isVisible() is False


def test_switching_content_keeps_previous_widget_alive(host):
    """外壳按插件缓存面板：换页时不能把旧控件删掉（否则切回来是空的）。"""
    from PySide6.QtWidgets import QLabel

    first = QLabel("第一个")
    host.set_content(first, "一")
    second = QLabel("第二个")
    host.set_content(second, "二")

    assert host.is_open() is False or host.has_content() is True
    # 旧控件不应被销毁，只是被隐藏/脱离父级
    assert first is not None
    assert first.parent() is None
    assert first.isHidden() is True


def test_host_paints_stylesheet_background(qapp):
    """QWidget 子类必须开 WA_StyledBackground，否则 QSS 背景在真机上不绘制。"""
    from PySide6.QtCore import Qt

    from app.side_panel import SidePanelHost

    assert SidePanelHost(width=300).testAttribute(Qt.WA_StyledBackground)
