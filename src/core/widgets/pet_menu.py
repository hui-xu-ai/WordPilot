# core/widgets/pet_menu.py
"""宠物右键菜单（外观皮肤 / 插件快捷项 / 校对开关 / 建议气泡 / 设置）。"""
from __future__ import annotations

from PySide6.QtWidgets import QMenu


def _pet_service():
    try:
        from core.services.pet_service import PetService

        return PetService.instance()
    except Exception:                      # noqa: BLE001
        return None


class PetMenuMixin:
    """宿主需提供 tr / _skins / _tools_provider / proofreadToggleRequested 等。"""

    def _build_menu(self):
        menu = QMenu(self)
        menu.setObjectName("petMenu")

        skins_menu = menu.addMenu(self.tr("pet.menu.skin"))
        current_skin = self._skins.current_id if self._skins else ""
        from core.services.i18n import message
        from core.services.ui_labels import skin_label

        for item in (self._skins.available() if self._skins else []):
            # 皮肤名是应用级文案（skin.<id>）：用 message()，别用插件命名空间的 tr
            label = skin_label(item["id"], item["name"], message)
            action = skins_menu.addAction(label,
                                          lambda sid=item["id"]: self._switch_skin(sid))
            action.setCheckable(True)
            action.setChecked(item["id"] == current_skin)

        # 插件快捷入口：内核宠物服务里"已启用插件"注册的动作
        tools = list(self._tools_provider() if self._tools_provider else [])
        service = _pet_service()
        if service is not None:
            tools = list(service.actions() or []) + tools
        if tools:
            tools_menu = menu.addMenu(self.tr("pet.menu.plugins"))
            for label, handler in tools:
                tools_menu.addAction(label, handler)

        menu.addSeparator()
        # 按**当前状态**显示将要发生的动作：校对中 →「暂停校对」；已暂停 →「继续校对」。
        # 原来是可勾选的「暂停/继续校对」，勾与不勾都读不出当前处于哪种状态（用户反馈语义不明）。
        running = self._proofread_enabled()
        key = "pet.menu.resume_proofread" if not running else "pet.menu.pause_proofread"
        menu.addAction(self.tr(key), self.proofreadToggleRequested.emit)

        bubble_action = menu.addAction(self.tr("pet.menu.toggle_bubble"), self.toggle_bubble)
        bubble_action.setCheckable(True)
        bubble_action.setChecked(self.bubble.isVisible())

        menu.addAction(self.tr("pet.menu.settings"), self.settingsRequested.emit)
        return menu

    def _show_menu(self, pos) -> None:
        menu = self._build_menu()
        menu.exec(self.mapToGlobal(pos))
