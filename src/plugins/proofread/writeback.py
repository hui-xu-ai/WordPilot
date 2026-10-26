# plugins/proofread/writeback.py
"""把校对建议写回 Word（含 B01 的原文校验）。

从 plugin.py 抽出来：那个文件已接近行数阈值，而这段与 Qt 事件无关，便于单测。
"""
from __future__ import annotations

import logging
import re
import time

from PySide6.QtWidgets import QMessageBox

from core.services.word_bridge import get_word_app
from plugins.proofread.word_monitor import (TokenizedSentence, clear_error_marks,
                                            get_sentence_range, split_trailing_chars)

logger = logging.getLogger(__name__)


def apply_correction(plugin, corrected_text: str, start: int | None = None,
                     end: int | None = None, original_text: str | None = None) -> None:
    """把建议写回 Word。

    含图片/公式的句子无法安全地整体替换，只清除标记并提示手动修改；
    纯文本句则只替换主体部分，保留句尾的段落符等不可见字符。
    """
    started = time.perf_counter()
    word = None
    prev_screen_updating = None
    try:
        word = get_word_app()
        doc = word.ActiveDocument
        # 替换期间冻结重绘：Word 默认每次 COM 变更都重画，长句/大文档上很吃时间
        try:
            prev_screen_updating = word.ScreenUpdating
            word.ScreenUpdating = False
        except Exception:                  # 某些受保护视图不允许改，忽略即可
            prev_screen_updating = None

        start, end = plugin.normalize_span(start, end)
        if start is not None and end is not None:
            sent_range = doc.Range(start, end)
        else:
            sent_range = get_sentence_range(word.Selection)

        if sent_range.Start >= sent_range.End:
            plugin._status(plugin.tr("status.sentence_not_found"))
            return

        tokenized = TokenizedSentence(sent_range)
        has_object = any(block["type"] == "object" for block in tokenized.get_blocks())
        sent_text = sent_range.Text

        # 分离主体和尾部
        body_text, tail_text = split_trailing_chars(sent_text)

        # B01：写回前校验原文。生成建议后若用户切了文档或继续编辑，
        # 按旧坐标写下去会覆盖别的文本（审查用真实 Word 复现过）。
        #
        # 注意：`sent_range.Text` 会带上句首空白/制表符，而建议里的原文是纯文本，
        # 若原样比较，正常写回也会被判"已过期"（表现为点了应用毫无反应）。
        # 因此比对前去掉所有空白；只要当前文本**包含**原文就放行。
        # 含图片/公式的句子只清标记、不写文本，天然不会覆盖别人的内容，不参与判定。
        if original_text is not None and not has_object:
            expected_body, _ = split_trailing_chars((original_text or "").strip())
            expected_norm = re.sub(r"\s+", "", expected_body)
            current_norm = re.sub(r"\s+", "", body_text)
            pure_norm = re.sub(r"\s+", "", getattr(tokenized, "pure_text", "") or "")
            matched = bool(expected_norm) and (
                expected_norm in current_norm or
                (bool(pure_norm) and expected_norm in pure_norm))
            if not matched:
                logger.warning("拒绝写回：原句已变化（%r ≠ %r）",
                               current_norm[:40], expected_norm[:40])
                plugin._status(plugin.tr("status.stale_suggestion"))
                plugin._warn_stale_suggestion()
                return

        if has_object:
            clear_error_marks(sent_range.Start, sent_range.End)
            plugin._notify_manual_edit()
            plugin._status(plugin.tr("status.marks_cleared"))
        else:
            clear_error_marks(sent_range.Start, sent_range.End)
            if tail_text:
                replace_range = doc.Range(sent_range.Start,
                                          sent_range.Start + len(body_text))
            else:
                replace_range = sent_range.Duplicate

            corrected_body = corrected_text
            if tail_text and corrected_text.endswith(tail_text):
                corrected_body = corrected_text[:-len(tail_text)]

            replace_range.Text = corrected_body
            plugin._status(plugin.tr("status.applied"))

    except Exception as exc:               # noqa: BLE001 - 写回失败必须让用户看见
        QMessageBox.warning(None, plugin.tr("error.replace_title"),
                            plugin.tr("error.replace_failed", error=exc))
    finally:
        if word is not None and prev_screen_updating is not None:
            try:
                word.ScreenUpdating = prev_screen_updating
            except Exception:              # noqa: BLE001
                pass
        logger.info("apply_correction 用时 %.0f ms", (time.perf_counter() - started) * 1000)
