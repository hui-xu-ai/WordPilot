# main.py
"""程序入口：只做「建内核 -> 注册服务 -> 建外壳 -> 挂 UI -> 加载插件」。

这里不出现任何具体功能的装配代码；新增功能请写到 plugins/ 下的插件包里。
"""
import logging
import sys
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from __version__ import APP_NAME, __version__, AUTHOR  # noqa: F401  全局元信息唯一来源
from core.kernel import Kernel
from app.main_window import MainWindow
from infra.memory_trimmer import start_periodic_trim, delayed_trim  #   定时清理内存占用


def _install_failure_hooks() -> None:
    """打包配置是 console=False：未捕获异常必须落盘，否则用户侧现象只剩"点了没反应"。

    三处都装：主线程异常、后台线程异常（监控线程/热键钩子只有这里能看到）、Qt 自身告警。
    """
    from PySide6.QtCore import qInstallMessageHandler

    from core.services.config_manager import CONFIG_DIR

    logger = logging.getLogger("smartproof.unhandled")
    logger.setLevel(logging.ERROR)
    if not logger.handlers:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(CONFIG_DIR / "errors.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
            logger.addHandler(handler)
        except OSError:
            pass  # 配置目录不可写时退化为标准 excepthook，不阻断启动

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.error("未捕获异常", exc_info=(exc_type, exc, tb))

    def _thread_hook(args):
        logger.error("线程未捕获异常: %s", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    def _qt_handler(mode, context, message):
        text = str(message)
        if "propagateSizeHints" in text:   # 无边框窗口的已知无害告警
            return
        logger.error("Qt: %s", text)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook
    qInstallMessageHandler(_qt_handler)


def main():
    _install_failure_hooks()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)

    # 1) 启动内核：注册 config / ai_client / ignore / caption_cache / word_bridge / hotkeys
    kernel = Kernel().boot()

    # 1.5) 热键回调编组：keyboard 库在钩子线程里回调，必须送回 Qt 主线程再执行
    #      （否则插件回调会跨线程动 QWidget / Word COM，随机崩或 RPC 失败）
    hotkey_service = kernel.services.get("hotkeys")
    if hotkey_service is not None:
        hotkey_service.set_dispatcher(lambda fn: QTimer.singleShot(0, fn))

    # 2) 建外壳（窗口、托盘、菜单栏骨架）
    window = MainWindow(kernel)

    # 2.5) 主题：把「渲染好的 QSS 写到 QApplication」这一动作交给内核服务，
    #      并监听 config/user.qss 实现保存后热重载
    theme = kernel.services.get("theme")
    if theme is not None:
        theme.set_apply_hook(app.setStyleSheet)
        theme.reload()
        theme.watch_user_qss()

    # 3) 把 UI 贡献点实现交给内核（同时注册窗口治理服务），然后加载插件
    #    插件在 activate() 中调用 ctx.ui.add_tool(...) 注册自己的界面入口
    kernel.attach_ui(window.ui)
    # 主窗口纳入治理：置顶策略、关闭转隐藏、几何记忆统一由窗口服务负责
    window.register_to_window_manager()
    kernel.plugins.load_all()

    # 4) 插件加载完毕，外壳绑定能力并渲染插件贡献的菜单项
    window.bind_plugins()

    # 5) 所有插件都注册完热键后再统一绑定（避免重复注册）
    hotkeys = kernel.services.get("hotkeys")
    if hotkeys is not None:
        hotkeys.apply()

    # 立即释放一次内存
    delayed_trim(delay=2.0)   # 可根据实际情况调整延迟时间 delay s后清理内存
    # 启动后台定时清理（默认每 10 分钟）
    start_periodic_trim(interval=600)   # 可根据需要调整间隔 600 s间隔

    window.show()
    exit_code = app.exec()
    kernel.shutdown()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
