import os
import time
import ctypes
import threading
from ctypes import wintypes
import win32com.client

# ---------- 配置 ----------
CONFIG_DIR = "D:\Python\Project\SmartProof\src\config"          # 请改成你的实际 config 目录
IMAGE_DOCX = os.path.join(CONFIG_DIR, "Image.docx")

# ---------- 全局参数 ----------
EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_SHOW   = 0x8002
WINEVENT_OUTOFCONTEXT = 0x0000
SW_HIDE = 0
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

# ---------- 钩子类（带详细日志）----------
class ImageDocHideHookTest:
    def __init__(self, target_process_id):
        self.target_pid = target_process_id
        self._hook = None
        self._running = False
        self._thread = None
        self._user32 = ctypes.windll.user32
        self._proc = None
        self.log = []           # 记录事件顺序
        self.start_time = None

    def _callback(self, hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        if idObject != 0:       # 只关心顶层窗口
            return
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != self.target_pid:
            return

        # 获取标题（可能为空）
        length = self._user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

        # 记录事件
        now = time.time()
        elapsed = round(now - self.start_time, 4) if self.start_time else 0
        log_entry = {
            "event": "CREATE" if event == EVENT_OBJECT_CREATE else "SHOW",
            "hwnd": hwnd,
            "title": title,
            "elapsed": elapsed
        }

        # 是我们的目标文档吗？不仅检查标题，也检查标题或未来标题可能包含 Image.docx
        # 有时标题还没设置，我们仍然要处理（后面会由 SHOW 事件再次进来）
        if "Image.docx" not in title and title != "":
            # 不是目标窗口，但为了调试，也记录一下非目标窗口
            log_entry["action"] = "ignore"
            self.log.append(log_entry)
            return

        # 执行隐藏前记录窗口当前状态（可见性）
        visible = self._user32.IsWindowVisible(hwnd)
        log_entry["visible_before"] = visible

        # 移出屏幕并隐藏
        self._user32.SetWindowPos(
            hwnd, 0, -20000, -20000, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE
        )
        self._user32.ShowWindow(hwnd, SW_HIDE)

        log_entry["action"] = "hide"
        self.log.append(log_entry)

    def start(self):
        self.start_time = time.time()
        WinEventProc = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD
        )
        self._proc = WinEventProc(self._callback)
        self._hook = self._user32.SetWinEventHook(
            EVENT_OBJECT_CREATE,       # 从创建到显示
            EVENT_OBJECT_SHOW,
            0,
            self._proc,
            0,
            0,
            WINEVENT_OUTOFCONTEXT
        )
        if not self._hook:
            print("❌ 安装钩子失败")
            return False
        self._running = True
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        print(f"✅ 钩子已安装，监听 Word 进程 PID={self.target_pid}")
        return True

    def _message_loop(self):
        msg = wintypes.MSG()
        while self._running:
            ret = self._user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1)  # PM_REMOVE
            if ret:
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            self._user32.UnhookWinEvent(self._hook)
            self._hook = None

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._user32.PostThreadMessageW(self._thread.ident, 0, 0, 0)
            self._thread.join(timeout=2)
        if self._hook:
            self._user32.UnhookWinEvent(self._hook)
            self._hook = None
        print("🛑 钩子已停止")

# ---------- 主测试流程 ----------
def main():
    # 1. 连接 Word
    try:
        word = win32com.client.GetObject(Class="Word.Application")
        print("✅ 已连接 Word")
    except Exception:
        print("❌ 请先启动 Word")
        return

    # 2. 获取 Word 主窗口句柄（兼容无文档打开的情况）
    hwnd_main = ctypes.windll.user32.FindWindowW("OpusApp", None)
    if not hwnd_main:
        print("❌ 找不到 Word 主窗口")
        return
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd_main, ctypes.byref(pid))
    print(f"Word PID: {pid.value}")

    # 3. 启动钩子
    hook = ImageDocHideHookTest(pid.value)
    if not hook.start():
        return

    # 4. 先关闭可能已打开的 Image.docx（避免残留）
    try:
        for doc in word.Documents:
            if doc.Name == "Image.docx":
                doc.Close(SaveChanges=False)
                print("⚠️ 已关闭之前残留的 Image.docx")
    except Exception:
        pass

    # 5. 打开我们的工作文档
    print(f"📂 即将打开 {IMAGE_DOCX} ...")
    # 你可以选择 Visible=False 看看是否还闪烁
    try:
        doc = word.Documents.Open(FileName=IMAGE_DOCX, Visible=False)
        print("✅ 打开完成")
    except Exception as e:
        print(f"❌ 打开失败: {e}")
        hook.stop()
        return

    # 6. 等两秒让钩子处理完毕，然后查看日志
    time.sleep(2)

    # 7. 打印诊断日志
    print("\n📋 钩子事件日志（按时间顺序）:")
    for entry in hook.log:
        print(f"  [{entry['elapsed']:>6.4f}s] {entry['event']:6s} | hwnd={entry['hwnd']} | title='{entry['title']}' | visible_before={entry.get('visible_before', 'N/A')} | action={entry['action']}")

    # 8. 检查文档窗口是否在屏幕上
    try:
        win_hwnd = doc.Windows(1).Hwnd
        vis = ctypes.windll.user32.IsWindowVisible(win_hwnd)
        print(f"\n🖥️ 最终窗口可见性: {'可见❌' if vis else '隐藏✅'}")
    except Exception:
        print("\n🖥️ 无法检测文档窗口")

    # 9. 清理
    doc.Close(SaveChanges=False)
    hook.stop()

if __name__ == "__main__":
    main()