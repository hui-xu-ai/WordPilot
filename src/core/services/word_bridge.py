"""Word COM 统一入口（内核级基础服务）。

所有对 Word 对象模型的访问必须经过本模块，以便：
- 集中处理「Word 未运行」等附加失败，给出可读错误；
- 集中管理 COM 线程初始化（CoInitialize/CoUninitialize 成对）；
- 集中管理 ScreenUpdating 开关（异常路径也能恢复）。

注意：COM 对象不可跨线程持有。在使用 Word 对象的线程内调用 com_initialized()。
"""

from core.services.i18n import message

import pythoncom
import win32com.client


class WordNotRunningError(RuntimeError):
    """未检测到正在运行的 Word 实例。"""


def get_word_app():
    """附着到用户已打开的 Word 实例（不新建进程）。

     Raises: WordNotRunningError —— Word 未运行或 COM 附着失败。
    """
    try:
        return win32com.client.GetActiveObject("Word.Application")
    except Exception as e:
        raise WordNotRunningError(message("word.error.not_running_doc")) from e


def attach_word_app():
    """等价于 GetObject(Class=...)，语义与旧图片处理器一致。"""
    try:
        return win32com.client.GetObject(Class="Word.Application")
    except Exception as e:
        raise WordNotRunningError(message("word.error.not_running")) from e


def get_active_document():
    """返回当前活动文档；无活动文档时返回 None。"""
    try:
        return get_word_app().ActiveDocument
    except Exception:
        return None


def foreground_hwnd() -> int:
    """当前前台窗口句柄（0 表示取不到）。单独抽出来，便于单测注入。"""
    try:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:                      # noqa: BLE001
        return 0


def cursor_hwnd() -> int:
    """鼠标所在窗口句柄（0 表示取不到）。用户刚点过引文，鼠标位置比焦点更可靠。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return 0
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        return int(user32.WindowFromPoint(point) or 0)
    except Exception:                      # noqa: BLE001
        return 0


def root_hwnd(hwnd: int) -> int:
    """根祖先窗口（GA_ROOT）。Word 的 `Window.Hwnd` 是 MDI 子窗口，前台/鼠标给的是顶层，
    直接比较永远不相等，必须比到根。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetAncestor.restype = wintypes.HWND
        return int(user32.GetAncestor(wintypes.HWND(int(hwnd)), 2) or hwnd)
    except Exception:                      # noqa: BLE001
        return int(hwnd)


def active_window(text=..., word=None):
    """**用户当前在操作的那个 Word 窗口**（不是 Word 自认为的 ActiveDocument）。

    为什么需要：同一个 Word 进程里开着两篇文档时，`Application.ActiveDocument` /
    `Application.Selection` 会停留在旧窗口（用户现场：切到另一篇后程序仍按旧文档判断，
    仓库不跟着切、点了也不弹）。

    匹配顺序（都用**根祖先**比较，Word 的 Window.Hwnd 是 MDI 子窗口）：
    1. 前台窗口 —— 用户刚点过文档，通常就是它；
    2. 鼠标所在窗口 —— 前台被任务栏/别的程序抢走时的兜底（实测会拿到任务栏）；
    3. 都匹配不上才退回 `word.ActiveWindow`。
    """
    window, _source = active_window_with_source(text, word)
    return window


def window_visible(window) -> bool:
    """Word 窗口是否对用户可见。

    图片工具箱会用 `Documents.Open(Visible=False)` 打开隐藏的 `Image.docx` 干活，
    且不恢复用户原来的活动文档 —— 于是 `ActiveDocument` 变成那篇隐藏文档。参考助手
    必须只认**可见**的窗口，否则会一直按隐藏文档判断（用户现场：文档识别错、点引用不弹）。
    """
    try:
        return bool(getattr(window, "Visible", True))
    except Exception:                      # noqa: BLE001
        return True


def visible_windows(word) -> list:
    """Word 里所有**可见**的文档窗口（隐藏的工具箱工作文档会被剔除）。"""
    try:
        windows = [window for window in word.Windows if window_visible(window)]
    except Exception:                      # noqa: BLE001
        return []
    return windows


def active_window_with_source(text=..., word=None):
    """**用户当前在操作的那个 Word 窗口**（不是 Word 自认为的 ActiveDocument）。

    为什么需要：同一个 Word 进程里开着两篇文档时，`Application.ActiveDocument` /
    `Application.Selection` 会停留在旧窗口（用户现场：切到另一篇后程序仍按旧文档判断，
    仓库不跟着切、点了也不弹）。

    匹配顺序（都用**根祖先**比较，Word 的 Window.Hwnd 是 MDI 子窗口）：
    1. 前台窗口 —— 用户刚点过文档，通常就是它；
    2. 鼠标所在窗口 —— 前台被任务栏/别的程序抢走时的兜底；
    3. 都匹配不上时用 ActiveWindow，但它必须可见；不可见就取第一个可见窗口。
    隐藏窗口（图片工具箱的 `Image.docx`）一律不参与。
    """
    if word is None:
        word = text if text is not ... else None
    if word is None:
        try:
            word = get_word_app()
        except Exception:                  # noqa: BLE001
            return None, "无 Word"
    windows = visible_windows(word)
    candidates = [hwnd for hwnd in (foreground_hwnd(), cursor_hwnd()) if hwnd]
    roots = {root_hwnd(hwnd) for hwnd in candidates}
    if roots and windows:
        for window in windows:
            try:
                hwnd = int(getattr(window, "Hwnd", 0) or 0)
            except Exception:              # noqa: BLE001
                continue
            if not hwnd:
                continue
            if hwnd == candidates[0] or root_hwnd(hwnd) == root_hwnd(candidates[0]):
                return window, "前台窗口"
            if hwnd in candidates or root_hwnd(hwnd) in roots:
                return window, "鼠标位置"
    try:
        active = word.ActiveWindow
    except Exception:                      # noqa: BLE001
        active = None
    if active is not None and window_visible(active):
        return active, "ActiveWindow"
    if windows:
        return windows[-1], "可见窗口兜底"
    return None, "无可见窗口"


def active_window_document(word=None):
    """前台 Word 窗口对应的文档；取不到返回 None。"""
    window = active_window(word)
    try:
        return window.Document if window is not None else None
    except Exception:                      # noqa: BLE001
        return None


class com_initialized:
    """线程内 COM 初始化的上下文管理器（CoInitialize/CoUninitialize 成对）。

    用法：
        with com_initialized():
            ... 访问 Word COM ...
    """

    def __enter__(self):
        pythoncom.CoInitialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pythoncom.CoUninitialize()
        return False


class screen_updating_off:
    """临时关闭 Word 屏幕刷新，退出时无论是否异常都恢复。

    用法：
        with screen_updating_off(word_app):
            ... 批量 COM 操作 ...
    """

    def __init__(self, word_app):
        self._app = word_app

    def __enter__(self):
        self._app.ScreenUpdating = False
        return self._app

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._app.ScreenUpdating = True
        except Exception:
            pass  # Word 已关闭等场景下允许失败
        return False
