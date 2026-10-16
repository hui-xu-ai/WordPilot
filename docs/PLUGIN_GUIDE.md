# 插件开发指南 · PLUGIN GUIDE

SmartProof 采用**微内核 + 插件**架构：内核只提供生命周期与基础服务，
所有具体功能（`proofread` / `navigator` / `image_toolbox` / `reference` …）
都是 `plugins/` 下的独立包。

**新增一个功能 = 新建一个插件包，内核与外壳零改动。**

---

## 1. 三步新建插件

### 第 1 步：建包

```
src/plugins/my_feature/
├── __init__.py
├── plugin.json      清单（必需）
└── plugin.py        入口类
```

包内可以放任意模块：业务逻辑、Qt 界面、数据文件均可。
插件允许自带 UI（外壳只提供窗口骨架与菜单挂载点）。

### 第 2 步：写 `plugin.json`

```json
{
  "id": "my_feature",
  "name": "我的功能",
  "version": "1.0.0",
  "description": "一句话说明这个功能做什么",
  "author": "Xu Hui",
  "entry": "plugin:MyFeaturePlugin",
  "requires": ["config", "ai_client"],
  "deps": [],
  "enabled_by_default": true
}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 唯一 id，**必须与目录名一致**（内核按 `plugins.<id>.<entry_module>` 导入） |
| `entry` | `模块名:类名`，默认 `plugin:Plugin` |
| `requires` | 依赖的**内核服务**名，缺失则拒绝加载并记错 |
| `deps` | 依赖的**其它插件** id，内核按拓扑顺序加载；成环或缺失即报错 |
| `enabled_by_default` | 为 `false` 时默认不启用（如开发中的 `reference`） |

### 第 3 步：写入口类

```python
from core.sdk.plugin_base import PluginBase

class MyFeaturePlugin(PluginBase):
    def activate(self, ctx):
        super().activate(ctx)
        config = ctx.get_service("config")      # 取内核服务
        ctx.ui.add_tool("✨ 我的功能", self.open) # 注册工具菜单项
        ctx.ui.add_tray_action("我的功能", self.open)
        ctx.register_capability("my_feature", self)  # 暴露能力给外壳

    def deactivate(self):
        # 必须释放：线程 / 窗口 / 热键 / 定时器
        super().deactivate()

    def open(self):
        ...
```

**就这些。** 无需修改 `main.py`、`main_window.py` 或任何内核代码。

---

## 2. PluginContext 可用接口

| 成员 | 说明 |
| --- | --- |
| `ctx.get_service(name, default=None)` | 取内核服务 |
| `ctx.require_service(name)` | 取服务，缺失抛 `KeyError` |
| `ctx.config` | 配置管理器（`config.get/set/get_api_key/set_api_key`） |
| `ctx.services` / `ctx.events` / `ctx.ui` | 服务注册器 / 事件总线 / UI 贡献点 |
| `ctx.register_capability(name, obj)` | 对外暴露能力 |
| `ctx.data_dir` | 插件私有目录 `config/plugins/<id>/`，自动创建 |
| `ctx.logger` | `logging.Logger`，名为 `plugin.<id>` |

### 内核服务一览

| 服务名 | 类型 | 用途 |
| --- | --- | --- |
| `config` | `ConfigManager` | 配置读写 + API Key 加密 |
| `ai_client` | `DeepSeekClient` | `proof_text` / `translate` / `rewrite` / `clipboard_proof` |
| `word_bridge` | module | `get_word_app()` / `com_initialized()` / `screen_updating_off()` |
| `ignore` | `IgnoreCacheManager` | 忽略句增删查 |
| `caption_cache` | `CaptionCache` | 题注 SQLite 缓存 |
| `hotkeys` | `HotkeyService` | `register(name, default_combo, cb, owner=)` |
| `ui` | `UiRegistry` 实现 | `add_tool` / `add_tray_action` / `status` |

> **访问 Word 一律走 `word_bridge`**，不要直接 `win32com.client.GetActiveObject`——
> 集中入口才能保证 COM 初始化与 `ScreenUpdating` 成对恢复。

---

## 3. UI 贡献点

```python
ctx.ui.add_tool(label, handler, order=100, icon=None, plugin_id=ctx.id)
ctx.ui.add_tray_action(label, handler, order=100, plugin_id=ctx.id)
ctx.ui.status("操作完成")            # 写入主窗口状态栏
```

- `order` 越小越靠前；现有取值：navigator `10`、image_toolbox `20`、API 监控 `50`、reference `60`。
- 卸载插件时，内核会自动清理该 `plugin_id` 注册的全部菜单项。
- 无界面环境（单测 / 冒烟）下 `ctx.ui` 是 `NullUiRegistry`，所有调用静默生效。

## 4. 事件总线

```python
ctx.events.subscribe("proofread.toggled", self._on_toggled)  # 返回退订闭包
ctx.events.emit("my_feature.done", payload)
```

约定事件名 `<插件或模块>.<动作>`；处理器签名 `handler(event_name, payload)`。
现有事件：`proofread.toggled`、`ai.client_changed`、`kernel.shutdown`。

> **务必订阅 `ai.client_changed`** —— 用户在设置里改了 API Key 后，
> 内核会重建 `ai_client` 并广播该事件，插件需刷新自己持有的引用。

---

## 5. 启停与升级

- **停用**：把插件 id 加进 `config/MWindow_config.json` 的 `plugins.disabled` 列表；
  下次启动时该插件不会被激活（外壳上对应的菜单项自动消失）。
- **升级**：直接替换 `plugins/<id>/` 整个目录并更新 `plugin.json` 的 `version`；
  内核只依赖清单与入口类，不依赖插件内部实现。
- **故障隔离**：单个插件激活抛异常只会被记录，其余插件与主程序照常运行。

---

## 6. 打包注意

插件入口是**动态 import**，PyInstaller 静态扫描不到，新增插件时必须：

1. 在 `SmartProof.spec` 的 `hiddenimports` 里加 `plugins.<id>.plugin`；
2. 确认 `datas` 里保留了 `('src/plugins/*/plugin.json', 'plugins')`（清单随包分发）。

---

## 7. 现成范例

| 想做什么 | 看哪个插件 |
| --- | --- |
| 后台线程 + 悬浮 UI + 全局热键 + 能力暴露 | `plugins/proofread/` |
| 非模态工具对话框 | `plugins/navigator/` |
| 带工作文档的模态工具箱 | `plugins/image_toolbox/` |
| 占位 / 开发中功能如何声明 | `plugins/reference/`（注意 `enabled_by_default: false`） |
