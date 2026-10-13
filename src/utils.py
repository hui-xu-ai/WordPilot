# utils.py
import os
import sys


def resource_path(relative_path):
    """获取资源绝对路径，兼容开发与打包环境"""
    if getattr(sys, 'frozen', False):          # 是否打包运行
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)