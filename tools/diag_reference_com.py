# tools/diag_reference_com.py
"""诊断：把 Word 当前选区"看到的东西"打印出来，用于排查参考文献悬浮窗为什么不弹。

用法（Word 打开文档、把光标点到引用上，然后运行）：

    .venv\\Scripts\\python.exe tools\\diag_reference_com.py

输出会包含：文档名、选区范围、选区文本、段落样式、所在域的数量/指令/结果文本，
以及 `resolve_selection()` 的判定结果。把整段输出贴回来即可定位。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _safe(label: str, getter):
    try:
        print(f"  {label}: {getter()!r}")
    except Exception as exc:                    # noqa: BLE001 - 诊断脚本要尽量多打印
        print(f"  {label}: <失败 {type(exc).__name__}: {exc}>")


def main() -> int:
    try:
        import pythoncom
    except ImportError:
        print("缺少 pywin32：先 pip install pywin32")
        return 2

    pythoncom.CoInitialize()
    try:
        from core.services.word_bridge import get_word_app
        from plugins.reference.word_watch import resolve_selection

        try:
            word = get_word_app()
        except Exception as exc:                # noqa: BLE001
            print(f"连不上 Word：{exc}")
            return 3

        try:
            selection = word.Selection
        except Exception as exc:                # noqa: BLE001
            print(f"取不到 Selection（Word 没有活动文档？）：{exc}")
            return 4

        print("=== 选区 ===")
        _safe("文档名", lambda: word.ActiveDocument.Name)
        _safe("Start/End", lambda: (int(selection.Start), int(selection.End)))
        _safe("Text", lambda: (selection.Text or "")[:120])
        _safe("段落样式", lambda: selection.Paragraphs(1).Style.NameLocal)

        print("=== 域 ===")
        for name, getter in (
            ("Selection.Fields.Count", lambda: selection.Fields.Count),
            ("Selection.Range.Fields.Count", lambda: selection.Range.Fields.Count),
        ):
            _safe(name, getter)
        try:
            if selection.Fields.Count:
                field = selection.Fields(1)
                print(f"  域指令: {(field.Code.Text or '').strip()[:160]!r}")
                print(f"  域结果: {(field.Result.Text or '').strip()[:120]!r}")
        except Exception as exc:                # noqa: BLE001
            print(f"  读取域失败：{exc}")

        print("=== 解析结果 ===")
        payload = resolve_selection(word)
        if payload is None:
            print("  None（按当前规则不弹窗：不是文献类域、也不是脚注/尾注，或在题注段落里）")
        else:
            print(f"  文档: {payload['doc_name']}")
            print(f"  屏幕点: {payload['point']}")
            for item in payload["items"]:
                ref = item.get("reference")
                print(f"  - 锚点: {item['anchor_key']} | 编号: {item.get('label')!r} | "
                      f"元数据: {'有' if ref is not None else '无'}")
    finally:
        pythoncom.CoUninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
