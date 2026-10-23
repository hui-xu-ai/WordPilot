# SmartProof 工作区（pydev 预设）

> 自动注入的会话基线；只放事实 / 启动清单 / 红线，方法论在技能里按需加载。

## 会话开始

1. 读 `.dsh-memory/HANDOFF.md`（≤80 行，唯一必读）；
2. `skill(name="pydev-protocol")`；
3. 按需读 `docs/ARCHITECTURE.md` / `.dsh-memory/project/CODE_MAP.md`；
4. 若 `tools\memory-gate.ps1` 存在，跑一次。

## 事实

- **平台**：Windows + 本机 Microsoft Word（pywin32 COM）；**PySide6 6.11.1**；Python **3.14.5**。
- **入口**：`src/main.py`；`src/` 是 import 根（`from core...` / `from app...` 可直接用）。
- **打包**：PyInstaller + `SmartProof.spec`，产物 `dist/SmartProof`（`dist/`、`build/` 已忽略）。
- **测试**：离线单测在 `tests/`（pytest，`pythonpath=src`，无需桌面）；GUI 交互仍用 `tools/smoke_*.py` / `tools/diag_*.py` 冒烟（需桌面会话）。
- **隐私**：`src/config/` 下 `.key`、`MWindow_config.json`、`api_calls.log`、`*.db`、`Image.docx` 严禁入库；`local/`、`.dsh-memory/`、`work/` 已忽略。
- **历史**：workbuddy 记忆已归档 `docs/archive/workbuddy-memory/`。

## 红线

- 不改 `src/config/` 真实配置；api_key 不进代码、日志、提交。
- 杀进程只按 PID；**不按进程名**批量杀 Word / Python。
- 提交显式列路径；**禁止 `git add -A`**。
- 声称"完成"前必须声明验证档位（单测 / 真实链路 / 用户验收）。

## 常用命令

```powershell
.venv\Scripts\python.exe src\main.py                    # 运行
.venv\Scripts\python.exe -m pytest -q                   # 离线单测（tests/，无需桌面）
.venv\Scripts\python.exe tools\smoke_p8.py              # GUI 冒烟（需桌面）
.venv\Scripts\python.exe -m PyInstaller --noconfirm SmartProof.spec   # 打包
python tools\proj\proj.py arch check .                  # 模块架构违规（模型 .dsh/architecture.toml）
python tools\proj\proj.py panel . --port 8787           # 架构面板（浏览器开 127.0.0.1:8787）
```

## 预设维护

本预设（pydev）来自 `D:\Python\DeepSeek\CodeAgent\pydev-share\preset\pydev`；改预设不在本项目做。
