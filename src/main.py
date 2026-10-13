# main.py
import sys

from PySide6.QtWidgets import QApplication

from api_client import DeepSeekClient
from config_manager import ConfigManager
from ignore_cache import IgnoreCacheManager
from main_window import MainWindow
from memory_trimmer import start_periodic_trim, delayed_trim #   定时清理内存占用

APP_NAME = "Word智能伴侣"
VERSION = "3.0"
AUTHOR = "Xu Hui"

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    config = ConfigManager()
    api = DeepSeekClient(config)
    ignore_mgr = IgnoreCacheManager()
    window = MainWindow(config, api, ignore_mgr)

    # 立即释放一次内存
    delayed_trim(delay=2.0)   # 可根据实际情况调整延迟时间 delay s后清理内存
    # 启动后台定时清理（默认每 10 分钟）
    start_periodic_trim(interval=600)   # 可根据需要调整间隔 600 s间隔

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()