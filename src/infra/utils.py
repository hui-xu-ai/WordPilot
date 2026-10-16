# utils.py
import os
import sys


def resource_path(relative_path):
    """获取资源绝对路径，兼容开发与打包环境"""
    if getattr(sys, 'frozen', False):          # 是否打包运行
        base_path = sys._MEIPASS
    else:
        # infra/utils.py -> 上一级为 src/ 根目录（不再依赖启动时的工作目录）
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)