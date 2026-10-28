# 交接文档 · HANDOVER

> 面向接手者：**只读这一篇就能接手**。更细的内容在文末「延伸阅读」。
> 快照时间：2026-10-28　版本：**v3.2.0**　状态：CI 绿、发布包已出。

## 0. 一句话现状

WordPilot 是「面向论文写作的 Microsoft Word 智能助手」（PySide6 + pywin32/COM + 任意 OpenAI 兼容 API），
微内核 + 插件化，中英双语，数据全在本机 `config/`。**v3.2.0 已推送 GitHub 并发布 Release。**

| 项 | 值 |
| --- | --- |
| 仓库 | https://github.com/hui-xu-ai/WordPilot （public，默认分支 `main`，MIT） |
| 版本 | `src/__version__.py` → **3.2.0**；tag `v3.2.0`（另有 `v3.1.0`） |
| 提交 | 316 个，工作区干净 |
| 测试 | **901 passed / 1 skipped**（离线，不需要 Word） |
| CI | GitHub Actions `windows-latest`：pytest + quality-gate + 版本校验 → **passing** |
| 发布产物 | 本地 `D:\Python\Project\WordPilot-release\`（程序目录 103 MB + `WordPilot-v3.2.0-win64.zip` 61.9 MB） |
| Release 资产 | `WordPilot-v3.2.0-win64.zip`，SHA256 `197A8705691883E4F57406254FC968DF9AAEB4D16010B35E0719FBC91DD067C9` |

## 1. 五分钟跑起来

```powershell
git clone https://github.com/hui-xu-ai/WordPilot.git
cd WordPilot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
copy src\config\MWindow_config.example.json src\config\MWindow_config.json   # 填自己的 api_key
.venv\Scripts\python.exe src\main.py          # 或双击 run.bat
```

前置条件：Windows 10/11 + **本机已安装 Microsoft Word**（软件通过 COM 操作 Word）。
运行后到「设置 → API」填 Key（默认端点 `https://api.deepseek.com`、模型 `deepseek-flash`），
点「保存」（只点「测试连接」不会保存，见 §5 坑 6）。

## 2. 架构与目录（判断新代码放哪）

```
src/
├── __version__.py   # 版本号唯一来源（工具：tools/version.py --check）
├── main.py          # 入口：建内核 → 建外壳 → 挂 UI → 加载插件
├── app/             # 前端表现层（PySide6；不碰 COM / API / 缓存）
├── core/            # 微内核：kernel / plugin_manager / event_bus / sdk / services
├── plugins/<id>/    # 每个功能一个插件包（plugin.json + plugin.py + i18n/）
└── assets/          # 品牌图标 / SVG / 主题 / i18n / 桌宠皮肤 / templates(Image.docx)
```

分层红线（违反会被 `tests/` 与 quality-gate 抓）：`app/` 不碰 COM/API/缓存；`core/` 零 Qt 依赖；
**新功能写成插件，不改外壳与内核**。详见 `docs/ARCHITECTURE.md`。

## 3. 五个必须知道的设计决定

1. **界面语言 = 唯一显示标准**：所有界面文案（含内置默认提示词、提示词版本名、皮肤名、表头）
   只跟 `ui_language`。`prompt_language` 配置键**已废弃**。
2. **提示词「默认」= 程序内置文案**：来自语言包 `prompt.<场景>.default`（中英各一套），
   **永不写入 config**（`prompt_store.value_for_save()` 把等于默认的文本存成空串）；
   config 只存用户另存为的版本（`config/plugins/<插件>/prompts/`，属用户资产，永不清理）。
3. **显示层映射**：`core/services/ui_labels.py` 负责「后端值 → 显示文案」（库名、任务名、版本名、皮肤名），
   自建内容（用户库名、自定义提示词）**永不翻译**。
4. **语言切换契约**：每个 Qt 控件类必须实现 `retranslateUi()`；
   `tests/test_i18n_roundtrip_contract.py` 会跑「中→英→中」，要求切回后文字与初始快照逐字一致。
5. **状态/日志不存成品文本**：存「词条 key + 参数」，切语言时重画（`_set_status/_render_logs`）。

## 4. 日常操作

| 要做的事 | 怎么做 |
| --- | --- |
| 跑测试 | `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` |
| 质量闸门 | `pwsh -NoProfile -File tools\quality-gate.ps1`（隐私/体积/行数/测试映射） |
| 版本一致性 | `.venv\Scripts\python.exe tools\version.py --check [--tag]` |
| 打包 | **输出到仓库树之外**：`.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath D:\Python\Project\WordPilot-release WordPilot.spec` |
| 冒烟（保护配置） | `.venv\Scripts\python.exe tools/smoke_packaged.py --exe <产物路径>`（备份 config → 跑 10s → 还原） |
| 加一条界面文案 | 两份语言包同时加 key（`assets/i18n` 或 `plugins/<id>/i18n`），控件只在 `retranslateUi()` 里取词 |
| 加一个功能 | `src/plugins/<new_id>/`（plugin.json + plugin.py + i18n/），不改外壳内核 |
| 发版 | 改 `__version__.py` → 写 `CHANGELOG.md` → `version.py --check` → 打包 → 打 tag `vX.Y.Z` → 推 main/tags → 上传 zip → 正文用 `docs/RELEASE.md` 的「发布要点」 |

## 5. 已知的坑（都踩过，别再踩）

1. **安全软件目录级拦截**：本机装腾讯电脑管家时，`D:\Python\Project\SmartProof\dist\` 整棵树被标记，
   其中的 exe（哪怕是 2 MB 的空白对照程序）双击都弹「Windows 已保护你的电脑」；
   复制到 `D:\Python\Project\` 就不弹 → **判定随目录走**。→ 打包输出放仓库外 + 把该目录加入信任区。
2. **未签名 exe**：`NotSigned`，首次运行可能弹 SmartScreen，点「更多信息 → 仍要运行」；
   它**不会记住**该决定（每次双击都问）。根治只能代码签名。详见 `docs/RELEASE.md` §3.1。
3. **不要反搭 `dist/` 当长期分发目录**：反复打包产生大量未签名新 exe，容易把整棵目录拖进安全软件黑名单。
4. **CI 的 stdout 是 cp1252**：脚本 `print` 中文会抛 `UnicodeEncodeError`（已在 `tools/version.py` 里 reconfigure
   成 utf-8，CI 也加了 job 级 `PYTHONUTF8=1`；新脚本照此办理）。
5. **测试不能依赖 gitignored 目录**：`work/`、`local/`、`src/config/.key`、`src/config/MWindow_config.json`
   在 CI 都不存在。已踩过两次（`.key`、`work/design`），规则：缺文件就 `pytest.skip`，或用临时目录自造。
6. **设置页「保存」才落盘**：只点「测试连接」不会保存 Key；保存后 `api_key` 应是 120 字符、`gAAAAA` 开头的密文。
   另：`save_settings()` 只在 Key **确实改动**时才写它（早期版本每次保存都会把 Key 清空 → 401）。
7. **`Image.docx`**：随包提供空白模板（`assets/templates/Image.docx`），首次使用时复制到 `config/`；
   页面构建**先复制再判缺失**（早期是先报错、于是复制逻辑永不执行）。
8. **杀进程只按 PID**：禁止按进程名批量杀 Word / Python。开发机曾因隐藏 Word 实例占用
   `config/Image.docx` 导致打包失败——先用 COM 只读确认无未保存文档再决定，**动进程前先问用户**。

## 6. 隐私边界（对外承诺，勿破）

唯一外发的是「你要 AI 处理的那段文字」，发往用户自己配置的端点；无遥测。
仓库与发布包**都不含**：`.key`、`MWindow_config.json`、`api_calls.log`、`*.db*`、
`config/plugins/**`、`config/references/**`、`user.qss`、运行日志、`dist/`、`work/`、`local/`。
发布前跑 `tools/quality-gate.ps1`（含隐私检查）；打包产物只应有 `*.example.json`。

## 7. 后续建议（按性价比排序）

1. **代码签名**（Authenticode OV / 微软 Trusted Signing）：彻底消除 SmartScreen 提示，发布体验的关键一步；
2. **安装包**（Inno Setup / MSIX）：替代“解压到可写目录”，并带卸载；
3. **首启向导**：引导填 API Key + 一键测试，减少“打开没反应”的困惑；
4. 拆分超长文件：`plugins/reference/plugin.py`(864)、`app/settings_page.py`(678)、
   `plugins/reference/library_page.py`(624)、`plugins/reference/word_watch.py`(619)（quality-gate 已在告警）；
5. 打包瘦身：103 MB，可 `excludes` 未用到的 Qt 模块；
6. 自动更新（读 GitHub Release 版本号，提示下载）。

## 8. 交接时的人工待办

- [ ] 交接人确认 GitHub 仓库协作者/转移归属已就绪（当前在 `hui-xu-ai` 名下）；
- [ ] 确认 `D:\Python\Project\WordPilot-release\` 的产物已归档（Release 上已有同 SHA256 的 zip）；
- [ ] 若换机器：`.key` **不要**跨机复用（否则旧密钥解不开），让用户重新填 API Key；
- [ ] 本地开发目录 `work/`、`local/`、`dist/`、`src/config/` 都**不入库**，交接只需仓库 + Release。

## 9. 延伸阅读

| 文档 | 内容 |
| --- | --- |
| `README.md` | 功能表、安装方式、配置、隐私边界、构建 |
| `docs/ARCHITECTURE.md` | 分层、数据流、插件协议、关键服务 |
| `docs/PLUGIN_GUIDE.md` | 插件开发 + **界面文案规约**（i18n 必须遵守的规则） |
| `docs/DEVELOPMENT.md` | 开发流程、提交规范、代码风格 |
| `docs/RELEASE.md` | 版本规则、发版步骤、隐私复核、分发坑、回滚 |
| `docs/PRIVACY.md` / `SECURITY.md` | 外发边界、权限清单、漏洞上报 |
| `docs/ROADMAP.md` | 路线图 |
| `CONTRIBUTING.md` | 贡献须知（含五条硬规矩） |
| `CHANGELOG.md` | 变更记录（Keep a Changelog） |

