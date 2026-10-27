# 打包与运行 · BUILD

> 本文件取代旧的 `src/编译指令.txt`（该文件含明文 API Key，已被 gitignore，不要再提交）。

## 环境准备

```bash
# 建议使用独立虚拟环境打包，避免污染开发环境
python -m venv build_env
build_env\Scripts\activate          # Windows

pip install -r requirements.txt
pip install pyinstaller
```

依赖清单见根目录 `requirements.txt`（PySide6 / pywin32 / cryptography / pillow / piexif / numpy / requests / keyboard）。

## 生成 Word COM 类型库（修复智能提示/常量报错）

```bash
python -m win32com.client.makepy "Microsoft Word 16.0 Object Library"
```

若 Office 不是 16.0，可先执行：

```bash
python -c "from win32com.client import gencache; gencache.EnsureModule('{00020905-0000-0000-C000-000000000046}', 0, 8, 7)"
```

## 打包

```bash
# 标准打包（使用项目根目录的 WordPilot.spec）
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean WordPilot.spec

# 若 UPX 未加入 PATH，指定 UPX 目录以压缩体积
.venv\Scripts\python.exe -m PyInstaller --upx-dir "D:\Tool\upx" --clean WordPilot.spec
```

产物在 `dist/WordPilot/`（`WordPilot.exe` + `_internal/`）。`build/` 为中间产物，二者均已 gitignore。

## spec 要点（WordPilot.spec）

- 入口 `src/main.py`，`pathex=['src']`。
- 打包资源（全部来自 `src/assets/`，复制进包内 `assets/`，运行期用 `resource_path()` 读取，不依赖开发目录）：
  - `tray_icon.png`、`icon_float.png` —— 托盘、标题栏 18px、建议窗图标
  - `icons/*.svg`、`themes/*`、`i18n/*.json`、`skins/*` —— 界面图标 / 主题 / 语言包 / 桌宠皮肤
  - 插件清单 `plugins/*/plugin.json` + 各插件 `i18n/`
- 程序图标：`icon='src/assets/icon.ico'`（16~256 多尺寸）。
- 配置模板：只打包 `MWindow_config.example.json` 与 `navigator_config.example.json`；
  **真实配置（含 API Key）绝不入包**，由程序首次运行时在 exe 同级 `config/` 生成。
- `console=False`（无控制台窗口），启用 UPX。

## 运行前置条件

- Windows 系统，已安装并可正常启动 **Microsoft Word**（工具通过 COM 附着到已打开的 Word）。
- 首次运行会在 exe 同级 `config/` 生成 `.key` 与配置；填入 DeepSeek API Key 后功能方可联网。

## 分发前自检清单

1. `dist/WordPilot/_internal/` 里**没有** `MWindow_config.json`、`.key`、`api_calls.log`、`*.db`；
2. 双击 `WordPilot.exe` 能启动，托盘图标为 `03-jade` 品牌图；
3. 设置 → API 里填入自己的 Key 后「测试连接」通过；
4. Word 打开文档后纠错/翻译/改写可用；
5. 打包机与干净机器各试一次（确认不依赖开发目录）。

## 分发注意

- exe 未做代码签名时，用户下载运行可能触发 SmartScreen 拦截，需说明「仍要运行」。工业级分发建议购买代码签名证书。
- 发布走 GitHub Release 资产，**不要把 exe / dist 提交进仓库**。
- 与隐私相关的数据清单见 [`PRIVACY.md`](PRIVACY.md)。
