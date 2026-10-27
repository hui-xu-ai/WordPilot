# tests/test_i18n_exception_messages.py
"""异常/状态文案也要走语言包（它们会被界面直接显示）。

现场（2026-10-27）：英文界面里导航页底部显示
`Scan failed: 未检测到正在运行的 Word，请先打开 Word 文档` —— 异常信息是前一句硬编码中文。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")

#: 会显示给用户的异常类型
EXC_CTORS = {"Exception", "RuntimeError", "ValueError", "TypeError", "OSError", "LookupError",
             "KeyError", "FileNotFoundError", "WordNotRunningError", "PermissionError"}
KEYS = ("word.error.not_running", "word.error.not_running_doc", "image.error.blank",
        "image.error.no_image", "image.error.no_selection",
        "library.error.empty_name", "library.error.keep_one")


def _raise_strings() -> list[str]:
    found: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
            if name not in EXC_CTORS:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and CJK.search(arg.value):
                    found.append(f"{path.name}:{node.lineno} {arg.value[:40]}")
    return found


def test_no_hardcoded_chinese_exception_messages():
    offenders = _raise_strings()

    assert not offenders, "异常文案会显示给用户，必须走 i18n.message()：\n" + "\n".join(offenders)


def test_message_helper_falls_back_and_translates():
    from core.services import i18n

    i18n.register_fallback({"demo.key": "中文兜底"})
    i18n.set_message_service(None)

    assert i18n.message("demo.key") == "中文兜底"
    assert i18n.message("missing.key") == "missing.key"


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_exception_keys_exist(locale):
    data = json.loads((ROOT / f"src/assets/i18n/{locale}.json").read_text(encoding="utf-8"))

    for key in KEYS:
        assert key in data, f"{locale} 缺 {key}"
    if locale == "en_US":
        for key in KEYS:
            assert not CJK.search(data[key]), f"{key} 英文包里仍是中文"


def test_kernel_injects_message_service():
    source = (ROOT / "src/core/kernel.py").read_text(encoding="utf-8")

    assert "set_message_service(i18n_service)" in source
