# src/__version__.py
"""项目全局元信息的唯一来源（single source of truth）。

任何需要展示/上报版本号的地方都从这里导入；
插件自身版本以各插件的 plugin.json 为准，与本文件无关。
"""

APP_NAME = "Word智能伴侣"
__version__ = "3.0"
AUTHOR = "Xu Hui"

# 标题栏只显示软件名（版本/作者移到设置 →「关于」，避免更新后标题里版本没跟着变）
WINDOW_TITLE = APP_NAME

# 供日志 / 错误上报 / 关于页使用的完整标识
APP_TITLE = f"{APP_NAME} v{__version__} - {AUTHOR}"
