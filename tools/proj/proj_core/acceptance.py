"""proj accept —— 独立验收报告的脚手架与机械校验（配合 acceptance-testing 技能）。"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .common import run_git

TEMPLATE = """# 独立验收报告 {run_id}

- 验收会话：{session}   commit：{commit}   分支：{branch}   dirty：{dirty}
- 环境：python {python} / {platform} / 沙箱策略 {sandbox}
- 规格来源：{specs}
- 用例来源：（按 test-design 从规格推导，勿照实现写）

## 用例矩阵

| # | 来源（规格条目） | 操作 | 期望 | 类型 | 实测 | 证据 |
|---|---|---|---|---|---|---|
| 1 |  |  |  | 正常 |  |  |
| 2 |  |  |  | 边界 |  |  |
| 3 |  |  |  | 错误 |  |  |

## 结论

- 判定：（逐项 PASS / FAIL / N-A，不得合并）
- 阻塞项（P0）：
- 非阻塞问题（P1）：
- 未覆盖 / 不可判定（写清原因）：
"""


@dataclass
class AcceptReport:
    run_id: str
    path: str
    commit: str = ""
    created: bool = False
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _env_facts() -> dict[str, str]:
    parts = []
    for key, label in (("DSH_SANDBOX_MODE", "sandbox"), ("DSH_PERMISSION_MODE", "approval")):
        if os.environ.get(key):
            parts.append(f"{label}={os.environ[key]}")
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "sandbox": " ".join(parts) or "未注入（手填）",
        "session": os.environ.get("DSH_SESSION_ID") or "未注入（手填）",
    }


def _head(root: Path) -> tuple[str, str, str]:
    commit = (run_git(root, "rev-parse", "--short", "HEAD") or "").strip()
    branch = (run_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    dirty = "是" if (run_git(root, "status", "--porcelain") or "").strip() else "否"
    return commit, branch, dirty


def init_report(root: Path, run_id: str | None = None, force: bool = False) -> AcceptReport:
    root = root.resolve()
    run_id = run_id or datetime.now(UTC).strftime("acc-%Y%m%d-%H%M%S")
    out_dir = root / "work" / "verify" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ACCEPTANCE.md"
    commit, branch, dirty = _head(root)
    facts = _env_facts()
    specs = [p for p in ("docs/adr", "docs/standards/INDEX.md", "work/tasks") if (root / p).exists()]
    report = AcceptReport(run_id=run_id, path=str(path.relative_to(root)).replace("\\", "/"), commit=commit)
    if path.exists() and not force:
        report.problems.append(f"已存在 {report.path}（要覆盖加 --force）")
        return report
    path.write_text(
        TEMPLATE.format(
            run_id=run_id, session=facts["session"], commit=commit, branch=branch, dirty=dirty,
            python=facts["python"], platform=facts["platform"], sandbox=facts["sandbox"],
            specs=", ".join(specs) or "（未找到，手填）",
        ),
        encoding="utf-8",
    )
    report.created = True
    return report


def check_report(root: Path, run_id: str) -> AcceptReport:
    root = root.resolve()
    path = root / "work" / "verify" / run_id / "ACCEPTANCE.md"
    commit, _, _ = _head(root)
    report = AcceptReport(run_id=run_id, path=str(path.relative_to(root)).replace("\\", "/"), commit=commit)
    if not path.is_file():
        report.problems.append(f"报告不存在：{report.path}")
        return report
    text = path.read_text(encoding="utf-8")
    m = re.search(r"commit：\s*([0-9a-f]{4,40})", text)
    if not m:
        report.problems.append("缺 commit 记录")
    elif commit and not commit.startswith(m.group(1)) and not m.group(1).startswith(commit):
        report.problems.append(f"报告 commit={m.group(1)} 与当前 HEAD={commit} 不一致（证据作废）")
    if "环境：" not in text:
        report.problems.append("缺环境事实（python/沙箱策略）")
    rows = [ln for ln in text.splitlines() if ln.strip().startswith("|") and "---" not in ln]
    data_rows = [ln for ln in rows[1:] if ln.count("|") >= 6 and ln.split("|")[1].strip()]
    if not data_rows:
        report.problems.append("用例矩阵为空（至少 1 条：正常/边界/错误）")
    if "证据" not in (rows[0] if rows else ""):
        report.problems.append("用例矩阵缺「证据」列")
    verdict = re.search(r"判定：\s*(.+)", text)
    verdict_text = verdict.group(1).strip() if verdict else ""
    placeholder = (not verdict_text) or verdict_text.startswith("（") or "逐项" in verdict_text
    if placeholder or not re.search(r"PASS|FAIL|N-?A", verdict_text):
        report.problems.append("结论段缺逐项判定（PASS / FAIL / N-A）；空白模板的「（逐项 …）」不算填写")
    if re.search(r"判定：\s*PASS\s*$", text, re.MULTILINE) and "N-A" not in text and "FAIL" not in text:
        report.problems.append("只写了一个总 PASS：独立验收要求逐项判定，不得合并")
    for ln in data_rows:
        cells = [c.strip() for c in ln.split("|")]
        if len(cells) >= 8 and (cells[6] == "" or cells[7] == ""):
            report.problems.append("有用例行缺「实测」或「证据」指针（命令/输出文件）")
            break
    return report


def print_accept(report: AcceptReport) -> None:
    action = "已创建" if report.created else "检查"
    print(f"=== proj accept（{action}）：{report.path}")
    if report.commit:
        print(f"  commit：{report.commit}")
    if report.problems:
        for p in report.problems:
            print(f"  [FAIL] {p}")
    elif report.created:
        print("  [OK] 模板已生成（待填写）；填完用 accept check 校验")
    else:
        print("  [OK] 报告结构完整、commit 一致")


def write_json(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
