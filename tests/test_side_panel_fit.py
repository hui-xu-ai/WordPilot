# tests/test_layout_fit.py
"""U05/U06：默认 550px 窗口里，抽屉不能把内容顶出窗口，按钮文字不能被压成省略号。

审查证据：抽屉右边 718px（窗口 550），英文翻译页 5 个按钮挤一行，
Read clipboard 需要 103px 实际只有 43px。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_side_panel_fits_inside_narrow_window(qapp):
    """U05：父容器只有 550px 时，展开的抽屉必须留在里面。"""
    from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout
    from app.side_panel import SidePanelHost

    window = QWidget()
    window.resize(550, 600)
    layout = QHBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = SidePanelHost(parent=window, width=400)
    layout.addWidget(panel)
    layout.addWidget(QLabel("主内容"), 1)

    panel.set_content(QLabel("抽屉内容"), "参数")
    panel.set_open(True)
    window.show()
    qapp.processEvents()

    main = layout.itemAt(1).widget()
    assert main.width() >= 260, f"主内容被抽屉挤到只剩 {main.width()}px"
    assert panel.width() + main.width() <= 560, "抽屉 + 主内容超出窗口宽度"


def test_clipboard_buttons_wrap_in_narrow_window(qapp):
    """U06：五个动作按钮换行显示，而不是把文字压成省略号。"""
    from PySide6.QtWidgets import QGridLayout, QWidget
    from plugins.clipboard.plugin import ClipboardWidget

    class _Ctx:                                  # 只要 tr，不需要完整上下文
        def tr(self, key, /, **params):
            return key

    page = ClipboardWidget(_Ctx())
    page.resize(430, 600)
    page.show()
    qapp.processEvents()

    layout = None
    for child_layout in page.findChildren(QGridLayout):
        if child_layout.count() >= 5:
            layout = child_layout
            break
    assert layout is not None, "按钮应放在栅格布局里"
    assert layout.rowCount() >= 2, "窄窗口下按钮应至少分两行"
