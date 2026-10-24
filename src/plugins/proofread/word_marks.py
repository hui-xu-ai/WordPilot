# plugins/proofread/word_marks.py
"""Word 里"校对标记"的清除逻辑（从 word_monitor 拆出，避免单文件继续膨胀）。

性能说明：旧实现逐字符走 COM（`Characters(i)` + 读两次 Font 属性），一句话 60 字≈200 次跨进程调用，
用户点「确认」替换时会明显卡顿。现在优先用 Word 的 `Find` 按**格式**一次性替换掉标记，
Find 不可用时才退回逐字符，保证功能不退化。
"""
from __future__ import annotations

import logging

from core.services.word_bridge import get_word_app
from core.services.word_constants import wdConst as constants

logger = logging.getLogger(__name__)

wdUnderlineWavy = constants.wdUnderlineWavy
wdColorRed = constants.wdColorRed


def clear_error_marks(start: int, end: int) -> None:
    """清除区间内的红色波浪线标记。"""
    try:
        word = get_word_app()
        doc = word.ActiveDocument
        sentence_range = doc.Range(start, end)
    except Exception:
        return

    try:
        find = sentence_range.Find
        find.ClearFormatting()
        find.Replacement.ClearFormatting()
        find.Font.Underline = wdUnderlineWavy
        find.Font.UnderlineColor = wdColorRed
        find.Replacement.Font.Underline = 0            # wdUnderlineNone
        find.Format = True
        find.Forward = True
        find.Wrap = 0                                   # wdFindStop
        find.Execute(FindText="", MatchCase=False, MatchWholeWord=False,
                     MatchWildcards=False, MatchSoundsLike=False, MatchAllWordForms=False,
                     Forward=True, Wrap=0, Format=True, ReplaceWith="", Replace=2)  # wdReplaceAll
        return
    except Exception:
        logger.debug("按格式查找清除标记失败，退回逐字符方式", exc_info=True)

    try:
        for i in range(len(sentence_range.Characters)):
            char = sentence_range.Characters(i + 1)
            if char.Font.Underline == wdUnderlineWavy and char.Font.UnderlineColor == wdColorRed:
                char.Font.Underline = 0
    except Exception:
        pass
