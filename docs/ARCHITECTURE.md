# 架构说明 · ARCHITECTURE

本文梳理 SmartProof 的模块划分、关键数据流、线程/COM 模型，以及当前已知的性能与正确性问题，作为后续开发的参照。

## 1. 总体结构

程序是一个常驻托盘的 PySide6 桌面应用，通过 **Windows COM（pywin32）** 附着到用户**已打开的** Word 实例（`GetActiveObject("Word.Application")`），不自己新建 Word 进程。

架构采用 **逻辑分层 + 微内核 + 插件化**（非进程级分离——跨进程封装 COM 对桌面工具收益极低，却会大幅增加复杂度）：

```
┌─────────────────────────────────────────────────────────────┐
│ 前端 app/          只有 PySide6，不碰 COM / API / 缓存      │
│   main_window     外壳：窗口 / 托盘 / 菜单 / 剪贴板工具      │
│   ui_registry     UI 贡献点的 Qt 实现（add_tool / add_tray） │
│   settings_dialog / monitor_window                        │
├───────────────────────────────────────────────────────────┤
│ 微内核 core/                 事件总线 ─── 前后端解耦        │
│   kernel         生命周期：boot → attach_ui → plugins      │
│   plugin_manager 清单发现 / 依赖拓扑 / 加载 / 卸载          │
│   event_bus      纯 Python 发布订阅                        │
│   sdk/           PluginBase / PluginContext / UiRegistry   │
│   services/      config(Fernet) / ai_client / word_bridge │
│                  / i18n / theme / skins / window_manager  │
│                  / ignore / caption_cache / proof_cache   │
│                  / hotkeys                                │
├───────────────────────────────────────────────────────────┤
│ 插件 plugins/      每个功能一个自包含包（可含自己的 UI）    │
│   proofread(含桌宠) / navigator / image_toolbox /        │
│   reference(预留)                                         │
├───────────────────────────────────────────────────────────┤
│ assets/           themes/ + i18n/ + skins/（皮肤包）       │
│ infra/            hotkey 服务 / 内存回收 / 资源路径        │
└───────────────────────────────────────────────────────────┘
```

后端（core + plugins 的纯逻辑部分）不依赖 PySide6，可脱离界面单独测试；
插件只拿到 `ctx` 一个对象，内核演进不会波及插件代码。
**新增功能 = 在 `plugins/` 下新增一个包，内核与外壳零改动**（见 `docs/PLUGIN_GUIDE.md`）。

## 2. 模块职责

| 模块 | 职责 | 关键点 |
| --- | --- | --- |
| `main.py` | 入口：建内核 → 注册服务 → 建外壳 → 挂 UI → 加载插件 | 不含任何具体功能装配代码 |
| `core/kernel.py` | 内核生命周期：boot / attach_ui / reload_ai_client / shutdown | 无业务逻辑、无 Qt 依赖 |
| `core/plugin_manager.py` | 扫描 `plugin.json`、按 `deps` 拓扑排序、加载/卸载 | 单插件激活失败不影响其余 |
| `core/event_bus.py` | 纯 Python 发布订阅 | 处理器抛异常被隔离 |
| `core/sdk/` | `PluginBase` / `PluginContext` / `UiRegistry` 协议 | 插件唯一需要 import 的层 |
| `core/services/config_manager.py` | 配置读写 + **Fernet 加密 API Key** | 历史明文读取时自动迁移 |
| `core/services/ai_client.py` | DeepSeek `chat/completions`，提供 `proof_text/translate/rewrite` | 强制 `thinking:disabled` |
| `core/services/word_bridge.py` | **Word COM 唯一入口**：`get_word_app` / `com_initialized` / `screen_updating_off` | 见 §3 |
| `core/services/api_logger.py` | 调用日志写入 | 纯日志，缓存已迁出 |
| `core/services/proof_cache.py` | 校对结果 SQLite 缓存（WAL，线程安全） | 键统一为正文 body_text |
| `core/services/i18n.py` | 多语言：JSON 语言包 + 热切换 | 界面语言与提示词语言解耦 |
| `core/services/theme_service.py` | 设计令牌 + QSS 渲染 + user.qss 热重载 | 令牌 JSON + 模板 |
| `core/services/skin_service.py` | 桌宠皮肤包：skin.json 布局解析 + SVG 占位符渲染素材 | 纯 Python，零 Qt |
| `core/services/window_manager.py` | 窗口治理：层级仲裁 / 单例 / 几何记忆 / 关闭语义 | win32 置顶唯一出口 |
| `core/services/caption_cache.py` | 题注 SQLite 缓存，指纹 = (路径, 大小, mtime) | WAL 模式 |
| `core/services/ignore_cache.py` | 忽略句子库（存 sha256 哈希） | 已修悬空调用 |
| `core/services/hotkey_service.py` | `keyboard` 库全局热键，插件按需注册 | 依赖管理员权限时行为需注意 |
| `core/services/word_constants.py` | Word COM 常量字典 | |
| `app/main_window.py` | 外壳：顶部工具栏 + 左侧导航 + QStackedWidget 内容区 + 状态栏 | 关闭窗口默认最小化到托盘 |
| `app/ui_registry.py` | 收集插件的界面入口（工具 / 托盘 / 页面）并供外壳渲染 | label 存 i18n key |
| `app/settings_dialog.py` | 设置对话框（API、提示词、界面、外观） | 界面语言与提示词语言分离 |
| `app/monitor_window.py` | API 调用监控（内嵌为主界面内容页） | |
| `plugins/proofread/` | 实时校对：监控线程 + 桌宠窗口 + 替换逻辑 + 热键 | 桌宠见 §8，校对见 §4 |
| `plugins/navigator/` | 题录导航：扫描 SEQ 域→列表→跳转/返回 | 见 §5 性能问题 |
| `plugins/image_toolbox/` | 图片工具箱：借隐藏文档 `Image.docx` 批量裁剪居中 | |
| `plugins/reference/` | 参考文献助手（开发中，默认不启用） | 对应 `work/` 下两个测试文档 |
| `infra/utils.py` | `resource_path` 兼容开发/打包资源路径 | |
| `infra/memory_trimmer.py` | 定时回收内存 | |

## 3. 线程与 COM 模型

- **GUI 线程**：PySide6 事件循环、所有窗口/对话框。
- **监控线程**：`WordMonitor(QThread)`，独立 `pythoncom.CoInitialize()`，每 ~0.2s 轮询一次 `word.Selection`。COM 对象跨线程使用需各自初始化，当前实现是在本线程内重新 `GetActiveObject`。
- **导航对话框**：在 GUI 线程内用 `QTimer.singleShot` 分批扫描域，避免一次性长阻塞卡死界面。

> COM 要点：Word 的对象模型对并发访问敏感。任何跨线程持有 Word Range/Field 对象都要小心生命周期，`ScreenUpdating` 的开关必须成对出现（异常路径也要恢复）。

## 4. 实时校对数据流

1. 监控线程取光标所在句 `sentence_range`。
2. `TokenizedSentence` 用 `Range.Words` 遍历，把内嵌图片/公式替换为 `[IMG0]/[EQ1]` 占位符，得到 `pure_text`。
3. 判定「句子刚完成」：上一轮该句未以句末标点结束、这一轮结束了 → 触发校对（并查忽略库）。
4. `proof_sentence()`：分离句尾标点 → 先查 `api_calls.log` 缓存 → 未命中调 `api.proof_text()` → 恢复句尾。
5. `mark_text_differences()`：用 `difflib` 对比原文与修正文，字符区间**外扩 2 字**后映射回 token，对应 Word Range 打红色波浪下划线；再清除占位符/句尾上的误标。
6. `suggestion_ready` 信号 → 悬浮卡片展示建议，用户可「应用」（纯文本直接替换；含图片/公式则提示手动改）。

另有「选中整句」触发路径：当上一次是光标、这次选中内容恰等于整句时，直接校对该句。

## 5. 已知性能问题：题录刷新慢 & 干扰 Word

**现象**：题录自动刷新慢，且刷新期间 Word 卡顿。

**根因分析**（详见 `word_navigator_dialog.py`）：

1. **逐域 COM 往返**：`_scan_fields_batch` 对 `doc.Fields` 逐个 `Item(i)` 并读 `field.Code.Text`。COM 调用是跨进程 marshalling，每次都有固定开销；域数量大时累积明显。
2. **页码获取昂贵**：`para_range.Information(3)`（wdActiveEndPageNumber）会触发 Word **重新分页/重排**，是全套操作里最慢的一步；缓存命中后若 `update_pages=True` 仍对每条题注逐一取页码。
3. **在主 GUI 线程分批**：虽用 `QTimer` 切片避免假死，但每批仍在 GUI 线程执行 COM 调用，与用户操作 Word 抢占同一 COM 通道 → 表现为「干扰 Word、卡顿」。
4. **`ScreenUpdating` 频繁开关** 也会引发重绘抖动。

**改进方向**（供后续实施，未改代码）：
- 扫描改到**独立 COM 线程**（QThread + 本线程 CoInitialize），与 GUI/用户操作解耦。
- 一次性读取，减少往返：用 `doc.Fields` 的批量文本或 `Range.Find` 定位 SEQ，避免 `Item(i)` 循环。
- 页码：默认**不实时取页**，仅在用户显式「更新页码」或保存后延迟一次性批量取；或用 `ComputeStatistics`/书签策略替代逐条 `Information(3)`。
- 缓存指纹已用 mtime，可进一步只重扫「变更区间」做增量更新。

## 6. 已知正确性 / 一致性问题

### 已解决（P2 / P3）
1. ~~**API Key 加密形同虚设**~~ → 已统一走 `get_api_key()/set_api_key()`（Fernet），历史明文读取时自动加密回写。
2. ~~**冗余/脏文件**~~ → `word_image_processor_1.py`、`word_image_processor_备份.py`、`src/test.py` 已删除（git 历史可找回）。
3. ~~**`ignore_cache.py` 悬空调用**~~ → 文件重写，删除重复 `_save_json` 与被注释代码包裹的 `self._save_cache()`。
4. ~~**改 Key 需重启**~~ → `open_settings` 重建客户端后由内核广播 `ai.client_changed`，各插件自动刷新引用。
5. ~~**`Ctrl+Shift+Z` 光标校对热键失效**~~ → 原 `handle_suggestion(cur, corrected)` 少传两个参数必然 TypeError，被裸 except 吞掉；重构后插件内部直连，已修复。

### 仍待处理
1. **裸 `except:`** 仍有残留（主要在 navigator 等未触及的模块），会吞掉 COM 真实错误，排障困难。

### 已在重构批中解决（追加）
6. ~~**缓存即日志 / 缓存键不一致**~~ → 独立 `proof_cache.py`（SQLite WAL），
   `proof_sentence` 与 `proof_text` 统一以 body_text 为键。
7. ~~**版本号三处漂移**~~ → 统一到 `src/__version__.py`，插件读自身 plugin.json。

## 7. 配置与数据落盘位置

- 打包运行：`config/` 位于 exe 同级目录（`sys.executable.parent`）。
- 源码运行：`config/` 位于 `src/config/`（`__file__` 同级）。

因此源码开发时的隐私数据都落在 `src/config/`，已被 `.gitignore` 覆盖。

## 8. GUI 重设计要点（P1~P5 已落地）

- **窗口治理**：所有窗口（含插件窗口）经 `WindowManager` 打开，
  统一父子关系、置顶层级（NORMAL/TOPMOST/PALETTE/OVERLAY）、单例与关闭语义；
  win32 `SetWindowPos(SWP_NOACTIVATE)` 是唯一置顶出口。
- **主题**：`assets/themes/tokens.<name>.json`（设计令牌）+ `base.qss`（@token 模板）
  + `config/user.qss`（用户覆盖，热重载）；界面代码禁止再写内联 setStyleSheet。
- **多语言**：JSON 语言包 + 内核 `i18n` 服务，界面语言与提示词语言独立，
  热切换走 `i18n.language_changed` 事件 + `retranslateUi()`。
- **桌宠**：`assets/skins/<id>/` 皮肤包（skin.json + SVG 分部件）；
  桌宠本体 + 建议气泡合并为单个受管窗口 `proofread.pet`，
  主体颜色即状态色（绿/红/黄/灰），带眨眼与呼吸动画。
- **主界面信息架构**：顶部工具栏（校对开关/置顶钉/清理/设置）+
  左侧导航（仪表盘/剪贴板工具/插件/API 监控）+ QStackedWidget 内容区 + 状态栏；
  插件可通过 `ctx.ui.add_page()` 贡献独占导航页。
