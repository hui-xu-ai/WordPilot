# 发布说明 · RELEASE

本文件记录「打包 → 隐私复核 → 推送 GitHub」这一条链路的执行口径与本次结果。

## 1. 本次打包（v3.1.0）

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean WordPilot.spec
```

| 项 | 结果 |
| --- | --- |
| 产物 | `dist/WordPilot/WordPilot.exe`（6.6 MB）+ `_internal/` |
| 总体积 | 约 103 MB（未做安装包；压缩后预计 60~70 MB） |
| 冒烟 | 启动 exe，进程存活 12s 无崩溃（随后手动结束，并删除冒烟产生的 `dist/WordPilot/config/`） |
| 资源抽查 | `_internal/assets/{tray_icon.png,icon_float.png,i18n/*.json}`、`_internal/plugins/*` 均在 |
| 真实配置 | **不在包内**（只有 `MWindow_config.example.json`、`navigator_config.example.json`） |

## 2. 隐私复核（推送前）

复核对象：`git ls-files` 全量被跟踪文件。

| 检查 | 结果 |
| --- | --- |
| 运行时隐私文件是否被跟踪（`.key` / `MWindow_config.json` / `api_calls.log` / `user.qss` / `Image.docx`） | 无 ✅ |
| 用户资产是否被跟踪（`config/plugins/`、`config/references/`） | 无 ✅ |
| 数据库 / 日志是否被跟踪（`*.db`、`*.log`） | 无 ✅ |
| 源码中的疑似密钥（`sk-*`、`api_key` 非空、`*.pem`） | 无 ✅ |
| 本机绝对路径（`C:\Users\<name>`） | 无 ✅ |
| git 历史是否提交过隐私文件 | 从未（`git log --all -- <file>` 为空）✅ |
| 打包产物是否含真实配置 | 否 ✅ |
| 被跟踪文件体积 Top3 | `tray_icon.png` 0.21MB、`icon_float.png` 0.21MB、`icon.ico` 0.10MB |

本次修复的隔离缺口：`src/config/plugins/`（各插件的提示词版本与个人设置）此前**未**被 gitignore，
现已加入；`.gitignore` 覆盖到 `.key`、配置、日志、数据库、用户资产、QSS、Word 锁文件等。

## 3. 推送 GitHub

仓库当前**没有配置 remote**，首次推送：

```powershell
# 1) 在 GitHub 建一个空仓库（不要勾选 README/LICENSE，避免冲突）
git remote add origin https://github.com/<你的账号>/WordPilot.git
git push -u origin main
git push origin v3.1.0          # 发布标签（CI 会跑离线单测 + quality-gate）
```

发布 Release：

```powershell
# dist/ 与 build/ 已 gitignore，exe 作为 Release 附件上传，不进仓库
Compress-Archive -Path dist\WordPilot\* -DestinationPath WordPilot-v3.1.0-win64.zip
# 然后在 GitHub Release 页面手工上传该 zip，正文可直接引用下方「发布要点」
```

## 4. 发布要点（可直接用作 Release 正文）

- 面向中文毕业论文/科技论文的 Word 辅助工具，Windows + 本机 Word；
- 需要自己的 DeepSeek API Key（设置 → API）；
- 数据全部留在本机 `config/`，只有待处理文本外发；详见 `docs/PRIVACY.md`；
- 未做代码签名，SmartScreen 可能提示「仍要运行」。

## 5. 后续可选

- [ ] 安装包（Inno Setup / MSIX）与代码签名；
- [ ] 首启向导（引导填写 API Key，减少“打开发现没反应”）；
- [ ] 拆 `reference/plugin.py`(845) 与 `settings_page.py`(647) 两个超长文件；
- [ ] 打包体积优化：`excludes` 掉未用到的 Qt 模块（当前 103MB）。
