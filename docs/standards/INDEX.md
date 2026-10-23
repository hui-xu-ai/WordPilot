# SmartProof 标准与门索引

> 生命周期门（G0–G6）与 `STD-*` 的**全局定义**在 pydev 预设 `docs/standards/INDEX.md`；
> 本文件只列**本项目**特有的规则与机械检查，不重复全局正文。

## 一、项目红线（STD-SP）

| 编号 | 规则 | 机械检查 |
|---|---|---|
| STD-SP-01 | `src/config/` 下 `.key`、`MWindow_config.json`、`api_calls.log`、`*.db`、`Image.docx` 严禁入库 | `proj scan`（隐私）/ `quality-gate` G1 |
| STD-SP-02 | 测试样本分层：小样本 `docs/test-fixtures/`（入库）、大样本 `local/fixtures/`（不入库）、截图 `local/screenshots/` | `.gitignore` + `proj scan` |
| STD-SP-03 | GUI/交互改动必须跑对应 `tools/smoke_*.py`（桌面会话），并保留截图/输出 | 冒烟输出落 `work/verify/<run>/` |
| STD-SP-04 | 插件不得 import `app` 层；共享控件放 `core/widgets/` | 评审（`code-review`）+ **`proj arch check`（模块边界）** |
| STD-SP-05 | 提交前双闸门：`quality-gate` + `proj scan`（后者覆盖未跟踪文件） | 工具 |
| STD-SP-06 | 打包相关改动必须跑 `tools/smoke_p7_frozen.py` | 工具输出 |
| STD-SP-07 | `src/` 是 import 根；新增包必须能从 `src/` 解析到 | `proj graph`（无未解析） |
| STD-SP-08 | 模块边界以 `.dsh/architecture.toml` 为准；跨模块改动先改模型并说明 | `proj arch check`（违规 exit 1） |

## 二、门产物约定（本项目）

| 门 | 产物位置 |
|---|---|
| G0 需求 | `work/tasks/<id>/intake.md` |
| G1 设计 | `docs/adr/NNN-*.md` |
| G2 计划 | `work/tasks/<id>/plan.md`（每步 `verify:`） |
| G3 实现 | 测试 + 实现提交 |
| G4 验证 | `work/verify/<run-id>/RESULT.md`（开发自证）/ `ACCEPTANCE.md`（独立验收） |
| G5 评审 | 评审记录（严重度分级） |
| G6 发布 | `docs/BUILD.md` 流程 + CHANGELOG（待建立） |
