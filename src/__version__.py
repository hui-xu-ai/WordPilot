# src/__version__.py
"""项目全局元信息的唯一来源（single source of truth）。

任何需要展示/上报版本号的地方都从这里导入；
插件自身版本以各插件的 plugin.json 为准，与本文件无关。
"""

APP_NAME = "Word智能伴侣"
__version__ = "3.0"
AUTHOR = "Xu Hui"

# 供窗口标题 / 日志 / 错误上报使用的完整标识
APP_TITLE = f"{APP_NAME} v{__version__} - {AUTHOR}"
