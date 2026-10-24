# plugins/navigator/caption_item.py
"""题录条目（从 word_navigator_dialog.py 抽出，供面板与扫描/跳转 mixin 共用，避免循环导入）。"""
from __future__ import annotations

import re

#: 题注编号前缀（"图 1.1" / "表 2-3" / "Figure 1.1"）：离线来源定位时要去掉，
#: 因为 Word 里的编号常由 SEQ 域渲染，按"图 1.1"整串搜不到
_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:图|表|Figure|Table)\s*[0-9０-９]+(?:[.\-－．_][0-9０-９]+)*\s*")


class CaptionItem:
    """一条题录：文案 + 可选序列域 + 起止偏移 + 页码 + 所属章节。

    离线扫描（`offline_scan`）只填 `display` 与 `chapter`（拿不到 COM 引用与页码）；
    COM 扫描填 `field` / `start` / `end` / `page`。跳转对两种来源都成立：
    有域或区间就用它们精确定位，否则按文案 `Find`。
    """

    def __init__(self, display, field=None, start=None, end=None, page="", chapter=-1):
        self.display = display
        self.field = field
        self.start = start
        self.end = end
        self.page = page
        self.chapter = chapter

    def select_in_document(self, doc):
        """在 Word 里选中该题录：序列域 → 字符区间 → 按文案查找（离线来源走这条）。"""
        if self.field is not None:
            try:
                self.field.Result.Select()
                return True
            except Exception:  # noqa: BLE001  COM 对象可能已失效，退回区间选择
                pass
        if self.start is not None and self.end is not None:
            try:
                rng = doc.Range(self.start, self.end)
                rng.Select()
                return True
            except Exception:  # noqa: BLE001  同上
                pass
        return self._select_by_text(doc)

    def search_needles(self) -> list[str]:
        """按优先级给出要搜的文本：整串 → 去掉编号前缀（图 1.1）→ 更短的前缀片段。"""
        text = (self.display or "").split("  [", 1)[0].strip()
        if not text:
            return []
        needles = [text]
        stripped = _NUMBER_PREFIX_RE.sub("", text).strip()
        if stripped and stripped != text:
            needles.append(stripped)
            needles.append(stripped[:16])
        else:
            needles.append(text[:16])
        seen: list[str] = []
        for needle in needles:
            if needle and needle not in seen:
                seen.append(needle)
        return seen

    def _select_by_text(self, doc) -> bool:
        """按文案定位并**把光标移过去**（离线扫描没有 COM 引用）。

        Word 用于跳转的两种写法都用上：
        1) `doc.Application.Selection.Find` —— 命中时光标就在该处，真正的"跳转"；
        2) `doc.Content.Find` 命中后把结果区间 `Select()`（老实现的路子）。
        """
        needles = self.search_needles()
        if not needles:
            return False
        finder = self._selection_finder(doc)
        for needle in needles:
            if self._execute_find(finder, needle):
                return True
        for needle in needles:
            found_range = self._range_find(doc, needle)
            if found_range is not None:
                try:
                    found_range.Select()
                    return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    @staticmethod
    def _selection_finder(doc):
        try:
            return doc.Application.Selection.Find
        except Exception:  # noqa: BLE001  没有 Application（测试替身/Word 异常）
            return None

    @staticmethod
    def _execute_find(finder, needle: str) -> bool:
        if finder is None:
            return False
        try:
            finder.ClearFormatting()
            finder.Text = needle
            finder.Forward = True
            finder.Wrap = 0            # wdFindStop：别绕回文档开头
            return bool(finder.Execute())
        except Exception:  # noqa: BLE001  搜索失败不该抛到界面
            return False

    @staticmethod
    def _range_find(doc, needle: str):
        try:
            content = doc.Content
            finder = content.Find
            finder.ClearFormatting()
            finder.Text = needle
            finder.Forward = True
            finder.Wrap = 0
            if finder.Execute():
                return content
        except Exception:  # noqa: BLE001
            return None
        return None
