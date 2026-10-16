# word_bridge.py
"""Word COM 统一入口（内核级基础服务）。

所有对 Word 对象模型的访问必须经过本模块，以便：
- 集中处理「Word 未运行」等附加失败，给出可读错误；
- 集中管理 COM 线程初始化（CoInitialize/CoUninitialize 成对）；
- 集中管理 ScreenUpdating 开关（异常路径也能恢复）。

注意：COM 对象不可跨线程持有。在使用 Word 对象的线程内调用 com_initialized()。
"""
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
        raise WordNotRunningError("未检测到正在运行的 Word，请先打开 Word 文档") from e


def attach_word_app():
    """等价于 GetObject(Class=...)，语义与旧图片处理器一致。"""
    try:
        return win32com.client.GetObject(Class="Word.Application")
    except Exception as e:
        raise WordNotRunningError("未检测到 Word 应用程序，请先打开 Word") from e


def get_active_document():
    """返回当前活动文档；无活动文档时返回 None。"""
    try:
        return get_word_app().ActiveDocument
    except Exception:
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
