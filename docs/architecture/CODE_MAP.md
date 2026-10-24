# WordPilot · 代码地图（CODE_MAP）

> 由 `proj arch map` 从**文件头 docstring**生成；改职责请改文件头，不要改本文件。

## app（8 文件）
应用外壳：无边框窗口、导航轨、标题栏、设置页与页面装配；不含业务逻辑。

| 文件 | 职责 |
|---|---|
| `src/app/__init__.py` | （未写） |
| `src/app/icon_factory.py` | SVG 图标工厂。 |
| `src/app/main_window.py` | 应用外壳（前端）：窗口骨架 / 托盘 / 主题 / 生命周期。 |
| `src/app/nav_rail.py` | 可折叠图标导航轨（NavRail）。 |
| `src/app/pages_mixin.py` | 外壳的页面与导航能力（插件页工厂、导航轨重建、切页、右侧抽屉联动）。 |
| `src/app/side_panel.py` | 内容区右侧「次要界面」抽屉容器（ADR-002）。 |
| `src/app/plugin_manager_page.py` | 插件管理面板（内置页，卡片网格风格）。 |
| `src/app/settings_page.py` | 设置页（主窗口内置页面，取代原先的模态对话框）。 |
| `src/app/title_bar.py` | 自绘标题栏（无边框窗口用，Fluent 风格）。 |
| `src/app/ui_registry.py` | UI 贡献点的 Qt 实现。 |

## core（26 文件）
微内核与内核服务：事件总线、插件生命周期、配置/主题/语言/Word 桥等。

| 文件 | 职责 |
|---|---|
| `src/core/__init__.py` | （未写） |
| `src/core/event_bus.py` | 事件总线（纯 Python，零 Qt 依赖）。 |
| `src/core/kernel.py` | 微内核：只负责「注册内核服务 -> 挂载 UI -> 加载插件 -> 关闭」这条生命周期。 |
| `src/core/plugin_manager.py` | 插件管理器：扫描 plugins/ 目录，按依赖顺序加载/卸载插件。 |
| `src/core/sdk/__init__.py` | 插件 SDK：插件开发者唯一需要 import 的协议层。 |
| `src/core/sdk/context.py` | 插件上下文：插件与内核交互的唯一入口。 |
| `src/core/sdk/plugin_base.py` | 插件清单与插件基类。 |
| `src/core/sdk/ui.py` | UI 贡献点接口。 |
| `src/core/services/__init__.py` | （未写） |
| `src/core/services/api_client.py` | 按「提示词语言」取默认提示词；服务缺失时回退到 key 本身。 |
| `src/core/services/api_logger.py` | 将 DeepSeek 返回的 usage 或自定义字典统一为 {prompt, completion, total} |
| `src/core/services/caption_cache.py` | CREATE TABLE IF NOT EXISTS documents ( id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT UNIQUE NOT NULL, file_size INTEGER NOT NULL, last_modified REAL NOT  |
| `src/core/services/config_manager.py` | 配置管理：读写 MWindow_config.json、Fernet 加密保存 api_key、提供 get/set。 |
| `src/core/services/hotkey_service.py` | 全局热键服务：插件按需注册，服务统一绑定。 |
| `src/core/services/i18n.py` | 国际化服务：JSON 语言包 + tr() 查词 + 运行时热切换。 |
| `src/core/services/ignore_cache.py` | 忽略句子库：以 sha256 哈希记录用户选择忽略的句子。 |
| `src/core/services/proof_cache.py` | 校对结果缓存（SQLite）。 |
| `src/core/services/service_registry.py` | 内核服务定位器。 |
| `src/core/services/skin_service.py` | 桌宠皮肤服务：发现皮肤包、解析布局、产出可渲染的 SVG。 |
| `src/core/services/theme_service.py` | 主题服务：设计令牌 -> QSS 渲染 -> 应用级样式 + 用户覆盖层。 |
| `src/core/services/window_manager.py` | 窗口治理服务：所有顶层窗口的唯一出入口。 |
| `src/core/services/word_bridge.py` | Word COM 统一入口（内核级基础服务）。 |
| `src/core/services/word_constants.py` | Word 常量兼容模块 - 优先使用 win32com 动态常量（完整、最新） - 若缓存损坏则自动回退到硬编码值（稳定、跨版本） |
| `src/core/widgets/__init__.py` | 内核级通用 Qt 控件库：外壳（app/）与插件（plugins/）都可以引用。 |
| `src/core/widgets/monitor_panel.py` | API 调用监控面板（内嵌式）。 |
| `src/core/widgets/switch.py` | iOS/Fluent 风格拨动开关（Switch）。 |

## infra（3 文件）
与业务无关的通用基础设施（日志裁剪等）。

| 文件 | 职责 |
|---|---|
| `src/infra/__init__.py` | （未写） |
| `src/infra/memory_trimmer.py` | 内存自清理模块（含定时清理线程） 提供： - trim_memory() 释放当前进程物理内存工作集 - delayed_trim() 在 delay 秒后于后台线程中执行一次 trim_memory()，避免阻塞主线程，消除对启动速度的影响。 - start_periodic_trim() 启动后台线程，每 inte |
| `src/infra/utils.py` | 获取资源绝对路径，兼容开发与打包环境 |

## plugins（17 文件）
功能插件：智能纠错、题录导航、图片工具、翻译改写等，按插件启停。

| 文件 | 职责 |
|---|---|
| `src/plugins/__init__.py` | （未写） |
| `src/plugins/clipboard/plugin.py` | 翻译改写工具插件。 |
| `src/plugins/image_toolbox/__init__.py` | （未写） |
| `src/plugins/image_toolbox/image_reshaper_widget.py` | 图片裁剪/居中面板。 |
| `src/plugins/image_toolbox/plugin.py` | 图片工具箱插件。 |
| `src/plugins/image_toolbox/word_image_processor.py` | 当工作文档被意外显示时，强制再次隐藏并移出屏幕 |
| `src/plugins/navigator/__init__.py` | （未写） |
| `src/plugins/navigator/plugin.py` | Word 导航助手插件。 |
| `src/plugins/navigator/word_navigator_dialog.py` | 插件版本以 plugin.json 为唯一来源，避免与界面文案漂移。 |
| `src/plugins/proofread/__init__.py` | （未写） |
| `src/plugins/proofread/dashboard_page.py` | 「智能纠错」插件页：校对状态 + 参数 + 缓存清洗 + API 监控。 |
| `src/plugins/proofread/pet_window.py` | 桌宠窗口：桌宠本体 + 建议气泡合并为单个受管窗口。 |
| `src/plugins/proofread/plugin.py` | 智能纠错插件。（原名「实时校对」，P9 与侧边栏「智能纠错」统一） |
| `src/plugins/proofread/suggestion_window.py` | 返回差异片段列表，每个片段包含 original 和 suggest 字符串 |
| `src/plugins/proofread/word_monitor.py` | 将 Word 句子 Range 解析为带对象信息的 token 列表 |
| `src/plugins/reference/__init__.py` | （未写） |
| `src/plugins/reference/plugin.py` | 参考文献助手插件（开发中，默认不启用）。 |
