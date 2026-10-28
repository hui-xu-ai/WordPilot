# 贡献指南（Contributing）

感谢你愿意改进 WordPilot。这是一个 **Windows + Microsoft Word(COM)** 的桌面项目，
提交前请先读完本文，可以省掉一轮返工。

## 环境

| 项 | 版本 |
| --- | --- |
| Python | 3.14 |
| PySide6 | 6.11.x |
| 依赖 | `pip install -r requirements.txt`（开发另加 `requirements-dev.txt`） |
| 平台 | Windows 10/11 + 本机已安装 Microsoft Word |

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
copy src\config\MWindow_config.example.json src\config\MWindow_config.json   # 填入自己的 api_key
.venv\Scripts\python.exe src\main.py
```

## 提交前必跑

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider      # 离线单测（不需要 Word）
pwsh -NoProfile -File tools\quality-gate.ps1                    # 隐私 / 体积 / 测试 / 文件行数
.venv\Scripts\python.exe tools\version.py --check               # 版本号与 CHANGELOG 一致
```

改动涉及界面时再加一次冒烟（需要桌面会话）：

```powershell
.venv\Scripts\python.exe tools\smoke_p8.py
```

## 硬规矩

1. **绝不提交用户数据**：`src/config/` 下的 `.key`、`MWindow_config.json`、`api_calls.log`、
   `*.db*`、`plugins/`（用户提示词版本）、`references/`、`Image.docx`、`user.qss`、`*.log`
   全部已被 `.gitignore` 排除。**API Key 不允许出现在源码、测试、提交信息里。**
2. **提交显式列路径**：`git add <具体文件>`，不要 `git add -A`（避免误把运行时文件带进去）。
3. **杀进程只按 PID**：禁止按进程名批量终止 Word / Python（会误杀用户的文档进程）。
4. **不破坏分层**：`app/` 不碰 COM、API 与缓存；`core/` 零 Qt 依赖；
   新功能写成 `plugins/<id>/` 插件包，不改外壳与内核。
5. **界面文案必须进语言包**：中文与英文两份 key 集合一致，英文包里不允许出现中文
   （语言名本身除外）。新增控件必须实现 `retranslateUi()`，否则切语言会残留旧语言
   —— 有专门的契约测试会拦住。

## 新增/修改界面文案的做法

```python
# 1) 语言包：src/assets/i18n/zh_CN.json + en_US.json（插件用 plugins/<id>/i18n/*.json）
"settings.example.title": "示例标题",      // zh_CN
"settings.example.title": "Example title", // en_US

# 2) 控件只在 retranslateUi() 里取词
def retranslateUi(self):
    self.title_label.setText(self.tr("settings.example.title"))
```

**不要**在构造时 `setText(self.tr(...))` 之后就完事——那样切语言不会刷新。

## 提示词的约定

- 「默认」= 程序内置提示词（语言包里的 `prompt.<场景>.default`），按**界面语言**取；
- 默认文案**不写进配置**：保存路径统一走 `prompt_store.value_for_save()`；
- 用户另存为的版本存在 `config/plugins/<插件>/prompts/`，属于用户资产，永不清理。

## 提交信息

```
<类型>(<范围>): <一句话>

<为什么这么改 / 现场是什么问题>
<关键取舍与验证方式>
```

类型：`feat` / `fix` / `refactor` / `test` / `docs` / `build` / `chore`。

## 发布

见 [`docs/RELEASE.md`](docs/RELEASE.md)：改 `src/__version__.py` → 更新 `CHANGELOG.md`
→ `tools/version.py --check` → 打包 → 打 tag `vX.Y.Z` → 写 Release Notes。
