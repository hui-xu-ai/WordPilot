# tests/test_image_toolbox_workspace.py
"""图片工具箱不再要求用户/安装包预置 Image.docx：缺失时自动用 Word 建一个空白工作文档。

现场（2026-10-27 打包后截图）：`找不到必要文件` 指向 dist 下的 `config/Image.docx`。
工作文档是**运行时产物**，既不该要求用户手工准备，也不该打进安装包（可能残留用户图片）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/plugins/image_toolbox/word_image_processor.py"


def test_no_file_not_found_error_for_missing_workspace():
    text = SOURCE.read_text(encoding="utf-8")

    assert "请在config文件夹下建立" not in text, "旧报错文案应已删除"
    assert "_ensure_workspace_file" in text, "必须有自动创建工作文档的逻辑"


def test_workspace_is_created_before_opening():
    text = SOURCE.read_text(encoding="utf-8")
    init = text[text.index("def __init__"):text.index("def _ensure_workspace_file")]
    order_create = init.index("self._ensure_workspace_file()")
    order_open_doc = init.index("self._open_workspace()")

    assert order_create < order_open_doc, "先创建工作文档，再打开它"


def test_workspace_uses_word_com_and_closes_cleanly():
    text = SOURCE.read_text(encoding="utf-8")
    helper = text[text.index("def _ensure_workspace_file"):text.index("def _open_word_app")]

    assert "Documents.Add()" in helper
    assert "SaveAs2(" in helper or "SaveAs(" in helper
    assert "Close(SaveChanges=0)" in helper, "创建后的临时文档必须不保存地关掉"


def test_workspace_path_stays_under_config_dir():
    text = SOURCE.read_text(encoding="utf-8")

    assert 'os.path.join(config_dir, "Image.docx")' in text, \
        "工作文档必须落在 config/ 下（已被 gitignore，且不随安装包分发）"


def test_no_repo_template_docx_required():
    """仓库里不该再依赖一个 Image.docx 模板文件（避免把用户图片带进版本库）。"""
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert re.search(r"src/config/Image\.docx", ignored), "Image.docx 必须保持 gitignore"
    assert not (ROOT / "src/config/templates/Image.docx").exists()
