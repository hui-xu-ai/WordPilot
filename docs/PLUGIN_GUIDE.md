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
| `ctx.windows` | WindowManager：打开子窗口一律走它，禁止自己 new Dialog |
| `ctx.theme` | 主题服务：`token(key)` / `diff_palette()` |
| `ctx.skins` | 桌宠皮肤服务：皮肤包发现与渲染素材 |
| `ctx.tr` | 多语言查词：key 自动带 `<plugin_id>.` 前缀 |
| `ctx.register_capability(name, obj)` | 对外暴露能力 |
| `ctx.data_dir` | 插件私有目录 `config/plugins/<id>/`，自动创建 |
| `ctx.logger` | `logging.Logger`，名为 `plugin.<id>` |

插件基类还自带两个便利方法：
- `self.tr(key)`：等价于 `ctx.tr(key)`，未激活时原样返回 key；
- `self.subscribe(event, handler)`：订阅事件，**deactivate 时自动退订**，
  插件侧不需要自己保存退订闭包。

### 内核服务一览

| 服务名 | 类型 | 用途 |
| --- | --- | --- |
| `config` | `ConfigManager` | 配置读写 + API Key 加密 |
| `i18n` | `I18nService` | 多语言：`tr()` 界面语言 / `prompt_tr()` 提示词语言 |
| `theme` | `ThemeService` | 设计令牌 + QSS；`token()` / `render()` / `diff_palette()` |
| `skins` | `SkinService` | 桌宠皮肤包：`available()` / `part_svg()` / `layout()` |
| `ai_client` | `DeepSeekClient` | `proof_text` / `translate` / `rewrite` / `clipboard_proof` |
| `word_bridge` | module | `get_word_app()` / `com_initialized()` / `screen_updating_off()` |
| `ignore` | `IgnoreCacheManager` | 忽略句增删查 |
| `caption_cache` | `CaptionCache` | 题注 SQLite 缓存 |
| `proof_cache` | `ProofCache` | 校对结果 SQLite 缓存（线程安全） |
| `hotkeys` | `HotkeyService` | `register(name, default_combo, cb, owner=)` |
| `window_manager` | `WindowManager` | 窗口治理：`open/register/close_owner/set_topmost` |
| `ui` | `UiRegistry` 实现 | `add_tool` / `add_tray_action` / `add_page` / `status` |

> **访问 Word 一律走 `word_bridge`**，不要直接 `win32com.client.GetActiveObject`——
> 集中入口才能保证 COM 初始化与 `ScreenUpdating` 成对恢复。

---

## 3. UI 贡献点

```python
# label 一律传 i18n key（如 "tool_label"），不是写死的中文；
# 语言包放 plugins/<id>/i18n/<lang>.json，内核加载插件时自动注册命名空间。
ctx.ui.add_tool("tool_label", self.open, order=100, plugin_id=ctx.id)
ctx.ui.add_tray_action("tray_label", self.open, order=100, plugin_id=ctx.id)
ctx.ui.add_page("page_label", self._build_page, order=100, icon="🧩")  # 主界面导航页
ctx.ui.status(self.tr("status.done"))   # 写入主窗口状态栏
```

- 主界面信息架构：顶部工具栏 + 左侧导航 + QStackedWidget 内容区 + 状态栏；
  `add_tool` 的入口落在「插件」页与托盘菜单，`add_page` 独占一个导航页。
- `order` 越小越靠前；现有取值：navigator `10`、image_toolbox `20`、API 监控 `50`、reference `60`。
- 卸载插件时，内核会自动清理该 `plugin_id` 注册的全部菜单项与页面。
- 无界面环境（单测 / 冒烟）下 `ctx.ui` 是 `NullUiRegistry`，所有调用静默生效。

### 多语言约定

- **启用规则**：内置插件默认启用（plugin.json 的 `enabled_by_default`）；
  安装到 `plugins_user/` 的插件默认不启用，用户必须在「插件管理」面板勾选，
  勾选后才会 activate、上轨、上桌宠菜单。排序由面板写入 `plugins.order`。
- 语言包是纯 JSON：`assets/i18n/<lang>.json`（内核/外壳）与 `plugins/<id>/i18n/<lang>.json`（插件私有）；
  插件词条会自动加 `<plugin_id>.` 命名空间，互不撞键。
- 回退链：当前语言 → 基准语言(zh_CN) → key 本身（并记 warning）。
- 常驻界面（窗口/对话框）实现 `retranslateUi()` 并订阅 `i18n.language_changed`。
- 发给 AI 的提示词用 `i18n.prompt_tr(key)`——它走「提示词语言」，
  与界面语言互不影响（界面可以是英文，校对指令仍用中文）。

## 4. 事件总线

```python
ctx.events.subscribe("proofread.toggled", self._on_toggled)  # 返回退订闭包
ctx.events.emit("my_feature.done", payload)
```

约定事件名 `<插件或模块>.<动作>`；处理器签名 `handler(event_name, payload)`。
现有事件：`proofread.toggled`、`proofread.state_changed`（idle/error/busy/disabled）、
`ai.client_changed`、`i18n.language_changed`、`theme.changed`、`skin.changed`、`kernel.shutdown`。

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
2. spec 已自动逐文件收集 `plugins/*/plugin.json`、`plugins/*/i18n/*.json`、
   `assets/themes/*`、`assets/i18n/*.json`、`assets/skins/*/*`（**一律逐文件展开**，
   禁止目录级 datas，否则同名文件会互相覆盖只剩最后一份）；
3. 若插件带了自己的资源目录，参照皮肤包的写法往 spec 里加一条 glob 规则。

---

## 7. 现成范例

| 想做什么 | 看哪个插件 |
| --- | --- |
| 后台线程 + 悬浮 UI + 全局热键 + 能力暴露 | `plugins/proofread/` |
| 非模态工具对话框 | `plugins/navigator/` |
| 带工作文档的模态工具箱 | `plugins/image_toolbox/` |
| 占位 / 开发中功能如何声明 | `plugins/reference/`（注意 `enabled_by_default: false`） |
