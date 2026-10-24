# tests/test_offline_scan.py
"""离线题录扫描（不走 COM 地解析 docx）：章节识别与题录归属。

现场问题：刷新题录走 Word COM，40MB 论文样本实测 ~1.3s（仅遍历字段），GUI 卡顿约 2s。
离线解析同一样本 ~70ms。这里用**现场构造的最小 docx**钉住识别规则：
- 章节按"识别到的最小标题层级"判定（模板常用 heading 2 当章标题）；
- 题注以文案编号（图/表 + 编号）为准，样式只作 SEQ 域的辅助线索；
- 每条题录带所属章节下标，供界面按章节过滤。
"""
from __future__ import annotations

import zipfile

import pytest

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _p(text: str, style_id: str = "") -> str:
    style = f'<w:pPr><w:pStyle w:val="{style_id}"/></w:pPr>' if style_id else ""
    return f"<w:p>{style}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _make_docx(path, paragraphs: list[str]) -> str:
    document = (f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<w:document xmlns:w="{W}"><w:body>' + "".join(paragraphs) + "</w:body></w:document>")
    styles = (f'<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{W}">'
              '<w:style w:styleId="2"><w:name w:val="heading 2"/></w:style>'
              '<w:style w:styleId="ae"><w:name w:val="图表标题"/></w:style>'
              "</w:styles>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
    return str(path)


@pytest.fixture
def sample_docx(tmp_path):
    paragraphs = [
        _p("章标题", "2"),                     # 占位式章标题（真实样本里就这样）
        _p("图 1.1 测试装置示意图", "ae"),
        _p("1.1)", "ae"),                      # 图注续行：样式相同但没有编号 ⇒ 不算题录
        _p("表 1.1 实验材料", "ae"),
        _p("第2章  方法", "2"),
        _p("图 2.1 驱动器结构", "ae"),
        _p("正文段落，不是题录"),
    ]
    return _make_docx(tmp_path / "sample.docx", paragraphs)


def test_chapters_and_items(sample_docx):
    from plugins.navigator.offline_scan import scan_docx

    result = scan_docx(sample_docx)

    assert result.chapters == ["章标题", "第2章  方法"], result.chapters
    assert result.chapter_level == 2, "模板用 heading 2 当章标题"
    assert len(result.table_items()) == 1
    assert len(result.figure_items()) == 2, "图注续行（1.1)）不应被当成题录"


def test_items_carry_chapter_index(sample_docx):
    from plugins.navigator.offline_scan import scan_docx

    result = scan_docx(sample_docx)
    by_text = {item.text: item.chapter for item in result.items}

    assert by_text["图 1.1 测试装置示意图"] == 0
    assert by_text["表 1.1 实验材料"] == 0
    assert by_text["图 2.1 驱动器结构"] == 1, "第二章之后的题录要归到第二章"


def test_kind_detection_by_text(tmp_path):
    from plugins.navigator.offline_scan import scan_docx

    path = _make_docx(tmp_path / "kinds.docx", [
        _p("图 3.2 曲线", "ae"),
        _p("表 3.1 参数", "ae"),
        _p("Figure 3.3 Curve", "ae"),
        _p("Table 3.2 Params", "ae"),
    ])
    result = scan_docx(path)
    assert len(result.figure_items()) == 2
    assert len(result.table_items()) == 2


def test_missing_styles_xml_still_works(tmp_path):
    """没有 styles.xml 时不该崩（章节识别不出来，题录照常按文案抓）。"""
    from plugins.navigator.offline_scan import scan_docx

    path = tmp_path / "no_styles.docx"
    document = (f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<w:document xmlns:w="{W}"><w:body>' + _p("图 1.1 示意图") + "</w:body></w:document>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)

    result = scan_docx(str(path))
    assert len(result.items) == 1
    assert result.chapters == []
