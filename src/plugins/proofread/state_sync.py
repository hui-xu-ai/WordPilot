# plugins/proofread/state_sync.py
"""把校对的启停状态同步给桌宠窗口（右键菜单据此显示「暂停/继续校对」）。

单独成文件：`plugin.py` 已接近行数阈值，而这段只做"找窗口 + 设状态"。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def push_proofread_state(plugin, enabled: bool, pet=None) -> None:
    """找桌宠窗口（传入的优先，其次宿主），把状态写进去。"""
    if pet is None:
        try:
            from core.services.pet_host import PetHost

            pet = PetHost.instance().window()
        except Exception:                      # noqa: BLE001
            pet = None
    setter = getattr(pet, "set_proofread_enabled", None)
    if callable(setter):
        try:
            setter(bool(enabled))
        except Exception:                      # noqa: BLE001 - 窗口可能已销毁
            logger.debug("同步桌宠校对状态失败", exc_info=True)
