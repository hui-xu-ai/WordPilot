#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构造含 多级标题 / SEQ 题注 / Zotero / EndNote 域 / 分页标记 的最小 .docx，用于自测离线扫描器。"""
import zipfile
from xml.sax.saxutils import escape

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def run_text(t):
    return f'<w:r><w:t xml:space="preserve">{escape(t)}</w:t></w:r>'


def run_break():
    return '<w:r><w:lastRenderedPageBreak/></w:r>'


def field(instr, result):
    return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve">{escape(instr)}</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            f'{run_text(result)}'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>')


def para(inner):
    return f'<w:p>{inner}</w:p>'


def heading(text, level):
    return (f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>'
            f'{run_text(text)}</w:p>')


zotero_instr = (' ADDIN ZOTERO_ITEM CSL_CITATION '
                '{"citationItems":[{"id":"ABC123XY","uris":["http://zotero.org/users/9/items/ABC123XY"],'
                '"itemData":{"title":"深度学习研究综述"}}],"properties":{}}')
endnote_instr = (' ADDIN EN.CITE <EndNote><Cite><record><rec-number>2</rec-number>'
                 '<titles><title>神经网络方法研究</title></titles></record></Cite></EndNote>')
bib_result = ('[1] 张三. 深度学习研究综述[J]. 计算机学报, 2020.\n'
              '[2] 李四. 神经网络方法研究[J]. 软件学报, 2021.')

body = []
body.append(heading('第一章 绪论', 1))
body.append(heading('1.1 研究背景', 2))
body.append(para(run_text('图') + field(' SEQ 图 \\* ARABIC ', '1') + run_text(' 系统总体架构')))
body.append(para(run_text('图') + field(' SEQ 图 \\* ARABIC ', '2') + run_text(' 数据流示意')))
body.append(para(run_break()))
body.append(heading('1.2 研究意义', 2))
body.append(para(run_text('表') + field(' SEQ 表 \\* ARABIC ', '1') + run_text(' 实验参数设置')))
body.append(para(run_text('已有研究表明') + field(zotero_instr, '[1]') + run_text('该方法有效。')))
body.append(heading('第二章 相关技术', 1))
body.append(para(run_break() + run_text('图') + field(' SEQ 图 \\* ARABIC ', '3') + run_text(' 网络拓扑')))
body.append(para(run_text('另有文献') + field(endnote_instr, '[2]') + run_text('指出该问题。')))
body.append(para(run_break() + field(' ADDIN ZOTERO_BIBL \\* MERGEFORMAT ', bib_result)))

document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document {W}><w:body>' + ''.join(body) +
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')

content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                 '</Types>')
rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>')

out = 'work/fixture_thesis.docx'
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('[Content_Types].xml', content_types)
    z.writestr('_rels/.rels', rels)
    z.writestr('word/document.xml', document)
print(f'已生成 {out}')
