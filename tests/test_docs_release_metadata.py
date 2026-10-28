# GitHub 说明（README）与版本管理同步检查：
# - README 必须写对当前版本号
# - CHANGELOG 首个版本条目必须与 __version__ 一致
# - 仓库文件齐全（CONTRIBUTING / SECURITY / .gitattributes / Issue 模板）
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _version() -> str:
    text = (ROOT / "src/__version__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    assert match, "__version__ 未定义"
    return match.group(1)


def test_readme_mentions_current_version():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert _version() in readme, f"README 未更新到当前版本 {_version()}"


def test_changelog_has_entry_for_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{_version()}]" in changelog


def test_repo_has_github_meta_files():
    for relative in (".gitattributes", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
                     ".github/pull_request_template.md",
                     ".github/ISSUE_TEMPLATE/bug_report.yml",
                     ".github/ISSUE_TEMPLATE/feature_request.yml"):
        assert (ROOT / relative).exists(), f"缺少 {relative}"


def test_gitattributes_enforces_lf_for_source():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto" in text
    assert "*.py" in text and "eol=lf" in text
