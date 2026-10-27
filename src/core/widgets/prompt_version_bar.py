# core/widgets/prompt_version_bar.py
"""提示词版本条：另存为版本 / 切换 / 删除 / 恢复默认（供各插件的提示词编辑区复用）。

用户要求（2026-10-27）：提示词能保存备份与切换；翻译改写页的纠错与智能纠错插件各自管理。
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QMenu, QMessageBox,
                               QToolButton, QWidget)

from core.services import prompt_store

logger = logging.getLogger(__name__)


class PromptVersionBar(QWidget):
    """挂在提示词编辑框下面：选版本 + 存版本 + 删除 + 恢复默认。

    与调用方约定：
    - `read_text()` 取编辑框当前内容；
    - `apply_text(text)` 把文本写进编辑框（切版本/恢复默认时用）。
    """

    def __init__(self, key: str, config, tr, read_text, apply_text, parent=None) -> None:
        super().__init__(parent)
        self._key = key
        self._config = config
        self._tr = tr
        self._read_text = read_text
        self._apply_text = apply_text

        # 紧凑布局：版本下拉 + 一个「⋯」菜单（三个大按钮太占地方，用户反馈）
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.combo = QComboBox()
        self.combo.setMaximumWidth(220)
        layout.addWidget(self.combo, 1)
        self.menu_btn = QToolButton()
        self.menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self.menu_btn)
        self.save_action = self.menu.addAction("", self._on_save)
        self.delete_action = self.menu.addAction("", self._on_delete)
        self.menu.addSeparator()
        self.reset_action = self.menu.addAction("", self._on_reset)
        self.menu_btn.setMenu(self.menu)
        layout.addWidget(self.menu_btn)

        self.combo.currentIndexChanged.connect(self._on_pick)
        self.refresh()

    # ---------------- 文案 ----------------
    def retranslate(self) -> None:
        self.save_action.setText(self._tr("prompt.version.save"))
        self.delete_action.setText(self._tr("prompt.version.delete"))
        self.reset_action.setText(self._tr("prompt.version.reset"))
        self.menu_btn.setToolTip(self._tr("prompt.version.title"))

    # ---------------- 数据 ----------------
    def refresh(self, select: str = "") -> None:
        """按仓库里的版本列表刷新下拉（不触发应用回调）。"""
        items = prompt_store.versions(self._key)
        active = prompt_store.active_name(self._key)
        self.combo.blockSignals(True)
        try:
            self.combo.clear()
            for item in items:
                label = item["name"] + ("（当前）" if item["active"] else "")
                self.combo.addItem(label, item["name"])
            target = select or active
            index = self.combo.findData(target)
            if index >= 0:
                self.combo.setCurrentIndex(index)
            elif self.combo.count():
                self.combo.setCurrentIndex(0)
        finally:
            self.combo.blockSignals(False)

    def _default_text(self) -> str:
        """恢复默认：取语言包里的默认文案（按提示词语言）。"""
        try:
            from core.services.config_manager import ConfigManager

            tr = getattr(self._config, "prompt_tr", None)
            if callable(tr):
                return tr(self._default_key())
            i18n = ConfigManager  # noqa: F841 - 仅为可读性
        except Exception:                    # noqa: BLE001
            pass
        return ""

    def _default_key(self) -> str:
        return {
            "proof_prompt": "prompt.proof.default",
            "correct_prompt": "prompt.correct.default",
            "translate_prompt": "prompt.translate.default",
            "rewrite_prompt": "prompt.rewrite.default",
        }.get(self._key, "")

    # ---------------- 交互 ----------------
    def _on_pick(self, _index: int) -> None:
        name = self.combo.currentData()
        if not name:
            return
        text = prompt_store.read(self._key, name)
        if not text:
            return
        self._apply_text(text)
        self._config.set(self._key, text)
        self._save_config()

    def _on_save(self) -> None:
        text = self._read_text()
        if not text.strip():
            QMessageBox.information(self, self._tr("prompt.version.title"),
                                    self._tr("prompt.version.empty"))
            return
        default_name = f"我的版本 {len(prompt_store.versions(self._key)) + 1}"
        name, ok = QInputDialog.getText(self, self._tr("prompt.version.title"),
                                        self._tr("prompt.version.name"), text=default_name)
        if not ok or not name.strip():
            return
        old = prompt_store.read(self._key, prompt_store.active_name(self._key))
        if old.strip() and old.strip() != text.strip():
            prompt_store.backup_active(self._key, old)      # 覆盖前先留一份自动备份
        prompt_store.save_version(self._key, name.strip(), text, activate=True)
        self._config.set(self._key, text)
        self._save_config()
        self.refresh(select=name.strip())

    def _on_delete(self) -> None:
        name = self.combo.currentData()
        if not name:
            return
        answer = QMessageBox.question(
            self, self._tr("prompt.version.title"),
            self._tr("prompt.version.delete_confirm", name=name))
        if answer != QMessageBox.Yes:
            return
        if not prompt_store.delete_version(self._key, name):
            QMessageBox.information(self, self._tr("prompt.version.title"),
                                    self._tr("prompt.version.last"))
            return
        self.refresh()

    def _on_reset(self) -> None:
        text = self._default_text()
        if not text:
            return
        self._apply_text(text)
        self._config.set(self._key, "")
        self._save_config()

    def _save_config(self) -> None:
        try:
            self._config.save()
        except Exception:                    # noqa: BLE001 - 保存失败不该崩界面
            logger.exception("保存提示词失败")
