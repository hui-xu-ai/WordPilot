"""Word COM 统一入口（内核级基础服务）。

所有对 Word 对象模型的访问必须经过本模块，以便：
- 集中处理「Word 未运行」等附加失败，给出可读错误；
- 集中管理 COM 线程初始化（CoInitialize/CoUninitialize 成对）；
- 集中管理 ScreenUpdating 开关（异常路径也能恢复）。

注意：COM 对象不可跨线程持有。在使用 Word 对象的线程内调用 com_initialized()。
"""

import logging
import os
import time

from core.services.i18n import message

import pythoncom
import win32com.client

logger = logging.getLogger(__name__)


class WordNotRunningError(RuntimeError):
    """未检测到正在运行的 Word 实例。"""


def _attach_word():
    """附着到**已在运行**的 Word（`GetActiveObject` 只查 ROT，不会新建进程）。"""
    try:
        return win32com.client.GetActiveObject("Word.Application")
    except Exception as e:                 # noqa: BLE001 - 统一转成可读错误
        raise WordNotRunningError(message("word.error.not_running_doc")) from e


def _alive(word) -> bool:
    """COM 对象是否还能用（退出的 Word 会留下一个"取属性就 AttributeError"的死对象）。"""
    try:
        int(word.Documents.Count)
        return True
    except Exception:                      # noqa: BLE001
        return False


def _attach_word_ready(timeout: float = 3.0):
    """等残留实例退干净再附着：`Quit()` 是异步的，立刻附着会拿到刚销毁的死对象。"""
    deadline = time.monotonic() + timeout
    last_error = None
    while True:
        try:
            word = _attach_word()
        except WordNotRunningError as exc:
            last_error = exc
        else:
            if _alive(word):
                return word
            last_error = WordNotRunningError(message("word.error.not_running_doc"))
        if time.monotonic() >= deadline:
            raise last_error
        time.sleep(0.2)


def _rot_document_paths() -> list[str]:
    """运行对象表里"已经打开的 Word 文档"的完整路径（排除系统 moniker）。

    多实例 / 僵尸注册表时，`GetActiveObject` 可能拿到死对象或错的实例；但每篇打开的文档
    都在 ROT 里登记了自己，从文档回溯到它的 Application 才拿得到**用户正在用的那个 Word**。
    """
    paths: list[str] = []
    try:
        rot = pythoncom.GetRunningObjectTable()
        bind = pythoncom.CreateBindCtx(0)
        monikers = list(rot.EnumRunning())
    except Exception:                      # noqa: BLE001 - 枚举失败就按"没有文档"处理
        logger.debug("枚举运行对象表失败", exc_info=True)
        return paths
    for moniker in monikers:
        try:
            display = moniker.GetDisplayName(bind, None)
        except Exception:                  # noqa: BLE001
            continue
        if not display or display.startswith("!") or not os.path.isabs(display):
            continue
        if os.path.splitext(display)[1].lower() not in (".docx", ".doc", ".docm", ".dotm"):
            continue
        paths.append(display)
    return paths


def _attach_from_documents(paths=None, opener=None):
    """按"已经打开的文档"附着到它所属的 Word 应用；找不到返回 None。

    `paths` / `opener` 只为测试注入，正常调用不传。
    """
    applications = _applications_of_open_documents(paths, opener)
    if applications:
        logger.info("从已打开的文档附着 Word 应用")
        return applications[0]
    return None


def _applications_of_open_documents(paths=None, opener=None) -> list:
    """每篇打开的文档所属的 Word 应用（跳过插件草稿文档与失效应用）。"""
    candidates = _rot_document_paths() if paths is None else paths
    open_document = win32com.client.GetObject if opener is None else opener
    applications = []
    for path in candidates:
        try:
            document = open_document(path)
            application = document.Application
        except Exception:                  # noqa: BLE001 - 这篇绑定失败就试下一篇
            continue
        if is_work_document(document):     # 图片工具箱的草稿文档不算"用户的 Word 文档"
            continue
        if _alive(application):
            applications.append(application)
    return applications


#: Word 主窗口的 Win32 类名（各版本一致）：用它判断"这个窗口是不是 Word"
WORD_WINDOW_CLASS = "OpusApp"

#: 最近一次落到的 Word 进程号。程序自己点按钮时前台是 WordPilot，
#: 所以要多实例场景里记住上一次命中的进程，否则会挑到另一篇文档所在的那个 Word。
_last_word_pid = 0


def _window_class(hwnd) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(wintypes.HWND(int(hwnd)), buffer, 256)
        return buffer.value
    except Exception:                      # noqa: BLE001
        return ""


def _window_pid(hwnd) -> int:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                    ctypes.POINTER(wintypes.DWORD)]
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
        return int(pid.value)
    except Exception:                      # noqa: BLE001
        return 0


def _foreground_word_pid() -> int:
    """用户最近一次落过的 Word 窗口属于哪个进程（0 = 没抓到）。"""
    global _last_word_pid
    for hwnd in (foreground_hwnd(), cursor_hwnd()):
        root = root_hwnd(hwnd)
        if root and _window_class(root) == WORD_WINDOW_CLASS:
            pid = _window_pid(root)
            if pid:
                _last_word_pid = pid
                return pid
    return _last_word_pid


def _application_pid(application) -> int:
    """这个 Word 应用给出的窗口属于哪个进程（0 = 取不到）。"""
    for getter in (lambda: application.ActiveWindow.Hwnd,
                   lambda: application.Windows(1).Hwnd):
        try:
            hwnd = int(getter())
        except Exception:                  # noqa: BLE001 - 没有活动窗口/窗口已关闭
            continue
        if hwnd:
            return _window_pid(root_hwnd(hwnd))
    return 0


def _has_visible_user_document(application) -> bool:
    return bool(visible_windows(application))


def _word_applications() -> list:
    """所有可用的 Word 应用：`[(pid, application)]`，按 PID 去重且跳过失效实例。"""
    candidates = []
    try:
        candidates.append(_attach_word())
    except WordNotRunningError:
        logger.debug("注册表里没有 Word 应用对象，改从已打开的文档附着", exc_info=True)
    try:
        candidates.extend(_applications_of_open_documents())
    except Exception:                      # noqa: BLE001 - 枚举失败就只用注册表那个
        logger.debug("枚举已打开的文档失败", exc_info=True)
    applications, seen = [], set()
    for application in candidates:
        if not _alive(application):
            continue
        pid = _application_pid(application)
        if pid and pid in seen:
            continue
        if pid:
            seen.add(pid)
        applications.append((pid, application))
    return applications


def get_word_app():
    """附着到**用户正在用的**那个 Word 实例（不新建进程）。

    Raises: WordNotRunningError —— Word 未运行、COM 附着失败，或拿到的对象已失效。

    多实例（同一台机器开着两个 WINWORD）时按"前台 Word 窗口所属进程"选实例，其次选
    "有可见用户文档"的那个：只认 `GetActiveObject` 会随便挑一个 —— 用户现场正是
    "另一篇文档能自动刷新题注、这一篇不能，甚至完全识别不到"。
    """
    applications = _word_applications()
    if not applications:
        raise WordNotRunningError(message("word.error.not_running_doc"))

    target_pid = _foreground_word_pid()
    if target_pid:
        for pid, application in applications:
            if pid == target_pid:
                return application
    for _pid, application in applications:
        if _has_visible_user_document(application):
            return application

    first = applications[0][1]
    if _recycle_headless_word(first):       # 只剩"没人看得见"的实例：退出草稿实例再附着
        return _attach_word_ready()
    if _alive(first):
        return first
    raise WordNotRunningError(message("word.error.not_running_doc"))


def attach_word_app():
    """等价于 `get_word_app()`：只附着**已在运行**的 Word，绝不新建实例。

    为什么不能用 `GetObject(Class="Word.Application")`（2026-11-13 现场）：Word 没在运行时
    它会**新建一个隐藏实例**（`WINWORD.EXE -Embedding` + `Application.Visible=False`），此后
    `GetActiveObject` 一直返回这个幽灵实例 —— 用户自己打开的文档在**另一个** WINWORD 进程里，
    于是"所有文档都识别不到"（实测：幽灵实例里只有 Image.docx，模板.docx 在另一个进程）。
    """
    return get_word_app()


def _is_headless_word(word) -> bool:
    """这个 Word 是不是"没人看得见、只开着插件草稿"的自动化幽灵实例。"""
    try:
        if bool(word.Visible):
            return False
        documents = list(word.Documents)
    except Exception:                      # noqa: BLE001 - COM 已断开
        return False
    for document in documents:
        if is_work_document(document):
            continue
        if _is_workspace_document(document):
            continue
        return False                       # 有用户文档 ⇒ 不能碰
    return True


def _is_workspace_document(document) -> bool:
    """文档是否落在本应用的运行目录（`config/`）下 —— 那里只放运行时产物，没有用户文档。"""
    try:
        from core.services.config_manager import CONFIG_DIR

        path = _normalize_path(getattr(document, "FullName", ""))
        return bool(path) and path.startswith(_normalize_path(CONFIG_DIR) + os.sep)
    except Exception:                      # noqa: BLE001
        return False


_headless_checked = False


#: 最近一次"程序造成的选区"（题录跳转的高亮），格式 (start, end)。
_programmatic_range: tuple[int, int] | None = None


def mark_programmatic_selection(selection) -> None:
    """登记"当前这个选区是程序造的"（例如题录跳转把目标区间 Select 出来做高亮）。

    为什么要登记：跳转必须把目标区间 `Select()` 出来，用户才看得见跳到了哪（高亮）；
    而智能纠错的监听线程会把"上一轮光标 + 这一轮选中的文字正好等于整句"当成
    "用户在选整句、校对一次" —— 题注整段往往正好是一句话，于是"跳一下就顺手校对了一次"。
    登记过的区间不再被当成用户意图。
    """
    global _programmatic_range
    try:
        _programmatic_range = (int(selection.Start), int(selection.End))
    except Exception:                      # noqa: BLE001 - 登记失败只是可能多校对一次
        logger.warning("登记程序选区失败（该项跳转可能触发一次校对）", exc_info=True)


def selection_is_programmatic(start, end) -> bool:
    """这个选区是不是上面登记的那一个（程序造的），而不是用户手选的。"""
    try:
        return _programmatic_range == (int(start), int(end))
    except Exception:                      # noqa: BLE001
        return False


def _recycle_headless_word(word) -> bool:
    """退出自己造的隐藏 Word 实例（只做一次），好让下次附着回到用户真正的 Word。

    只在这个实例**不可见**、且开着的东西全是插件草稿时动手（`Quit` 不保存任何改动）。
    不做这件事的后果：幽灵实例永久占着 ROT，用户即使打开了 Word 文档也永远识别不到。
    """
    global _headless_checked
    if _headless_checked:
        return False
    _headless_checked = True
    if not _is_headless_word(word):
        return False
    try:
        logger.warning("发现自动化残留的隐藏 Word 实例（只开着草稿文档），退出它以便附着用户的 Word")
        word.Quit(0)                       # 0 = wdDoNotSaveChanges：草稿本来就不该保存
    except Exception:                      # noqa: BLE001 - 退出失败就按原样用
        logger.warning("退出隐藏 Word 实例失败", exc_info=True)
        return False
    return True


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
        logger.warning("获取窗口根祖先失败，退回原窗口句柄", exc_info=True)
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


# 插件登记的工作文档全路径（规范化后）。图片工具箱的 `Image.docx` 是它自己的草稿纸，
# 永远不是"用户正在看的那篇"。登记而不是猜，是因为 `Window.Visible` 会骗人
# （Word 进程本身不可见时，用户文档的窗口也读成 False）。
_work_documents: set[str] = set()


def _normalize_path(path) -> str:
    text = str(path or "")
    return os.path.normcase(os.path.normpath(text)) if text else ""


def register_work_document(path) -> None:
    """登记一个**工作文档**路径（插件自用草稿），它不会被当成"当前文档"。"""
    key = _normalize_path(path)
    if key:
        _work_documents.add(key)


def is_work_document(document) -> bool:
    """这篇文档是不是插件登记过的工作文档（`Image.docx`）。"""
    try:
        return _normalize_path(getattr(document, "FullName", "")) in _work_documents
    except Exception:                      # noqa: BLE001 - 文档已关闭/COM 已断开
        return False


def window_document(window):
    """窗口对应的文档；取不到返回 None（窗口已关闭时会抛 COM 错）。"""
    try:
        return window.Document if window is not None else None
    except Exception:                      # noqa: BLE001
        return None


def _window_items(collection) -> list:
    """把 `word.Windows` 取成列表：COM 集合按 **1-based 下标逐项取**，逐项容错。

    为什么不能 `for window in word.Windows`：Word 的 `Windows` 里会出现**取就抛 COM 错的
    幽灵条目**（2026-11-13 实测 `Windows(3)` 失败），`for` 遍历会整条中断 —— 那些"可见
    窗口"就全丢了，前台窗口机制随之失效，最后退回 `ActiveDocument`（= 工具箱的 Image.docx）。
    """
    try:
        count = int(collection.Count)
    except Exception:                      # noqa: BLE001 - 测试替身是普通 list，没有 Count
        try:
            return list(collection)
        except Exception:                  # noqa: BLE001
            logger.warning("枚举 Word 窗口失败", exc_info=True)
            return []
    items = []
    for index in range(1, count + 1):
        try:
            items.append(collection(index))
        except Exception:                  # noqa: BLE001 - 幽灵窗口，跳过即可
            logger.debug("跳过取不到的 Word 窗口（第 %d 个）", index, exc_info=True)
    return items


def visible_windows(word) -> list:
    """Word 里**用户能看到的**文档窗口（隐藏的、插件工作文档都被剔除）。"""
    try:
        collection = word.Windows
    except Exception:                      # noqa: BLE001 - Word 正在关闭/对象已失效：正常情形
        logger.debug("取 Word 窗口集合失败（Word 可能正在退出）", exc_info=True)
        return []
    windows = []
    for window in _window_items(collection):
        if not window_visible(window):
            continue
        if is_work_document(window_document(window)):
            continue
        windows.append(window)
    return windows


def active_window_with_source(text=..., word=None):
    """**用户当前在操作的那个 Word 窗口**（不是 Word 自认为的 ActiveDocument）。

    为什么需要：同一个 Word 进程里开着两篇文档时，`Application.ActiveDocument` /
    `Application.Selection` 会停留在旧窗口（用户现场：切到另一篇后程序仍按旧文档判断，
    仓库不跟着切、点了也不弹）。

    匹配顺序（都用**根祖先**比较，Word 的 Window.Hwnd 是 MDI 子窗口）：
    1. 前台窗口 —— 用户刚点过文档，通常就是它；
    2. 鼠标所在窗口 —— 前台被任务栏/别的程序抢走时的兜底；
    3. 都匹配不上时用 ActiveWindow，但它必须可见；不可见就取最后一个可见窗口。
    隐藏窗口与插件登记的工作文档（图片工具箱的 `Image.docx`）一律不参与。
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
    if (active is not None and window_visible(active)
            and not is_work_document(window_document(active))):
        return active, "ActiveWindow"
    if windows:
        return windows[-1], "可见窗口兜底"
    return None, "无可见窗口"


def current_document_with_source(word=None):
    """**用户正在看的那篇文档**（真实场景里唯一的文档出口）+ 判定方式。

    优先级：① 前台/鼠标命中的 Word 窗口 → ② Word 的活动窗口 → ③ 最后一个可见窗口
    → ④ `ActiveDocument`（仅当它不是插件工作文档）。

    第 ④ 步是兜底而不是主力：图片工具箱隐藏打开 `Image.docx` 后会把 `ActiveDocument`
    留在它身上（2026-11-13 现场：题注导航扫出"共 0 个题注"、参考助手按 Image.docx 建了
    0 个锚点的索引）。所以工作文档在任何一步都不被选中。
    """
    if word is None:
        try:
            word = get_word_app()
        except Exception:                  # noqa: BLE001 - Word 未运行/COM 断开
            return None, "无 Word"
    window, source = active_window_with_source(word=word)
    document = window_document(window)
    if document is not None:
        return document, source
    try:
        document = word.ActiveDocument
    except Exception:                      # noqa: BLE001 - 没有打开的文档
        return None, "无文档"
    if is_work_document(document):
        logger.debug("当前文档是插件工作文档，已忽略：%s",
                     getattr(document, "Name", "?"))
        return None, "仅工作文档"
    return document, "ActiveDocument"


def current_document(word=None):
    """**用户正在看的那篇文档**；取不到返回 None（细节见 `current_document_with_source`）。"""
    document, _source = current_document_with_source(word=word)
    return document


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
            pass  # silent-ok: Word 关闭时恢复失败
        return False
