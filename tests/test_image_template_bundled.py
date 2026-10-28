# tests/test_image_template_bundled.py
"""图片工具箱的 `Image.docx` 必须**随包提供**，不能指望用户或 Word COM 现场生成。

用户现场（2026-10-28）：「Image.docx 这个文件你忘记打包时附带了」——
弹出了 `Required file not found: …config\\Image.docx`。
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_template_exists_and_is_valid_docx():
    template = ROOT / "src/assets/templates/Image.docx"
    assert template.exists(), "缺少打包模板 src/assets/templates/Image.docx"

    with zipfile.ZipFile(template) as archive:
        names = set(archive.namelist())
    assert "[Content_Types].xml" in names
    assert "word/document.xml" in names


def test_spec_bundles_template():
    spec = (ROOT / "WordPilot.spec").read_text(encoding="utf-8")
    assert "assets/templates" in spec, "WordPilot.spec 必须把模板打进包"
    assert "template_datas" in spec


def test_workspace_copied_from_template(tmp_path, monkeypatch):
    """模板存在时，`_ensure_workspace_file()` 应该直接复制，不需要 Word。"""
    from plugins.image_toolbox import word_image_processor as module

    target = tmp_path / "Image.docx"
    processor = object.__new__(module.WordImageProcessor)
    processor.config_dir = str(tmp_path)
    processor.temp_docx_path = str(target)

    copied = processor._copy_template()

    assert copied is True
    assert target.exists()
    assert target.stat().st_size == (ROOT / "src/assets/templates/Image.docx").stat().st_size


def test_copy_template_returns_false_when_missing(tmp_path, monkeypatch):
    from plugins.image_toolbox import word_image_processor as module

    processor = object.__new__(module.WordImageProcessor)
    processor.config_dir = str(tmp_path)
    processor.temp_docx_path = str(tmp_path / "Image.docx")

    monkeypatch.setattr(module.WordImageProcessor, "_template_path",
                        staticmethod(lambda: tmp_path / "nope.docx"), raising=False)
    assert processor._copy_template() is False
