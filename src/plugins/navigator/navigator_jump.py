# plugins/navigator/navigator_jump.py
"""题录跳转与列表过滤（从 word_navigator_dialog.py 拆出）。

职责：按关键字过滤两份列表、双击条目跳到 Word 对应位置、返回上一位置。
依赖宿主面板的 `word` / `doc` / `history_stack` / 列表控件，故做成 mixin。
"""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


class JumpMixin:
    """过滤与跳转方法。"""
    def filter_lists(self):
        keyword = self.search_edit.text().lower()
        self.table_list.clear()
        self.figure_list.clear()
        for item in self.table_captions:
            if keyword in item.display.lower():
                self.table_list.addItem(item.display)
        for item in self.figure_captions:
            if keyword in item.display.lower():
                self.figure_list.addItem(item.display)

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
                self.status_label.setText(self.tr("status.jumped", target=list_item.text().split("[")[0].strip()))
            else:
                self.status_label.setText(self.tr("status.jump_failed"))
        except Exception as e:
            QMessageBox.critical(self, self.tr("error.jump_title"), str(e))
        finally:
            try:
                word.ScreenUpdating = True
            except:
                pass

    def go_back(self):
        if not self.history_stack:
            self.status_label.setText(self.tr("status.no_history"))
            return
        try:
            word, _, _ = self._get_word_document()
            if not word:
                return
            word.ScreenUpdating = False
            prev_range = self.history_stack.pop()
            prev_range.Select()
            word.ActiveWindow.ScrollIntoView(prev_range, True)
            self.status_label.setText(self.tr("status.back"))
        except Exception as e:
            QMessageBox.critical(self, self.tr("error.back_title"), str(e))
        finally:
            try:
                word.ScreenUpdating = True
            except:
                pass

