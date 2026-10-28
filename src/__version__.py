# src/__version__.py
"""项目全局元信息的唯一来源（single source of truth）。

任何需要展示/上报版本号的地方都从这里导入；
插件自身版本以各插件的 plugin.json 为准，与本文件无关。

版本号规则（语义化版本）：
- MAJOR：不兼容的数据/配置格式变更（需要迁移脚本）
- MINOR：向后兼容的新功能
- PATCH：向后兼容的修复

发版流程见 docs/RELEASE.md：改这里 → 更新 CHANGELOG.md → `python tools/version.py --check`
→ 提交 → 打 tag `vX.Y.Z`。
"""

APP_NAME = "WordPilot"
__version__ = "3.2.0"
AUTHOR = "Xu Hui"

# 标题栏只显示软件名（版本/作者移到设置 →「关于」，避免更新后标题里版本没跟着变）
WINDOW_TITLE = APP_NAME

# 供日志 / 错误上报 / 关于页使用的完整标识
APP_TITLE = f"{APP_NAME} v{__version__} - {AUTHOR}"
