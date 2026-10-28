# plugins/navigator/navigator_scan.py
"""题录扫描流水线（从 word_navigator_dialog.py 拆出，控制单文件长度）。

职责：连接 Word → 缓存比对 → 分批扫标题/题注 → 序列域(SEQ)处理 → 落缓存 → 刷新列表。
所有方法依赖宿主面板（WordNavigatorPanel）的控件与状态（进度条、状态栏、两份列表、缓存），
因此做成 mixin：`class WordNavigatorPanel(ScanMixin, JumpMixin, QWidget)`。
mixin 不定义 `__init__`、不持有状态。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QTimer

from plugins.navigator.caption_item import CaptionItem

logger = logging.getLogger(__name__)


class ScanMixin:
    """扫描与缓存相关方法。"""

    # ---------- 状态栏（存 key，切语言时按原文重译）----------
    def _set_status(self, *parts, **params) -> None:
        """设置状态栏并记住**词条 key**（成品文案没法反查原文，切语言就会残留旧语言）。

        每个段落写成 `"key"` 或 `("key", {"name": value})`；多段落按顺序拼接，
        例如「加载完成…」+「[页码已更新]」。`params` 作用于**第一段**的占位符。
        切语言时 `_render_status()` 按新语言重译。
        """
        normalized: list[tuple[str, dict]] = [
            part if isinstance(part, tuple) else (part, {}) for part in parts
        ]
        if normalized and params:
            key, own = normalized[0]
            normalized[0] = (key, {**own, **params})
        self._status_parts = normalized
        self._render_status()

    def _render_status(self) -> None:
        """按当前界面语言重画状态栏（未设置过则保持原样）。"""
        parts = getattr(self, "_status_parts", None)
        if not parts:
            return
        self.status_label.setText("".join(self.tr(key, **params) for key, params in parts))

    def _retranslate_captions(self) -> None:
        """重拼题录显示文本里的页码后缀（`[第N页]` / `[p.N]`）。

        题录基名是**文档原文**，不翻译；只有我们加的页码后缀随语言变。
        """
        for captions in (self.table_captions, self.figure_captions):
            for item in captions:
                page = getattr(item, "page", "")
                if not page:
                    continue
                base = item.display.rsplit("  [", 1)[0]
                item.display = self.tr("caption.display", text=base, page=page)

    def _apply_chapter_tags(self) -> None:
        """COM 扫描补出页码/跳转引用后，把离线扫描的章节信息续回条目上。

        否则"离线先出列表（有章节）→ 点刷新走 COM（有页码）"这一步会把章节抹掉，
        章节下拉退化成只有"全部章节"（用户实测反馈）。
        """
        chapter_map = getattr(self, "_chapter_map", None)
        if not chapter_map:
            return
        from plugins.navigator.offline_scan import chapter_key

        for captions in (self.table_captions, self.figure_captions):
            for item in captions:
                key = chapter_key(item.display)
                if key in chapter_map:
                    item.chapter = chapter_map[key]

    def _try_offline_scan(self, full_path: str) -> bool:
        """离线快速刷新：解析 docx 拿章节与题注，成功则直接渲染列表并返回 True。

        - 不碰 COM ⇒ GUI 不再卡 ~2s；
        - 拿不到页码（排版结果）⇒ 列表不再显示页码，缓存里的旧页码仍可用于其它逻辑；
        - 任何异常都退回 COM 路径（不静默失败，只记日志）。
        """
        if self._closing or not full_path:
            return False
        try:
            from plugins.navigator.offline_scan import scan_docx

            result = scan_docx(full_path)
        except Exception:
            logger.exception("离线扫描失败，退回 COM 路径: %s", full_path)
            return False
        if not result.items:
            return False        # 没有可识别题录时交给 COM 再试一次（可能用了不常见的样式）

        self._chapters = list(result.chapter_titles)
        from plugins.navigator.offline_scan import chapter_key

        # 题录文本 → 章节下标：COM 路径补页码后据此把章节信息"续"回去
        self._chapter_map = {chapter_key(i.text): i.chapter for i in result.items}
        self.table_captions = [CaptionItem(display=i.text, chapter=i.chapter)
                               for i in result.table_items()]
        self.figure_captions = [CaptionItem(display=i.text, chapter=i.chapter)
                                for i in result.figure_items()]
        self._render_lists()
        self._set_status("status.offline_done",
                         tables=len(self.table_captions),
                         figures=len(self.figure_captions))
        return True
    def _refresh_captions(self, force_com=False, silent=False):       #  2026.05.18
        """刷新题录。

        `force_com=False`（默认：首次打开、保存后自动刷新）→ 只走离线解析：快、不动页码；
        `force_com=True`（工具栏「刷新列表」）→ 走 COM 全量扫描并更新页码。
        离线失败（文件不可读、没有可识别题录）时才退回 COM，保证功能不丢。
        """
        self._captions_cache_data = []   # 每次刷新前都重置，保证属性存在
        word, doc, full_path = self._get_word_document()
        if not doc:
            self._set_status("status.no_doc")
            return
        if self._scanning:
            self._set_status("status.scanning")
            return
        if not force_com and self._try_offline_scan(full_path):
            return
        update_pages = force_com        # 页码只在显式全量刷新时更新
        
        # ---------- 缓存优先 ----------
        fingerprint = self.cache.get_document_fingerprint(full_path)
        cached_data = None
        if fingerprint:
            cached_data = self.cache.get_document_cache(fingerprint)
            if not isinstance(cached_data, list):   # 数据无效则视为未命中
                cached_data = None

        if cached_data is not None:
            # 命中缓存，尝试从缓存加载
            try:
                self._load_from_cache(word, doc, fingerprint, cached_data, update_pages, silent)
                return
            except Exception as e:
                self._set_status("status.cache_failed", error=e)
                # 加载失败直接进行全量扫描，不要递归调用 _refresh_captions
        # 未命中缓存或加载失败 → 全量扫描
        self._do_full_scan(word, doc, full_path, update_pages, silent)


    def _do_full_scan(self, word, doc, full_path, update_pages, silent):        #  2026.05.18
        """执行全量扫描，不使用缓存"""
        # 除了缓存检查，代码与原来全量扫描部分完全相同
        page_map = {}
        if not update_pages:
            for item in self.table_captions + self.figure_captions:
                if item.start is not None and item.page:
                    page_map[item.start] = item.page

        self._scanning = True
        self._scan_update_pages = update_pages
        self._scan_page_map = page_map
        self._scan_word = word
        self._scan_doc = doc
        self._scan_fingerprint = self.cache.get_document_fingerprint(full_path)
        self._scan_silent = silent

        self._scan_seq_info = []
        self._scan_total = 0
        self._scan_index = 1

        try:
            word.ScreenUpdating = False
            total_fields = doc.Fields.Count
            if total_fields == 0:
                self._finish_scan(0, 0)
                return
            self._scan_total = total_fields
            if not silent:
                self.progress_bar.setMaximum(total_fields)
                self.progress_bar.setValue(0)
                self.progress_bar.setVisible(True)
            if silent:
                self._set_status("status.scanning_captions")
            else:
                self._set_status("status.scan_progress", current=0, total=total_fields)
            QTimer.singleShot(1, self._scan_fields_batch)
        except Exception as e:
            self._set_status("status.init_error", error=e)
            self.progress_bar.setVisible(False)
            self._scanning = False
            self._restore_screen_updating(word)   # 异常路径也要恢复 Word 屏幕刷新

    # ---------- 高速域扫描（利用域类型筛选）----------
    def _scan_fields_batch(self):
        if self._closing:
            return
        batch_size = 50
        try:
            fields = self._scan_doc.Fields
            end_index = min(self._scan_index + batch_size - 1, self._scan_total)
            for i in range(self._scan_index, end_index + 1):
                field = fields.Item(i)
                try:
                    code = field.Code.Text.lower()
                    if "seq " in code and ("seq 表" in code or "seq 图" in code):
                        self._scan_seq_info.append((i, field))
                except:
                    continue
            self._scan_index = end_index + 1
            if not self._scan_silent:
                self.progress_bar.setValue(end_index)
                self._set_status("status.scan_progress", current=end_index, total=self._scan_total)
            if self._scan_index <= self._scan_total:
                QTimer.singleShot(1, self._scan_fields_batch)
            else:
                self._process_seq_info()
        except Exception as e:
            self._set_status("status.scan_aborted", error=e)
            self._scanning = False
            self.progress_bar.setVisible(False)
            self._restore_screen_updating()   # 扫描中断时恢复 Word 屏幕刷新

    # ---------- 处理题注 ----------
    def _process_seq_info(self):
        self._seq_index = 0
        self._added_starts = set()
        self._captions_cache_data = []
        self._new_table = []
        self._new_figure = []
        total = len(self._scan_seq_info)
        if total == 0:
            self._finish_scan(0, 0)
            return
        if not self._scan_silent:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(0)
        if self._scan_silent:
            self._set_status("status.updating")
        else:
            self._set_status("status.process_progress", current=0, total=total)
        QTimer.singleShot(1, self._process_seq_batch)

    def _process_seq_batch(self):
        if self._closing:
            return
        try:
            batch_size = 5
            total = len(self._scan_seq_info)
            end_index = min(self._seq_index + batch_size, total)
            for idx in range(self._seq_index, end_index):
                _, field = self._scan_seq_info[idx]
                try:
                    code = field.Code.Text.lower()
                    seq_type = "表" if "seq 表" in code else "图"
                    para = field.Result.Paragraphs(1)
                    para_range = para.Range
                    start_pos = para_range.Start
                    if start_pos in self._added_starts:
                        continue
                    self._added_starts.add(start_pos)
                    text = para_range.Text.strip().rstrip('\r\n')
                    if text:
                        page_num = "?"
                        if self._scan_update_pages:
                            try:
                                page_num = str(para_range.Information(3))
                            except:
                                pass
                        else:
                            if start_pos in self._scan_page_map:
                                page_num = self._scan_page_map[start_pos]
                            else:
                                try:
                                    page_num = str(para_range.Information(3))
                                except:
                                    pass
                        display = self.tr("caption.display", text=text, page=page_num)
                        item = CaptionItem(display, field=field, start=start_pos,
                                          end=para_range.End, page=page_num)
                        if seq_type == "表":
                            self._new_table.append(item)
                        else:
                            self._new_figure.append(item)
                        self._captions_cache_data.append({
                            'type': 'table' if seq_type == '表' else 'figure',
                            'text': text,
                            'page': page_num,
                            'start': start_pos,
                            'end': para_range.End
                        })
                except:
                    continue
            self._seq_index = end_index
            if not self._scan_silent:
                self.progress_bar.setValue(end_index)
                self._set_status("status.process_progress", current=end_index, total=total)
            if self._seq_index < total:
                QTimer.singleShot(1, self._process_seq_batch)
            else:
                self._finish_scan(len(self._new_table), len(self._new_figure))
        except Exception as e:
            # 外层兜底：QTimer 回调里未捕获的异常会吞掉且不再调度，
            # 必须在此恢复屏幕刷新与扫描状态
            self._set_status("status.process_aborted", error=e)
            self._scanning = False
            self.progress_bar.setVisible(False)
            self._restore_screen_updating()


    def _load_from_cache(self, word, doc, fingerprint, cached_data, update_pages, silent):      #  2026.05.18
        """从缓存快速加载题注。
        参数：
            word, doc：Word 对象
            fingerprint：文档指纹
            cached_data：缓存数据（列表）
            update_pages：是否实时更新页码
            silent：是否为后台静默更新
        异常：
            若加载过程中任何一步失败，直接抛出异常，由调用者负责回退到全量扫描。
        """
        # 1. 将缓存数据同步到实例属性（保证 _finish_scan 等场景可用）
        self._captions_cache_data = cached_data

        # 2. 准备空列表存放新题注
        self._new_table = []
        self._new_figure = []

        try:
            # 仅在需要更新页码时临时关闭屏幕刷新，提升性能
            if update_pages:
                word.ScreenUpdating = False

            # 遍历缓存数据，创建 CaptionItem 对象
            for item_data in cached_data:
                text = item_data['text']
                start_pos = item_data['start']
                end_pos = item_data['end']
                page_num = item_data.get('page', '?')

                # 如果需要更新页码，则通过 COM 实时获取
                if update_pages:
                    try:
                        para_range = doc.Range(start_pos, start_pos).Paragraphs(1).Range
                        page_num = str(para_range.Information(3))
                        item_data['page'] = page_num  # 更新缓存数据，以便后续保存
                    except Exception:
                        pass  # 页码获取失败则保留原值，不中断流程

                # 构造显示文本与数据对象
                display = self.tr("caption.display", text=text, page=page_num)
                item = CaptionItem(
                    display,
                    field=None,          # 缓存恢复时没有 field 对象，依赖 start/end 定位
                    start=start_pos,
                    end=end_pos,
                    page=page_num
                )

                if item_data['type'] == 'table':
                    self._new_table.append(item)
                else:
                    self._new_figure.append(item)

            # 3. 更新实例数据源（把离线章节信息续上）
            self.table_captions = self._new_table
            self.figure_captions = self._new_figure
            self._apply_chapter_tags()

            # 4. 刷新界面列表
            self.filter_lists()

            # 5. 恢复标签页状态
            if self.last_tab_index == 1 and self.figure_list.count() > 0:
                self.tab_widget.setCurrentIndex(1)
            elif self.last_tab_index == 0 and self.table_list.count() > 0:
                self.tab_widget.setCurrentIndex(0)
            else:
                if self.table_list.count():
                    self.tab_widget.setCurrentIndex(0)
                elif self.figure_list.count():
                    self.tab_widget.setCurrentIndex(1)

            # 6. 状态提示
            total = len(self._new_table) + len(self._new_figure)
            self._set_status(("status.fast_loaded", {"total": total,
                                                    "tables": len(self._new_table),
                                                    "figures": len(self._new_figure)}),
                             "status.pages_updated" if update_pages
                             else "status.pages_cached")

            if silent:
                QTimer.singleShot(3000, lambda: self._set_status("status.ready"))

            # 7. 保存更新后的缓存（页码可能已变）
            self.cache.save_document_cache(fingerprint, cached_data)

        except Exception:
            # 任何错误都直接向上抛出，不在此处回退
            raise

        finally:
            # 始终恢复 Word 屏幕刷新和内部扫描标志
            if update_pages:
                try:
                    word.ScreenUpdating = True
                except Exception:
                    pass
            self._scanning = False
            self.progress_bar.setVisible(False)



    def _finish_scan(self, num_table, num_figure):
        self._restore_screen_updating()
        if self._scan_fingerprint:              #  2026.05.18
            data = getattr(self, '_captions_cache_data', [])
            self.cache.save_document_cache(self._scan_fingerprint, data)
            
        self.table_captions = self._new_table
        self.figure_captions = self._new_figure
        self._apply_chapter_tags()
        self.filter_lists()
        # 恢复标签页
        if self.last_tab_index == 1 and self.figure_list.count() > 0:
            self.tab_widget.setCurrentIndex(1)
        elif self.last_tab_index == 0 and self.table_list.count() > 0:
            self.tab_widget.setCurrentIndex(0)
        else:
            if self.table_list.count():
                self.tab_widget.setCurrentIndex(0)
            elif self.figure_list.count():
                self.tab_widget.setCurrentIndex(1)

        if self._scan_silent:
            self._set_status("status.auto_updated")
            QTimer.singleShot(3000, lambda: self._set_status("status.ready"))
        else:
            self._set_status(("status.loaded", {"total": num_table + num_figure,
                                               "tables": num_table,
                                               "figures": num_figure}),
                             "status.pages_updated" if self._scan_update_pages
                             else "status.pages_cached")
        self.progress_bar.setVisible(False)
        self._scanning = False
        self._scan_silent = False


