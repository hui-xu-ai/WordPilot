# main.py
"""程序入口：只做「建内核 -> 注册服务 -> 建外壳 -> 挂 UI -> 加载插件」。

这里不出现任何具体功能的装配代码；新增功能请写到 plugins/ 下的插件包里。
"""
import sys

from PySide6.QtWidgets import QApplication

from __version__ import APP_NAME, __version__, AUTHOR  # noqa: F401  全局元信息唯一来源
from core.kernel import Kernel
from app.main_window import MainWindow
from infra.memory_trimmer import start_periodic_trim, delayed_trim  #   定时清理内存占用


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)

    # 1) 启动内核：注册 config / ai_client / ignore / caption_cache / word_bridge / hotkeys
    kernel = Kernel().boot()

    # 2) 建外壳（窗口、托盘、菜单栏骨架）
    window = MainWindow(kernel)

    # 3) 把 UI 贡献点实现交给内核，然后加载插件
    #    插件在 activate() 中调用 ctx.ui.add_tool(...) 注册自己的界面入口
    kernel.attach_ui(window.ui)
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
