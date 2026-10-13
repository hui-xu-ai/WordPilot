# 架构说明 · ARCHITECTURE

本文梳理 SmartProof 的模块划分、关键数据流、线程/COM 模型，以及当前已知的性能与正确性问题，作为后续开发的参照。

## 1. 总体结构

程序是一个常驻托盘的 PySide6 桌面应用，通过 **Windows COM（pywin32）** 附着到用户**已打开的** Word 实例（`GetActiveObject("Word.Application")`），不自己新建 Word 进程。

```
                ┌──────────────────────────────────────────┐
   main.py ───▶ │ MainWindow (main_window.py)               │
                │  · 装配 UI / 菜单 / 托盘                    │
                │  · 持有 config / api / ignore_mgr          │
                │  · 启动 WordMonitor 线程                    │
                │  · 打开各工具对话框                          │
                └───────┬───────────┬───────────┬────────────┘
                        │           │           │
          ┌─────────────▼──┐  ┌─────▼──────┐  ┌─▼──────────────────┐
          │ WordMonitor     │  │ 工具对话框  │  │ FloatingButton     │
          │ (QThread)       │  │ Navigator  │  │ (建议卡片/悬浮按钮) │
          │ 轮询 Selection  │  │ ImageTool  │  └────────────────────┘
          └───────┬─────────┘  └─────┬──────┘
                  │                  │
        ┌─────────▼──────────┐  ┌────▼─────────┐
        │ DeepSeekClient     │  │ CaptionCache │
        │ (api_client.py)    │  │ (SQLite)     │
        └─────────┬──────────┘  └──────────────┘
                  │
        ┌─────────▼──────────┐
        │ api_logger.py      │  日志即缓存：proof 结果写入 api_calls.log
        └────────────────────┘
```

## 2. 模块职责

| 模块 | 职责 | 关键点 |
| --- | --- | --- |
| `main.py` | 入口，创建 QApplication、Config、API、MainWindow，启动内存定时清理 | `memory_trimmer` 周期回收内存 |
| `main_window.py` | 主窗口、菜单、托盘、剪贴板处理、装配所有子功能 | 关闭窗口默认最小化到托盘，线程继续跑 |
| `word_monitor.py` | **核心**：后台线程轮询光标句、分词(`TokenizedSentence`)、句子完成判定、调用校对、把差异标红 | 见 §4 数据流 |
| `api_client.py` | 封装 DeepSeek `chat/completions`，提供 `proof_text/translate/rewrite` | 直接读 `config["api_key"]` 明文；强制 `thinking:disabled` |
| `api_logger.py` | 调用日志写入 + **以日志文件充当校对缓存** | `get_cached_suggestion` 扫描最近 N 条日志匹配 |
| `word_navigator_dialog.py` | 题录导航：扫描 SEQ 域→列表→跳转/返回；保存后自动刷新页码 | 见 §5 性能问题 |
| `caption_cache.py` | 题注 SQLite 缓存，指纹 = (路径, 大小, mtime) | WAL 模式 |
| `word_image_processor*.py` | 图片无损裁剪：借隐藏 Word 工作文档 `Image.docx` 处理 | 存在 `_1` 与 `_备份` 冗余副本 |
| `image_reshaper_widget.py` | 图片工具箱 UI | |
| `suggestion_window.py` | 悬浮按钮 + 建议卡片（接受/忽略/翻译等） | |
| `settings_dialog.py` | 设置对话框（API Key、提示词、外观、热键等） | |
| `config_manager.py` | 配置读写，内置 Fernet 加解密 API Key | **加解密路径未被实际调用**，见 §6 |
| `hotkey_manager.py` | `keyboard` 库全局热键 | 依赖管理员权限时行为需注意 |
| `ignore_cache.py` | 忽略句子库（存 sha256 哈希） | |
| `word_constants.py` | Word COM 常量字典 | |
| `utils.py` | `resource_path` 兼容开发/打包资源路径 | |

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

1. **API Key 加密形同虚设**：`config_manager` 提供 `get_api_key/set_api_key`（Fernet），但 `api_client.py` 直接 `config.get("api_key")` 取**明文**；实际磁盘上的 `MWindow_config.json` 里 Key 也是明文。加密路径没接上 → 要么统一走 `get_api_key()`，要么明确放弃加密。
2. **缓存即日志**：`api_calls.log` 同时承担「日志」和「校对缓存」两职，清空日志＝清缓存；`get_cached_suggestion` 每次线性扫描文件尾部，量大时变慢。建议独立缓存表（SQLite，已有 `caption_cache` 可参照）。
3. **冗余/脏文件**：`word_image_processor_1.py`、`word_image_processor_备份.py`、`src/test.py` 属开发遗留，推送前应确认哪个是正式实现并清理。
4. **`ignore_cache.py` 结构损坏风险**：文件中 `_save_json` 定义了两次、`self._save_cache()` 在无对应方法时被调用（第 72 行，位于类体缩进中），需核对是否运行期报错。
5. **裸 `except:`** 广泛存在，会吞掉 COM 真实错误，排障困难。

## 7. 配置与数据落盘位置

- 打包运行：`config/` 位于 exe 同级目录（`sys.executable.parent`）。
- 源码运行：`config/` 位于 `src/config/`（`__file__` 同级）。

因此源码开发时的隐私数据都落在 `src/config/`，已被 `.gitignore` 覆盖。
