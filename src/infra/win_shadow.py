# infra/win_shadow.py
"""无边框窗口的 Windows 原生阴影（DWM）。

问题：窗口是 `FramelessWindowHint`，没有系统边框 ⇒ 白底上看不出窗口边界，也没有边缘阴影。

做法（系统能力，不自绘阴影）：
1) 给窗口补上 `WS_THICKFRAME`（DWM 只为"有可调边框"的窗口投阴影）；
2) `DwmExtendFrameIntoClientArea(1,1,1,1)` 留 1px 边框 ⇒ DWM 画系统阴影、边缘仍可拖动缩放；
3) 在 `nativeEvent` 里吞掉 `WM_NCCALCSIZE`（返回 0）⇒ 非客户区压成 0，外观依旧无边框。

刻意不处理的两种情形：
- 最大化时把 `WM_NCCALCSIZE` 交回系统，避免内容被顶出屏幕（经典坑）；
- 非 Windows 平台直接返回 False（保留原外观，不影响启动）。

用户可在配置里把 `ui.native_shadow` 置 false 关掉（个别显卡驱动/多屏组合表现异常时）。
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger(__name__)

WM_NCCALCSIZE = 0x0083
GWL_STYLE = -16
WS_THICKFRAME = 0x0004_0000
WS_CAPTION = 0x00C0_0000
WS_MAXIMIZEBOX = 0x0001_0000
WS_MINIMIZEBOX = 0x0002_0000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


class _Margins(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _dwmapi():
    return ctypes.WinDLL("dwmapi")


def enable_native_shadow(widget) -> bool:
    """给无边框窗口启用 DWM 阴影；失败只记日志并返回 False（不阻断启动）。"""
    if sys.platform != "win32":
        return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        wanted = style | WS_THICKFRAME | WS_CAPTION | WS_MAXIMIZEBOX | WS_MINIMIZEBOX
        if wanted != style:
            user32.SetWindowLongW(hwnd, GWL_STYLE, wanted)
        margins = _Margins(1, 1, 1, 1)
        result = _dwmapi().DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        if result != 0:
            # 组合被关闭（如桌面窗口管理器停用）时返回非 0；不是错误路径，仅提示
            logger.info("DwmExtendFrameIntoClientArea 返回 %s（可能已禁用 DWM）", result)
            return False
        # 让样式改动立即生效（否则要等窗口下次显示/尺寸变化）
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        logger.info("已启用 DWM 原生阴影（hwnd=%s）", hwnd)
        return True
    except Exception:  # noqa: BLE001  视觉增值功能，任何失败都不该影响启动
        logger.exception("启用 DWM 原生阴影失败（保持无阴影外观）")
        return False


def handle_native_event(event_type, message):
    """Qt `nativeEvent` 的 Windows 分支：吞掉 `WM_NCCALCSIZE`，返回 (True, 0)。

    返回 ``None`` 表示"不处理，交回 Qt/系统"。最大化时也交回系统：
    否则带 `WS_THICKFRAME` 的窗口在最大化后会按边框外扩，内容被顶出屏幕。
    """
    if sys.platform != "win32":
        return None
    try:
        if bytes(event_type) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return None
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != WM_NCCALCSIZE or not msg.wParam:
            return None
        if _user32().IsZoomed(msg.hWnd):
            return None
        return True, 0
    except Exception:  # noqa: BLE001  原生消息处理失败时交回 Qt，不能抛进事件循环
        logger.debug("处理原生消息失败", exc_info=True)
        return None
