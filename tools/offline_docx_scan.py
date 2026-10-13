#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
offline_docx_scan.py — 离线 .docx 扫描原型（零 COM，不干扰前台 Word）

用途：验证"直接解压 docx 读 word/document.xml"这条路线能否离线、快速地抽出：
  1) 图/表题注（SEQ 域），含用 lastRenderedPageBreak 估算的页码
  2) 题注按章节/标题分组（毕业论文图多时便于定位）
  3) Zotero / EndNote 引用域，含文献身份（Zotero itemKey / EndNote rec-number）与标题
  4) 参考文献表（ZOTERO_BIBL / EN.REFLIST）

设计原则：
  - 只读磁盘文件，不打开 Word，不调用任何 COM。
  - 只用标准库（zipfile / xml.etree / json / re）。
  - 这是"发现真实域结构"的探针：--raw 会打印引用域原始指令，
    方便针对用户真实论文里 Zotero/EndNote 的确切格式做适配。

用法：
  python tools/offline_docx_scan.py <文件.docx> [--json] [--raw] [--flat]
    --json  结构化输出
    --raw   额外打印引用域原始指令片段
    --flat  平铺列出题注（不分组）
"""
import argparse
import json
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# 中文标题文本兜底识别（当段落没有样式/大纲级别时）
RE_CHAPTER = re.compile(r"^\s*第\s*[一二三四五六七八九十百零\d]+\s*[章篇部]")
RE_SECTION = re.compile(r"^\s*第\s*[一二三四五六七八九十百零\d]+\s*节")
RE_NUM_HEAD = re.compile(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?\s+\S")


def qn(tag):
    prefix, local = tag.split(":")
    ns = {"w": W}[prefix]
    return f"{{{ns}}}{local}"


def read_document_xml(docx_path):
    with zipfile.ZipFile(docx_path) as z:
        return z.read("word/document.xml")


def extract_balanced_json(text, start):
    """从 text[start] 处的 '{' 开始，按大括号配平截取一段 JSON 字符串。"""
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def parse_paragraph(p, page):
    """
    遍历一个 <w:p> 的所有后代（文档顺序），维护域状态机。
    返回 (para_text, fields, page_after)
    """
    parts = []
    fields = []
    cur_instr = []
    cur_result = []
    state = "text"      # text | instr | result
    depth = 0

    for el in p.iter():
        tag = el.tag
        if tag == qn("w:lastRenderedPageBreak"):
            page += 1
        elif tag == qn("w:fldChar"):
            t = el.get(qn("w:fldCharType"))
            if t == "begin":
                depth += 1
                if depth == 1:
                    state, cur_instr, cur_result = "instr", [], []
            elif t == "separate":
                if depth == 1:
                    state = "result"
            elif t == "end":
                if depth == 1:
                    fields.append(("".join(cur_instr), "".join(cur_result)))
                    state = "text"
                depth = max(0, depth - 1)
        elif tag == qn("w:instrText"):
            if state == "instr":
                cur_instr.append(el.text or "")
        elif tag == qn("w:t"):
            parts.append(el.text or "")
            if state == "result":
                cur_result.append(el.text or "")

    return "".join(parts), fields, page


def para_style_and_outline(p):
    """返回 (pStyle_val, outline_level or None)。"""
    pPr = p.find(qn("w:pPr"))
    style_val = ""
    outline = None
    if pPr is not None:
        ps = pPr.find(qn("w:pStyle"))
        if ps is not None:
            style_val = ps.get(qn("w:val")) or ""
        ol = pPr.find(qn("w:outlineLvl"))
        if ol is not None:
            v = ol.get(qn("w:val"))
            if v is not None and v.isdigit():
                outline = int(v) + 1     # outlineLvl 0 => 一级标题
    return style_val, outline


def heading_level(p, text):
    """
    判断段落是否为标题，返回级别（1=章，2=节，3=小节…）或 None。
    优先级：大纲级别 > 标题样式 > 中文标题文本兜底。
    """
    style_val, outline = para_style_and_outline(p)
    if outline is not None and 1 <= outline <= 9:
        return outline
    if style_val:
        m = re.match(r"(?i)^heading\s*(\d)$", style_val)
        if m:
            return int(m.group(1))
        m = re.match(r"^标题\s*(\d)$", style_val)
        if m:
            return int(m.group(1))
        if style_val.lower() in ("title",):
            return 1
    t = text.strip()
    if not t or len(t) > 60:      # 标题一般不长，避免误判正文
        return None
    if RE_CHAPTER.match(t):
        return 1
    if RE_SECTION.match(t):
        return 2
    m = RE_NUM_HEAD.match(t)
    if m:
        # 1 => 一级；1.1 => 二级；1.1.1 => 三级
        if m.group(3):
            return 3
        if m.group(2):
            return 2
        return 1
    return None


def classify_field(instr):
    low = instr.lower()
    if "seq" in low and ("图" in instr or "表" in instr):
        return "caption"
    if "addin zotero_bibl" in low or "addin en.reflist" in low:
        return "bibliography"
    if "addin zotero_item" in low:
        return "citation_zotero"
    if "addin en.cite" in low:
        return "citation_endnote"
    if low.strip().startswith("ref ") or " ref " in low:
        return "crossref"
    return "other"


def parse_zotero(instr):
    idx = instr.find("{")
    raw = extract_balanced_json(instr, idx)
    ids, titles = [], []
    if raw:
        try:
            data = json.loads(raw)
            for it in data.get("citationItems", []):
                if it.get("id"):
                    ids.append(it["id"])
                item_data = it.get("itemData") or {}
                if item_data.get("title"):
                    titles.append(item_data["title"])
        except Exception:
            pass
    return {"item_keys": ids, "titles": titles, "json_ok": bool(raw)}


def parse_endnote(instr):
    rec = re.findall(r"<rec-number>\s*(\d+)\s*</rec-number>", instr)
    titles = re.findall(r"<title>\s*(.*?)\s*</title>", instr, flags=re.S)
    return {
        "rec_numbers": rec,
        "titles": [t.strip() for t in titles if t.strip()],
    }


def scan(docx_path, want_raw=False):
    xml_bytes = read_document_xml(docx_path)
    root = ET.fromstring(xml_bytes)
    body = root.find(qn("w:body"))
    if body is None:
        raise ValueError("document.xml 中未找到 w:body")

    page = 1
    para_index = 0
    captions = []
    citations = []
    bibliographies = []
    field_kind_count = {}
    headings = {}       # level -> text（当前生效的各级标题）

    for p in body.iter(qn("w:p")):
        para_index += 1
        para_text, fields, page = parse_paragraph(p, page)
        page_at = page

        # 先判定标题，更新当前章节上下文
        lvl = heading_level(p, para_text)
        if lvl:
            headings[lvl] = para_text.strip()
            for deeper in list(headings):
                if deeper > lvl:
                    del headings[deeper]

        chapter = headings.get(1, "")
        section = headings.get(2, "")

        for instr, result in fields:
            kind = classify_field(instr)
            field_kind_count[kind] = field_kind_count.get(kind, 0) + 1

            if kind == "caption":
                is_table = "表" in instr
                captions.append({
                    "type": "table" if is_table else "figure",
                    "number": result.strip(),
                    "text": para_text.strip(),
                    "page_est": page_at,
                    "para": para_index,
                    "chapter": chapter,
                    "section": section,
                })
            elif kind in ("citation_zotero", "citation_endnote"):
                if kind == "citation_zotero":
                    info = parse_zotero(instr)
                    src, ident = "zotero", info["item_keys"]
                    title = info["titles"][0] if info["titles"] else None
                else:
                    info = parse_endnote(instr)
                    src, ident = "endnote", info["rec_numbers"]
                    title = info["titles"][0] if info["titles"] else None
                citations.append({
                    "source": src,
                    "displayed": result.strip(),
                    "identity": ident,
                    "title": title,
                    "page_est": page_at,
                    "para": para_index,
                    "chapter": chapter,
                    "raw": instr[:300] if want_raw else None,
                })
            elif kind == "bibliography":
                bibliographies.append({
                    "result_len": len(result),
                    "preview": result.strip()[:200],
                    "para": para_index,
                })

    groups = group_captions(captions)
    return {
        "captions": captions,
        "groups": groups,
        "citations": citations,
        "bibliographies": bibliographies,
        "field_kind_count": field_kind_count,
        "paragraphs": para_index,
        "pages_est": page,
    }


def group_captions(captions):
    """按 章 -> 节 分组，保持出现顺序。"""
    order = []
    tree = {}
    for c in captions:
        ch = c.get("chapter") or "（未识别章节）"
        sec = c.get("section") or ""
        if ch not in tree:
            tree[ch] = {"__order__": [], }
            order.append(ch)
        node = tree[ch]
        if sec not in node:
            node[sec] = []
            node["__order__"].append(sec)
        node[sec].append({
            "type": c["type"], "number": c["number"],
            "text": c["text"], "page_est": c["page_est"],
        })
    result = []
    for ch in order:
        node = tree[ch]
        secs = []
        for sec in node["__order__"]:
            secs.append({"section": sec, "captions": node[sec]})
        total = sum(len(s["captions"]) for s in secs)
        result.append({"chapter": ch, "total": total, "sections": secs})
    return result


def main():
    ap = argparse.ArgumentParser(description="离线扫描 .docx 的题注与引用（零 COM）")
    ap.add_argument("docx", help=".docx 文件路径")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--flat", action="store_true", help="平铺列出题注，不分组")
    args = ap.parse_args()

    t0 = time.perf_counter()
    try:
        res = scan(args.docx, want_raw=args.raw)
    except FileNotFoundError:
        print(f"❌ 找不到文件: {args.docx}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 解析失败: {e}")
        sys.exit(2)
    dt = (time.perf_counter() - t0) * 1000

    if args.json:
        print(json.dumps({**res, "elapsed_ms": round(dt, 1)}, ensure_ascii=False, indent=2))
        return

    caps = res["captions"]
    figs = [c for c in caps if c["type"] == "figure"]
    tabs = [c for c in caps if c["type"] == "table"]
    print(f"文件: {args.docx}")
    print(f"段落数: {res['paragraphs']}  估算页数: {res['pages_est']}  解析耗时: {dt:.1f} ms")
    print(f"域类型统计: {res['field_kind_count']}")
    print(f"\n=== 题注 {len(caps)} 个（图 {len(figs)} / 表 {len(tabs)}）===")

    if args.flat:
        for c in caps[:300]:
            mark = "表" if c["type"] == "table" else "图"
            print(f"  [{mark} p{c['page_est']:>3}] {c['text'][:60]}")
    else:
        for g in res["groups"]:
            print(f"\n▍{g['chapter']}  （{g['total']} 个）")
            for sec in g["sections"]:
                if sec["section"]:
                    print(f"   ├ {sec['section']}")
                for c in sec["captions"]:
                    mark = "表" if c["type"] == "table" else "图"
                    prefix = "   │  " if sec["section"] else "   "
                    print(f"{prefix}[{mark} p{c['page_est']:>3}] {c['text'][:56]}")

    cits = res["citations"]
    print(f"\n=== 引用 {len(cits)} 处 ===")
    for ci in cits[:200]:
        ident = ",".join(ci["identity"]) if ci["identity"] else "(未提取到ID)"
        title = ci["title"] or "(标题不在域内/需查文献表)"
        print(f"  [{ci['source']:<7} p{ci['page_est']:>3}] {ci['displayed']:<8} id={ident}  《{title[:40]}》")
        if ci.get("raw"):
            print(f"           raw: {ci['raw'][:160]}")
    if len(cits) > 200:
        print(f"  ...（另有 {len(cits) - 200} 处）")

    bibs = res["bibliographies"]
    print(f"\n=== 参考文献表域 {len(bibs)} 处 ===")
    for b in bibs[:5]:
        print(f"  [para {b['para']}] 长度 {b['result_len']}，预览: {b['preview']}")


if __name__ == "__main__":
    main()
