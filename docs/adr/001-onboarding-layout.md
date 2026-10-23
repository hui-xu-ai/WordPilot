# ADR-001 采用 pydev 项目布局与记忆分离

状态: accepted
日期: 2026-10-23

## 背景
项目此前由 workbuddy 开发，测试文档、截图与临时产物堆在 `work/`；无 ADR / 标准索引 / 项目记忆，
跨会话恢复依赖 `docs/` 与旧的 `.workbuddy/memory/`。迁入 DeepSeek Harness + pydev 预设后，
需要一套可机械校验的布局，且不能把 145 MB 的论文样本与截图放进 git。

## 备选
### 备选 A：维持现状（work/ 混放一切）
- 优点：零改动。
- 被否理由：`work/` 被 gitignore，测试样本不可复现；无记忆交接，换会话成本高；无法用 `proj scan/eval` 校验。

### 备选 B：采用 pydev 布局（选中）
- 优点：小样本进 `docs/test-fixtures/`（可复现）、大样本进 `local/`（私有）、截图进 `local/screenshots/`、
  记忆进 `.dsh-memory/`（独立 git）、文档进 `docs/`（可索引）；工具可机械检查。
- 被否理由：一次性迁移成本。

## 决策
采用备选 B：
- `docs/test-fixtures/`：`make_fixture.py`、`fixture_thesis.docx`、`scan_result.json`、`scan_grouped.txt`（**入库**）；
- `local/fixtures/`：两份真实论文样本（**不入库**，`local/` 已忽略）；
- `local/screenshots/`：GUI 预览图（**不入库**）；
- `.dsh-memory/`：交接卡 / 索引 / 流水 / 项目档案（**独立 git**，项目仓库忽略）；
- `docs/adr/`、`docs/standards/INDEX.md`、`docs/wiki/`：决策、标准与文档索引；
- `.workbuddy/memory/` → `docs/archive/workbuddy-memory/`（保留历史，workbuddy 目录删除）。

## 后果
- 优点：测试样本可复现、隐私边界清晰、跨会话可交接、`proj eval` 可量化。
- 代价：迁移一次；`.dsh-memory` 需单独提交；大样本首次使用需从 `local/` 取。
- 风险：`local/` 若被误加进 git，145 MB 会进历史 ⇒ `.gitignore` 已加固，`proj scan` 会检查私有路径是否被跟踪。
