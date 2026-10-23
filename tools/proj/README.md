# proj-core

项目架构与交付卫生的最小工具集（**纯 stdlib**，不依赖 DSH，可跨项目/跨机器复制）。

## 安装（分发）

实现全部在 `proj_core/` 包里；`proj.py` / `panel.py` / `mcp_server.py` 是**兼容 shim**（老命令继续可用）。

```powershell
# 方式 1：直接装源码目录（无第三方依赖）
python -m pip install --no-deps <本目录>

# 方式 2：先打 wheel 再装（本机没装 setuptools 也能用：自带纯 stdlib 打包器）
python tools\proj\build_wheel.py            # → tools/proj/dist/pydev_proj_core-<ver>-py3-none-any.whl
python -m pip install --no-deps tools\proj\dist\pydev_proj_core-0.4.0-py3-none-any.whl
```

安装后得到：控制台命令 **`proj`**、模块入口 `python -m proj_core.cli`、以及 MCP 入口 `python -m proj_core.mcp_server`。
`pyproject.toml` 里 `dependencies = []`（零依赖）；`build_wheel.py` 只依赖标准库 `zipfile`/`hashlib`。

## 用法

```powershell
python tools\proj\proj.py scan <root> [--json out.json]
python tools\proj\proj.py init <root> [--apply]
python tools\proj\proj.py push-check <root> [--mode staged|last|range] [--range A..B] [--message MSG] [--json out.json]
python tools\proj\proj.py wiki index <root> [--out docs/wiki]
python tools\proj\proj.py wiki search <root> <query> [--limit N] [--json out.json]
python tools\proj\proj.py context <root> [--task work/tasks/<id>] [--max-tokens 2500] [--json out.json]
python tools\proj\proj.py profile <root> [--write] [--json out.json]
python tools\proj\proj.py dup <root> [--symbols] [--min-lines 6] [--json out.json]
python tools\proj\proj.py impact <root> <path|symbol> [--json out.json]
python tools\proj\proj.py graph <root> [--focus <path>] [--depth 1] [--fail-on-cycle] [--json out.json]
python tools\proj\proj.py adr index <root> [--dir docs/adr] [--out docs/adr/INDEX.md] [--json out.json]
python tools\proj\proj.py eval <root> [--preset <预设目录>] [--json out.json]
python tools\proj\proj.py panel <root> [--host 127.0.0.1] [--port 8787]
python tools\proj\mcp_server.py [--list-tools]
```

退出码：`0` 无硬问题；`1` 有隐私/越界硬问题（scan、push-check）或搜索无命中（wiki search）；`2` 用法错误。

## 各命令

| 命令 | 做什么 | 写盘 |
|---|---|---|
| `scan` | 布局、文件规模（>400/800 行）、垃圾目录/文件、疑似密钥、`.env`、被跟踪的私有路径、`.gitignore` 缺项、评分 | 仅 `--json` |
| `init` | 创建 `repo/ local/ docs/{architecture,adr,wiki} work/tasks .dsh` + 补 `.gitignore` | 仅 `--apply` |
| `push-check` | 变更集的私有路径/垃圾/`.env`/大文件/疑似密钥 + 提交信息规范 + 未推送提交数 | 仅 `--json` |
| `wiki index` | 索引根 `*.md` + `docs/**/*.md`（+ `repo/docs`），写 `docs/wiki/index.json` 与 `INDEX.md` | 是 |
| `wiki search` | 在索引上按标题/小标题/路径打分检索 | 否 |
| `context` | 有界上下文包：任务卡 → HANDOFF → 项目档案 → Wiki 索引 → 架构（按优先级裁剪到 `--max-tokens`） | 仅 `--json` |
| `profile` | 探测 stack/commands/conventions/gates + `evidence`（commit + configHash） | 仅 `--write` |
| `dup` | 重复代码块（≥N 个有效行）与跨文件同名符号（过滤常见短名） | 仅 `--json` |
| `impact` | 给定文件或符号的引用面（grep 级，按文件计数 + 样例行） | 仅 `--json` |
| `graph` | 模块依赖图（Python `ast` + JS/TS 正则）：入度 Top、**循环检测**、`--focus` 上下游、未解析引用 | 仅 `--json` |
| `adr index` | ADR 索引与校验：编号唯一连续 + 状态/日期/背景/决策/后果/备选齐全，产出 `docs/adr/INDEX.md` | 写 `INDEX.md` |
| `eval` | 一键收口自检：布局/隐私/体积/垃圾/忽略/依赖循环/Wiki 新鲜/测试/技能预算/领域包 | 仅 `--json` |
| `panel` | 本地只读架构面板（`http://127.0.0.1:8787/`）：**模块级架构图**（声明模型 vs 实际依赖、违规红虚线）＋ **约束提示词生成**（允许改/不得改/必须回归） | 只读 |
| `arch init` / `arch check` | 模块级架构监管：`init` 生成 `.dsh/architecture.toml`（模块/路径/允许依赖），`check` 对比实际 import 报**违规**（exit 1） | `init` 写模型；`check` 只读 |
| `mcp_server.py` | 官方 `dsh-mcp-client`（stdio）暴露 7 个工具：`proj_scan`/`proj_push_check`/`proj_wiki_search`/`proj_context`/`proj_graph`/`proj_adr_index`/`proj_pack_list` | 只读（push_check 只读检查） |

## 约定

- **私有/运行区**（`local/`、`.dsh-memory/`、`work/`、`outputs/`）的内容不参与源码扫描，但会被检查"是否被 git 跟踪"。
- **假密钥/示例**：在**同一行**写 `allowlist secret`；检测器自身的模式定义写 `gate: pattern-source`（与 `tools/quality-gate.ps1` 同一约定）。
- `push-check` 默认看**暂存区**；`--mode last` 看最后一个提交；`--mode range --range A..B` 看区间；`--mode push` 看本次要推的提交（pre-push 钩子用）。
- `context` 默认预算 2500 tokens（≈10000 字符），超预算从低优先级整段丢弃。

## MCP 接入（官方 `dsh-mcp-client`，stdio）

在 **profile 补丁**里用 `insert` 形式加一行（顶层 `- id:` 只能覆盖既有入口，新行必须包 `insert`）：

```yaml
- insert:
    - id: mcp-pydev-proj
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        transport: stdio
        serverName: pydev-proj
        command: python
        args: [ "D:\\Python\\DeepSeek\\CodeAgent\\tools\\proj\\mcp_server.py" ]
```

工具公开名形如 `mcp__pydev-proj__proj_scan`。改 profile 后**必须重启 DSH 进程**才生效。
自检：`python tools\proj\mcp_server.py --list-tools`。

## 测试

```powershell
python -m pytest tests\test_proj.py -q
```
