# plugins/navigator/caption_item.py
"""题录条目（从 word_navigator_dialog.py 抽出，供面板与扫描/跳转 mixin 共用，避免循环导入）。"""
from __future__ import annotations


class CaptionItem:
    """一条题录：文案 + 可选序列域 + 起止偏移 + 页码。"""

    def __init__(self, display, field=None, start=None, end=None, page=""):
        self.display = display
        self.field = field
        self.start = start
        self.end = end
        self.page = page

    def select_in_document(self, doc):
        """在 Word 里选中该题录（优先用序列域，退化到字符区间）。"""
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
        return False
