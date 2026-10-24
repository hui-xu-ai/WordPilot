# plugins/navigator/caption_item.py
"""题录条目（从 word_navigator_dialog.py 抽出，供面板与扫描/跳转 mixin 共用，避免循环导入）。"""
from __future__ import annotations


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

    def _select_by_text(self, doc) -> bool:
        """按文案定位（离线扫描没有 COM 引用）。取首个完整匹配，找不到就再试短前缀。"""
        # 列表里显示的是"文案  [第3页]"，搜索要剔掉页码尾巴
        text = (self.display or "").split("  [", 1)[0].strip()
        if not text:
            return False
        try:
            find = doc.Content.Find
            find.ClearFormatting()
            find.Text = text
            find.Forward = True
            find.Wrap = 0            # wdFindStop：别绕回文档开头
            if find.Execute():
                return True
        except Exception:  # noqa: BLE001  Find 失败不该抛到界面
            return False
        # 文案可能带编号域前缀（如"图 1.9"由 SEQ 渲染），退一步只搜"图 1.9"这种前缀片段
        head = text.split("  [", 1)[0]
        for candidate in (head[:24], head[:16]):
            try:
                find.Text = candidate
                if find.Execute():
                    return True
            except Exception:  # noqa: BLE001
                return False
        return False
