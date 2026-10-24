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
pyinstaller --clean WordPilot.spec

# 若 UPX 未加入 PATH，指定 UPX 目录以压缩体积
pyinstaller --upx-dir "D:\Tool\upx" --clean WordPilot.spec
```

产物在 `dist/WordPilot/`（`WordPilot.exe` + `_internal/`）。`build/` 为中间产物，二者均已 gitignore。

## spec 要点（WordPilot.spec）

- 入口 `src/main.py`，`pathex=['src']`。
- 打包资源：`icon_float.png`、`tray_icon.png`、`config/MWindow_config.json`、`config/navigator_config.json`。
  - ⚠️ 当前 spec 会把 `config/MWindow_config.json` 打进产物。**分发前请确认该文件不含真实 Key**，或改为打包 `MWindow_config.example.json` 并在首次运行时让用户填写。
- `console=False`（无控制台窗口），`icon='src/icon.ico'`，启用 UPX。

## 运行前置条件

- Windows 系统，已安装并可正常启动 **Microsoft Word**（工具通过 COM 附着到已打开的 Word）。
- 首次运行会在 exe 同级 `config/` 生成 `.key` 与配置；填入 DeepSeek API Key 后功能方可联网。

## 分发注意

- exe 未做代码签名时，用户下载运行可能触发 SmartScreen 拦截，需说明「仍要运行」。工业级分发建议购买代码签名证书。
- 发布走 GitHub Release 资产，**不要把 exe / dist 提交进仓库**。
