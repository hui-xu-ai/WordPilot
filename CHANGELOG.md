# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。
格式：`## [版本] - 日期`，分类固定为 新增 / 变更 / 修复 / 移除 / 安全。

## [3.2.0] - 2026-10-28

以「界面语言 = 唯一显示标准」为主题的一次系统性收口，同时修掉若干真实缺陷。

### 新增

- **界面文案语言架构**：新增显示层映射 `core/services/ui_labels.py`，把"后端数据"
  与"界面显示"解耦——库名、任务名、提示词版本名、皮肤名、主题名都按界面语言渲染，
  自建内容（用户库名、自定义提示词）永不翻译。
- **语言切换契约测试**：`tests/test_i18n_roundtrip_contract.py` 要求每个 Qt 控件都实现
  `retranslateUi()`，并真实跑「中 → 英 → 中」，切回后文字必须与初始快照逐字一致；
  抽屉与提示词正文（`QTextEdit`）也纳入快照（`test_i18n_drawer_and_status_contract.py`）。
- 打包自带 `assets/templates/Image.docx` 工作文档模板，首次使用**不需要 Word 在运行**。
- `tools/version.py`：版本一致性校验（`--check`、`--check --tag`）。

### 变更

- **显示语言只跟界面语言**：`prompt_language` 配置键废弃（旧值即使为 `zh_CN` 也不再影响文案）；
  校对语言 / 翻译目标 / 纠错语言只用于判断待处理文本的语言。
- 「默认」提示词 = 程序内置文案（中英各一套），**永不写进 config**；
  config 只保存用户另存为的文本（`prompt_store.value_for_save()`）。
- 版本下拉文案改为语言无关 id（`mine-N` / `auto-<时间戳>`）+ 显示时翻译，历史中文名同样可译。
- 皮肤名、主题名、插件简介、存储清单、API 监控表头与任务列全部走语言包。

### 修复

- **Image.docx 报"找不到文件"**：页面构建先判缺失、后创建导致复制逻辑永不执行 →
  改为"先复制模板，再判缺失"，缺失提示也能重译。
- **翻译改写抽屉切语言无效**：抽屉由插件构建、外壳只重译页面 →
  抽屉容器委派 `retranslateUi()` 给插件，外壳新增 `retranslate_side_panels()`。
- **状态/日志切回中文不复原**：navigator、reference、image_toolbox 把渲染好的成品文本
  写进控件 → 改为存「词条 key + 参数」，切语言时逐条重画（成品文本走显式兼容入口）。
- **navigator 文案**：筛选标签原为无引用匿名控件（无法重译）、章节前缀与页码后缀硬编码中文。
- **reference 文案**：统计行/详情开关/notes 区/空备注占位未重译；孤立锚点表头与
  `status.save_failed` 缺 key 显示裸 key。
- `{key}` 占位符与 `I18nService._translate(lang, key, ...)` 形参撞名导致 `TypeError`。
- `create_library()` 吞掉 `ValueError`，导致「仓库名不合法」提示永不出现。
- 英文界面里会显示裸 key（如 `reference.library.default_name`）。
- 存储清单不随语言重算、API 监控面板切语言不刷新。

### 安全

- 发布包与仓库均不含真实配置：`config/` 下密钥、日志、缓存、用户资产全部 gitignore；
  打包仅带 `*.example.json` 模板。

## [3.1.0] - 2026-10-27

首个可分发版本：微内核 + 插件化（智能纠错 / 翻译改写 / 题录导航 / 图片工具 / 参考文献助手），
Fluent 主题、桌宠皮肤、提示词版本管理、API 监控、中英双语、PyInstaller 打包。
