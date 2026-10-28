# plugins/navigator/navigator_jump.py
"""题录跳转与列表过滤（从 word_navigator_dialog.py 拆出）。

职责：按关键字过滤两份列表、双击条目跳到 Word 对应位置、返回上一位置。
依赖宿主面板的 `word` / `doc` / `history_stack` / 列表控件，故做成 mixin。
"""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from plugins.navigator.offline_scan import chapter_label


class JumpMixin:
    """过滤与跳转方法。"""

    def filter_lists(self, *_args):
        """按「章节下拉」+「关键字」过滤两份列表。

        章节数据来自离线扫描（`_chapters`）；COM 路径不提供章节时下拉只有"全部"，
        行为与原来一致（纯关键字过滤）。
        """
        keyword = self.search_edit.text().strip().lower()
        chapter = self._current_chapter()
        self.table_list.clear()
        self.figure_list.clear()
        for item in self.table_captions:
            if chapter >= 0 and getattr(item, "chapter", -1) != chapter:
                continue
            if keyword and keyword not in item.display.lower():
                continue
            self.table_list.addItem(item.display)
        for item in self.figure_captions:
            if chapter >= 0 and getattr(item, "chapter", -1) != chapter:
                continue
            if keyword and keyword not in item.display.lower():
                continue
            self.figure_list.addItem(item.display)

    def _current_chapter(self) -> int:
        """当前选中的章节下标；-1 表示"全部章节"。控件不存在时也按全部处理。"""
        combo = getattr(self, "chapter_combo", None)
        if combo is None:
            return -1
        data = combo.currentData()
        return data if isinstance(data, int) else -1

    def _populate_chapters(self) -> None:
        """用离线扫描到的章节填充下拉框（第一项固定"全部章节"）。

        章节名按**当前语言**现拼（`chapter_label(..., self.tr)`），
        所以切语言时重建这个下拉即可，不用重新扫描文档。
        """
        combo = getattr(self, "chapter_combo", None)
        if combo is None:
            return
        chapters = list(getattr(self, "_chapters", []) or [])
        previous = combo.currentData()          # 切语言不该顺手改掉用户选的章节
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self.tr("chapter.all"), -1)
        for index, title in enumerate(chapters):
            combo.addItem(chapter_label(index, title, self.tr), index)
        combo.setCurrentIndex(0)
        find_data = getattr(combo, "findData", None)     # 测试替身没有这个方法
        if isinstance(previous, int) and callable(find_data):
            combo.setCurrentIndex(max(0, find_data(previous)))
        combo.setEnabled(bool(chapters))
        combo.blockSignals(False)

    def _render_lists(self) -> None:
        """重建章节下拉并重画两份列表（离线扫描完成时调用）。"""
        self._populate_chapters()
        self.filter_lists()

    def _get_item_by_display(self, display_text, captions):
        text_only = display_text.rsplit("  [", 1)[0] if "  [" in display_text else display_text
        for item in captions:
            if item.display.startswith(text_only):
                return item
        return None

    # ---------- 跳转 ----------
    def on_table_double_clicked(self, list_item):
        self._jump_to_caption(self.table_list, self.table_captions, list_item)

    def on_figure_double_clicked(self, list_item):
        self._jump_to_caption(self.figure_list, self.figure_captions, list_item)

    def _jump_to_caption(self, list_widget, captions, list_item):
        if not list_item:
            return
        item = self._get_item_by_display(list_item.text(), captions)
        if not item:
            return
        try:
            word, doc, _ = self._get_word_document()
            if not word or not doc:
                return
            word.ScreenUpdating = False
            try:
                current_range = word.ActiveWindow.Selection.Range.Duplicate
                self.history_stack.append(current_range)
                if len(self.history_stack) > self.max_history:
                    self.history_stack.pop(0)
            except:
                pass
            if item.select_in_document(doc):
                word.ActiveWindow.ScrollIntoView(word.Selection.Range, True)
                self._set_status("status.jumped", target=list_item.text().split("[")[0].strip())
            else:
                self._set_status("status.jump_failed")
        except Exception as e:
            QMessageBox.critical(self, self.tr("error.jump_title"), str(e))
        finally:
            try:
                word.ScreenUpdating = True
            except:
                pass

    def go_back(self):
        if not self.history_stack:
            self._set_status("status.no_history")
            return
        try:
            word, _, _ = self._get_word_document()
            if not word:
                return
            word.ScreenUpdating = False
            prev_range = self.history_stack.pop()
            prev_range.Select()
            word.ActiveWindow.ScrollIntoView(prev_range, True)
            self._set_status("status.back")
        except Exception as e:
            QMessageBox.critical(self, self.tr("error.back_title"), str(e))
        finally:
            try:
                word.ScreenUpdating = True
            except:
                pass

