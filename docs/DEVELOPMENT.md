# 开发与更新规范 · DEVELOPMENT

本文件约定 SmartProof 的协作方式：代码规范、Git 工作流、隐私红线、版本发布与更新流程。目标是让项目能安全、可持续地推送到 GitHub 并长期维护。

## 1. 隐私红线（最高优先级）

- **任何密钥、真实配置、日志、缓存、用户文稿都不得进入 Git。**
- 提交前自检：`git status` 看清单，`git grep -nI -E "sk-[A-Za-z0-9]{16,}" --cached` 扫密钥。
- 需要新配置项时：**同时更新 `src/config/*.example.json` 模板**，模板里 `api_key` 永远留空。
- API Key 只允许存在本机被 gitignore 的 `MWindow_config.json`，**禁止硬编码进任何 `.py` / `.txt` / `.spec`**。
- 一旦怀疑密钥泄露（曾明文入库、截图外发等），立即到 DeepSeek 后台**吊销并重建**。

## 2. 代码规范

- Python 3.10+，遵循 PEP 8；缩进 4 空格；文件 UTF-8。
- 类型注解：新写的公共函数建议加类型标注（现存代码逐步补齐即可，不强制一次性重构）。
- **禁止裸 `except:`**：至少 `except Exception as e:` 并记录/上报，避免吞掉 COM 错误。
- COM 使用铁律：
  - `pythoncom.CoInitialize()` / `CoUninitialize()` 成对，且在**使用 Word 对象的同一线程**内。
  - `word.ScreenUpdating = False` 必须在 `try/finally` 中恢复为 `True`。
  - 不跨线程长期持有 Word `Range`/`Field` 对象。
- 命名与注释：注释解释「为什么」，不解释「做了什么」；不要留 `--修改N` 这类历史批注（迁移到 Git 历史与 CHANGELOG）。
- 删除死代码：`word_image_processor_1.py`、`word_image_processor_备份.py`、`src/test.py`、大段被注释的旧逻辑，确认无用后删除，靠 Git 找回历史。

## 3. 目录约定

- 源码：`src/`
- 文档：`docs/`
- 运行时隐私数据：`src/config/`（已 gitignore，仅提交 `*.example.json`）
- 打包配置：根目录 `SmartProof.spec`

## 4. Git 工作流（GitHub 规范）

**分支模型（轻量 GitHub Flow）**
- `main`：始终可运行、可发布。受保护，禁止直接 push（推送 GitHub 后在仓库设置里开启分支保护）。
- `feat/xxx`、`fix/xxx`、`refactor/xxx`：功能/修复分支，从 `main` 切出，完成后开 PR 合回。

**提交信息（Conventional Commits）**
```
<type>(<scope>): <subject>

feat(navigator): 题注扫描移到独立 COM 线程，消除刷新卡顿
fix(monitor): 恢复 ScreenUpdating 异常路径未复位的问题
refactor(api): 统一走 config.get_api_key()，移除明文读取
docs: 补充打包说明
chore: 清理 word_image_processor 冗余副本
```
type 取值：`feat / fix / refactor / perf / docs / test / chore`。subject 用中文或英文均可，一句话说清「做了什么、为什么」。

**PR 要求**
- 一个 PR 只做一件事；标题遵循 Conventional Commits。
- 描述里写清动机、改动点、如何验证（含 UI 手动验证步骤）。
- 涉及 Word 交互的改动，必须在真实 Word 文档上手动验证 golden path + 边界。

## 5. 版本与发布规范

- 版本号 `MAJOR.MINOR.PATCH`（语义化）：
  - MAJOR：不兼容的重大改动；MINOR：新增功能且向后兼容；PATCH：修复。
- 单一版本源（已落地）：应用版本统一在 `src/__version__.py`（`__version__` / `APP_NAME` / `AUTHOR` / `APP_TITLE`），`main.py` 与主窗口标题均从此导入；插件版本以各自 `plugin.json` 为准，界面「关于」从清单动态读取，禁止在代码里硬编码版本号。
- 每次发布在 `CHANGELOG.md` 追加条目（Added / Changed / Fixed / Removed）。
- 打 Git tag：`git tag -a v3.1.0 -m "..."` 并推送；GitHub Release 附打包好的 exe（放 Release 资产，**不进仓库**）。

## 6. 更新（升级）机制建议

当前无自动更新。若要面向用户分发，建议分阶段：
1. **先做**：GitHub Release + 版本号检查（启动时请求 releases/latest，比对本地 VERSION，提示下载）。低风险、无需签名。
2. **再做**：增量更新/静默下载（需处理 exe 占用、回滚）。
3. 代码签名（EV/OV 证书）以避免 SmartScreen 拦截——工业级分发绕不开。

## 7. 测试规范

- 现状：无自动化测试，仅 `src/test.py` 手动脚本。
- 目标：对**纯逻辑**优先补单测（不依赖 Word）：`split_trailing_chars`、`difflib` 差异区间外扩与 token 映射、`caption_cache` 指纹与增删、`api_logger` 缓存命中、`ignore_cache` 哈希。
- COM/Word 相关用手动测试清单（见下）+ 可选 `pywinauto` 冒烟。
- 手动验证清单（每次改动 Word 交互后跑一遍）：
  - [ ] 打开含图/表/公式的论文，光标移动触发校对，红色标记位置正确
  - [ ] 句含图片/公式时「应用」走手动提示分支，不破坏对象
  - [ ] 题录导航：刷新、跳转、返回、保存后页码更新
  - [ ] 图片工具箱：裁剪居中后原图无损
  - [ ] 托盘最小化/恢复、热键、设置保存生效

## 8. 待办 / 技术债（Backlog）

- [ ] 题录扫描移出 GUI 线程（perf，见 ARCHITECTURE §5）
- [ ] API Key 加密路径接通或明确弃用（security）
- [ ] 校对缓存从 `api_calls.log` 剥离到独立 SQLite（perf/正确性）
- [ ] 清理冗余文件与 `--修改N` 批注（chore）
- [ ] 统一版本号来源（chore）
- [ ] 补 LICENSE（推送前必须）
- [ ] 核对 `ignore_cache.py` 第 72 行 `self._save_cache()` 悬空调用（bug）
