# SmartProof · Word 智能伴侣

面向中文毕业论文 / 科技论文写作场景的 Microsoft Word 桌面辅助工具。基于 **PySide6 + pywin32(COM)**，通过 **DeepSeek API** 提供实时校对能力，并内置题录导航、图片无损裁剪等论文写作实用工具。

> 作者：Xu Hui　当前版本：v3.0　平台：Windows（依赖本机已安装的 Microsoft Word）

## 功能特性

| 功能 | 说明 | 入口 |
| --- | --- | --- |
| 实时 AI 纠错 | 监控光标所在句子，句末完成后自动送 AI 校对，红色波浪线标出差异 | 后台监控线程 / `Ctrl+Shift+Z` |
| 翻译 | 选中文本或剪贴板内容翻译 | `Ctrl+Shift+T` |
| 改写 | 按风格改写文本 | `Ctrl+Shift+R` |
| 题录导航 | 扫描 Word 域(SEQ)，生成图/表题注列表，双击跳转、支持返回上一位置 | 工具菜单 / 托盘 |
| 图片无损裁剪 | 借助隐藏 Word 工作文档对图片裁剪居中，保留原始像素 | 工具菜单「图片工具箱」 |
| 校对开关 | `Ctrl+Shift+E` 启停实时监控 | 热键 / 主界面 |

## 快速开始

```bash
# 1. 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows

# 2. 安装依赖
pip install -r requirements.txt

# 3. 准备配置（首次运行也会自动生成，但建议用模板）
copy src\config\MWindow_config.example.json src\config\MWindow_config.json
#   然后编辑 MWindow_config.json，填入你自己的 DeepSeek api_key

# 4. 运行
python src\main.py
```

打包为 exe 见 [`docs/BUILD.md`](docs/BUILD.md)。

## 隐私与数据安全（重要）

本工具会在 `src/config/` 下生成**含敏感信息的运行时数据**，这些文件**已通过 `.gitignore` 排除，不会推送到仓库**：

- `.key` —— Fernet 加密主密钥
- `MWindow_config.json` —— 含 **API Key** 与个人偏好
- `api_calls.log` —— 含你论文的文本片段与 token 统计
- `ignore_db.json`、`word_caption_cache.db` —— 用户数据
- `Image.docx` —— 图片裁剪工作文档（可能残留你的图片）

仓库中只提供 `*.example.json` 模板。**请勿把真实配置提交到 Git，也请勿把 API Key 硬编码进源码。**

## 项目结构

```
SmartProof/
├── src/                     # 全部源码
│   ├── main.py              # 程序入口
│   ├── main_window.py       # 主窗口 / 菜单 / 托盘 / 各工具装配
│   ├── word_monitor.py      # 后台监控线程 + 分词 + 差异标记（核心）
│   ├── api_client.py        # DeepSeek Chat Completions 封装
│   ├── api_logger.py        # 调用日志 + 基于日志的校对缓存
│   ├── word_navigator_dialog.py  # 题录导航（域扫描 + 缓存）
│   ├── caption_cache.py     # 题注 SQLite 缓存（指纹=路径+大小+mtime）
│   ├── word_image_processor*.py  # 图片无损裁剪
│   ├── image_reshaper_widget.py  # 图片工具箱 UI
│   ├── suggestion_window.py # 悬浮按钮 / 建议卡片
│   ├── settings_dialog.py   # 设置对话框
│   ├── config_manager.py    # 配置读写 + Fernet 加密
│   ├── hotkey_manager.py    # 全局热键
│   ├── ignore_cache.py      # 忽略句子库
│   └── config/              # 运行时数据目录（隐私文件已 gitignore）
├── docs/                    # 架构 / 开发规范 / 打包文档
├── requirements.txt
├── SmartProof.spec          # PyInstaller 打包配置
└── .gitignore
```

更详细的模块职责与数据流见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)；开发、提交与发布规范见 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)。

## 许可

待定（推送 GitHub 前请补充 LICENSE）。
