# 发布说明 · RELEASE

本文件是「改版本号 → 打包 → 隐私复核 → 打 tag → 推送 GitHub / 发 Release」的执行口径。
每次发版照此走一遍，最后把结果补到文末「发版记录」。

## 0. 版本号管理

| 项 | 位置 | 说明 |
| --- | --- | --- |
| 版本号唯一来源 | `src/__version__.py` 的 `__version__` | 语义化版本 `MAJOR.MINOR.PATCH` |
| 变更记录 | `CHANGELOG.md` | Keep a Changelog 格式，必须有 `## [X.Y.Z]` 条目 |
| 一致性校验 | `python tools/version.py --check [--tag]` | 校验前两者一致，`--tag` 再校验最新 git tag |
| 运行时展示 | 设置 → 关于 | 读 `__version__` + `APP_TITLE` |

规则：**不兼容的数据/配置格式变更 → MAJOR；向后兼容的新功能 → MINOR；修复 → PATCH。**
改版本号只改 `src/__version__.py` 一处，其它文件不允许再写死版本号
（`tests/test_release_metadata.py` 会检查 README 与 CHANGELOG 是否跟上）。

## 1. 发版步骤（按顺序）

```powershell
# 1) 改版本号 + 写 CHANGELOG，然后校验
.venv\Scripts\python.exe tools\version.py --check

# 2) 全量验证
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
pwsh -NoProfile -File tools\quality-gate.ps1

# 3) 打包（先确认没有残留进程占用 dist\config，否则会 PermissionError）
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean WordPilot.spec

# 4) 冒烟：启动 exe 10 秒不崩，然后按 PID 结束并清掉冒烟产生的 config
Start-Process dist\WordPilot\WordPilot.exe
Stop-Process -Id <PID>          # 只按 PID，禁止按进程名批量杀
Remove-Item -Recurse -Force dist\WordPilot\config

# 5) 压缩产物 + 记哈希（Release 附件）
Compress-Archive -Path dist\WordPilot\* -DestinationPath WordPilot-v3.2.0-win64.zip
Get-FileHash WordPilot-v3.2.0-win64.zip -Algorithm SHA256

# 6) 打 tag 并校验一致
git tag -a v3.2.0 -m "WordPilot v3.2.0"
.venv\Scripts\python.exe tools/version.py --check --tag
```

## 2. 隐私复核（推送前必做）

复核对象：`git ls-files` 全量被跟踪文件 + `git log --all -- <隐私文件>` 历史。

| 检查 | 判断标准 |
| --- | --- |
| 运行时隐私文件是否被跟踪 | `.key` / `MWindow_config.json` / `api_calls.log` / `user.qss` / `Image.docx` 均不得出现 |
| 用户资产是否被跟踪 | `config/plugins/**`、`config/references/**` 不得出现 |
| 数据库 / 日志 | `*.db`、`*.db-wal`、`*.log` 不得出现 |
| 疑似密钥 | 源码无 `sk-*` 实值、无 `*.pem`、`api_key` 非空字面量 |
| 本机路径 | 无 `C:\Users\<真实用户名>` 之类的绝对路径 |
| 打包产物 | 只含 `*.example.json`，无真实配置 |
| CI | `.github/workflows/ci.yml` 在 windows-latest 上跑单测 + quality-gate 通过 |

自动闸门：`pwsh -NoProfile -File tools/quality-gate.ps1`（隐私 + 体积 + 测试 + 文件行数）。

## 3. 推送 GitHub（首次）

```powershell
# 在 GitHub 建空仓库（不要勾选 README/LICENSE，避免与本地历史冲突）
git remote add origin https://github.com/<你的账号>/WordPilot.git
git push -u origin main
git push origin --tags
```

README 里的徽章与链接占位 `OWNER` 需替换为真实账号/组织名：

```powershell
(Get-Content README.md -Raw -Encoding utf8) -replace 'OWNER', '<你的账号>' |
  Set-Content README.md -Encoding utf8 -NoNewline
```

## 4. 发布 Release

1. 上传 `WordPilot-vX.Y.Z-win64.zip`；
2. 正文直接引用下面「发布要点」，再补该版本的 `CHANGELOG` 小节；
3. 附上 SHA256，提示未签名可能触发 SmartScreen。

### 发布要点（可复用）

- 面向中文毕业论文/科技论文的 Word 辅助工具，Windows + 本机 Microsoft Word；
- 需要自己的 API Key（设置 → API，支持任何 OpenAI 兼容端点，默认 DeepSeek）；
- 数据全部留在本机 `config/`，只有待处理文本外发；详见 `docs/PRIVACY.md`；
- 未做代码签名，SmartScreen 可能提示「仍要运行」；
- 解压到可写目录运行（软件要在同级目录写 `config/`），不要放 `C:\Program Files`。

## 5. 回滚

- 代码：`git revert <commit>` 或 `git reset --hard <上一个 tag>`（谨慎，团队协作时用 revert）；
- 版本：删除错误 tag（本地 `git tag -d vX.Y.Z`，远端 `git push origin :refs/tags/vX.Y.Z`）后重打；
- 用户数据：`config/plugins/**` 是用户资产，回滚**不需要**清空；配置格式变更时须在启动时做向后兼容迁移。

## 6. 后续可选

- [ ] 安装包（Inno Setup / MSIX）与代码签名；
- [ ] 首启向导（引导填写 API Key）；
- [ ] 拆分 `reference/plugin.py`、`settings_page.py` 等超长文件；
- [ ] 打包体积优化：`excludes` 掉未用到的 Qt 模块（当前 103 MB）；
- [ ] 自动更新（检查 GitHub Release 版本号）。

## 发版记录

### v3.2.0（2026-10-28）

| 项 | 结果 |
| --- | --- |
| 产物 | `dist/WordPilot/WordPilot.exe` + `_internal/`，合计 103 MB |
| 冒烟 | 启动 10s 存活，随后按 PID 结束；冒烟 `config/` 已删除 |
| 资源抽查 | `assets/i18n/*.json`、`assets/templates/Image.docx`、`plugins/*/i18n/*.json` 均在包内 |
| 真实配置 | 不在包内（仅 `*.example.json`） |
| 单测 | 895 passed（1 skipped） |
| quality-gate | FAIL=0 |
| 主题 | 界面语言收口（默认提示词不落盘、抽屉/状态栏/日志随语言重画）、Image.docx 模板随包 |

### v3.1.0（2026-10-27）

首个可分发版本：微内核 + 5 个插件、主题与皮肤、提示词版本管理、API 监控、中英双语、
PyInstaller 打包（103 MB）。隐私隔离复核通过；仅 `*.example.json` 入包。
