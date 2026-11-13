# plugins/navigator/caption_item.py
"""题录条目（从 word_navigator_dialog.py 抽出，供面板与扫描/跳转 mixin 共用，避免循环导入）。"""
from __future__ import annotations

import re

#: 题注编号前缀（"图 1.1" / "表 2-3" / "Figure 1.1"）：离线来源定位时要去掉，
#: 因为 Word 里的编号常由 SEQ 域渲染，按"图 1.1"整串搜不到
_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:图|表|Figure|Table)\s*[0-9０-９]+(?:[.\-－．_][0-9０-９]+)*\s*")

#: 序列域里表示"表"与"图"的关键词。中英文都要认：中文学位论文写 `SEQ 图`/`SEQ 表`，
#: 英文模板与 EndNote 导出写 `SEQ figure`/`SEQ table` —— 只认中文会让 COM 刷新扫出 0 条，
#: 把离线扫描已经拿到的题录整份清空（用户现场：点「刷新列表」后题录全没了）。
_TABLE_SEQ_HINTS = ("表", "TABLE")
_FIGURE_SEQ_HINTS = ("图", "FIGURE")


def caption_kind(field_code) -> str | None:
    """按序列域指令判断题注种类：`table` / `figure` / None（不是题注域）。

    离线扫描（docx 的 `instrText`）与 COM 刷新（`Field.Code.Text`）共用这一份判定，
    避免两边口径不一致（一边认出 2 条、另一边 0 条，刷新就把列表清空了）。
    `SEQ chapter \\c`（章号）、`SEQ 公式` 之类本来就不含图/表关键词，自然返回 None。
    """
    code = str(field_code or "").upper()
    if "SEQ" not in code:
        return None
    if any(hint in code for hint in _TABLE_SEQ_HINTS):
        return "table"
    if any(hint in code for hint in _FIGURE_SEQ_HINTS):
        return "figure"
    return None


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
                pass  # silent-ok: 域失效时退回区间选择
        if self.start is not None and self.end is not None:
            try:
                rng = doc.Range(self.start, self.end)
                rng.Select()
                return True
            except Exception:  # noqa: BLE001  同上
                pass  # silent-ok: 区间失效时退回全文查找
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
