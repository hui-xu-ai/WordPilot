# plugins/proofread/notify.py
"""校对相关的非模态提示（已过期 / 需手动修改）。抽出来给 plugin.py 减行。"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox


def _show(parent, title: str, text: str, icon, timeout_ms: int) -> None:
    box = QMessageBox(parent or QApplication.activeWindow())
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Ok)
    box.setModal(False)
    box.show()
    QTimer.singleShot(timeout_ms, box.close)


def warn_stale(tr) -> None:
    """建议已过期：原文变了，本次不写回。"""
    _show(None, tr("error.stale_title"), tr("error.stale_body"), QMessageBox.Warning, 4000)


def notify_manual_edit(tr) -> None:
    """含图片/公式的句子无法安全整体替换，只清标记并提示手动改。"""
    _show(None, tr("manual.title"), tr("manual.body"), QMessageBox.Information, 2000)
