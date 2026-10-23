# tools/_config_guard.py
"""冒烟/诊断脚本的配置守卫：**只恢复自己动过的键，绝不清空用户配置**。

历史坑（HANDOFF「冒烟污染」）：`smoke_p7.py:109`、`smoke_p7_install.py:54` 用
`config.set("plugins", {})` 收尾——那不是"复位"，而是把用户的 `plugins.disabled` /
`plugins.order` 全部抹掉；`preview_shell.py` 则把语言/折叠状态强行写成固定值。

用法：
    from _config_guard import guard
    restore = guard(kernel.config, ["plugins", "ui_language", "skin"])   # 默认 atexit 自动恢复
    ...
    restore()      # 需要提前恢复也可以手动调
"""
from __future__ import annotations

import atexit


def guard(config, keys, auto: bool = True):
    """快照 keys 当前值，返回恢复函数；缺失（None）的键不写入，避免制造新键。"""
    snapshot = {key: config.get(key) for key in keys}

    def restore() -> None:
        for key, value in snapshot.items():
            if value is not None:
                config.set(key, value)

    if auto:
        atexit.register(restore)
    return restore
