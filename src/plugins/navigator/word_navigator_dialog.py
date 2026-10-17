# word_navigator_dialog.py
import json
from pathlib import Path

import pythoncom
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTabWidget, QListWidget, QLineEdit, QCheckBox, QProgressBar,
    QMessageBox, QFontDialog
)

from core.services.caption_cache import CaptionCache
from core.services.config_manager import CONFIG_DIR
from core.services.word_bridge import get_word_app
from core.services.word_constants import wdConst

CONFIG_FILE = CONFIG_DIR / "navigator_config.json"
wdFieldSequence = wdConst.wdFieldSequence               # Word 序列域类型常量


def _plugin_version() -> str:
    """插件版本以 plugin.json 为唯一来源，避免与界面文案漂移。"""
    try:
        manifest = json.loads((Path(__file__).with_name("plugin.json")).read_text(encoding="utf-8"))
        return manifest.get("version", "0.0.0")
    except Exception:
        return "0.0.0"


class CaptionItem:
    def __init__(self, display, field=None, start=None, end=None, page=""):
        self.display = display
        self.field = field
        self.start = start
        self.end = end
        self.page = page


    def select_in_document(self, doc):
        if self.field is not None:
            try:
                self.field.Result.Select()
                return True
            except:
                pass
        if self.start is not None and self.end is not None:
            try:
                rng = doc.Range(self.start, self.end)
                rng.Select()
                return True
            except:
                pass
        return False


class WordNavigatorDialog(QDialog):
    #: 在窗口治理服务中的注册 id（置顶偏好与几何记忆都挂在这个 id 下）
    WINDOW_ID = "navigator.main"

    def __init__(self, parent=None, config=None, window_manager=None):
        super().__init__(parent)
        self.app_config = config                    # 内核 ConfigManager（原 navigator_config.json 收敛到这里）
        self.window_manager = window_manager
        # ---------- 安全初始化 COM ----------
        self._com_ok = False
        try:
            pythoncom.CoInitialize()
            self._com_ok = True
        except Exception:
            pass  # 即使 COM 初始化失败，对话框仍然可以显示，只是无法操作 Word

        # ---------- 预定义所有可能在异常路径中访问的属性 ----------
        self._scan_silent = False
        self._captions_cache_data = []       # 防止后续 AttributeError
        self._new_table = []
        self._new_figure = []

        self.setWindowTitle("Word 导航助手 - Xu Hui")
        self.setMinimumSize(450, 600)
        self.resize(450, 600)

        self.config = self.load_config()
        # 置顶不再由本窗口自己加 WindowStaysOnTopHint，
        # 改由 WindowManager 按 PALETTE 层策略统一施加
        self.topmost = self.config.get("topmost", True)

        self.word = None
        self.doc = None
        self.table_captions = []
        self.figure_captions = []
        self.history_stack = []
        self.max_history = 10

        self.cache = CaptionCache()
        self._scanning = False
        self._scan_fingerprint = None

        # 保存监测
        self._save_monitor_timer = QTimer(self)
        self._save_monitor_timer.timeout.connect(self._check_document_saved)
        self._last_saved_state = False

        # 标签页记忆
        self.last_tab_index = 0          # 0:表格, 1:图

        self.setup_ui()
        self.apply_config()

        QTimer.singleShot(300, self.load_captions)

    # ---------- 配置 ----------
    # 历史遗留：配置原先存在独立的 config/navigator_config.json，
    # 与主配置不通。现在统一收敛进主配置，旧文件只做一次性迁移读取。
    def load_config(self):
        legacy = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    legacy = json.load(f) or {}
            except Exception:
                legacy = {}
        if self.app_config is None:
            return legacy
        return {
            "topmost": self.app_config.get("navigator.topmost", legacy.get("topmost", True)),
            "font_family": self.app_config.get("navigator.font_family",
                                               legacy.get("font_family", "Microsoft YaHei")),
            "font_size": self.app_config.get("navigator.font_size", legacy.get("font_size", 12)),
        }

    def save_config(self):
        if self.app_config is None:
            return
        self.app_config.set("navigator.topmost", self.topmost)
        self.app_config.set("navigator.font_family", self.current_font.family())
        self.app_config.set("navigator.font_size", self.current_font.pointSize())

    def apply_config(self):
        family = self.config.get("font_family", "Microsoft YaHei")
        size = self.config.get("font_size", 12)
        self.current_font = QFont(family, size)
        self.setFont(self.current_font)

    # ---------- UI ----------
    def setup_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton("🔄 刷新列表")
        self.btn_refresh.clicked.connect(self.load_captions)
        toolbar.addWidget(self.btn_refresh)

        self.btn_font = QPushButton("🔤 字体")
        self.btn_font.clicked.connect(self.change_font)
        toolbar.addWidget(self.btn_font)

        self.btn_about = QPushButton("ℹ️ 关于")
        self.btn_about.clicked.connect(lambda: QMessageBox.information(self, "关于",
            f"Word导航助手 v{_plugin_version()}\n手动刷新仅更新文本，保存后自动更新页码"))
        toolbar.addWidget(self.btn_about)

        self.chk_topmost = QCheckBox("📌 置顶")
        self.chk_topmost.setChecked(self.topmost)
        self.chk_topmost.stateChanged.connect(self.toggle_topmost)
        toolbar.addStretch()
        toolbar.addWidget(self.chk_topmost)
        layout.addLayout(toolbar)

        self.btn_return = QPushButton("↰  返回上一位置")
        self.btn_return.setStyleSheet("""
            QPushButton {
                background-color: #4a6cd4;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #3a5ab3;
            }
        """)
        self.btn_return.clicked.connect(self.go_back)
        layout.addWidget(self.btn_return)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 筛选："))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入关键词过滤...")
        self.search_edit.textChanged.connect(self.filter_lists)
        search_layout.addWidget(self.search_edit)
        layout.addLayout(search_layout)

        self.tab_widget = QTabWidget()
        self.tab_widget.currentChanged.connect(self._on_tab_changed)  # 记录标签页切换
        self.table_list = QListWidget()
        self.figure_list = QListWidget()
        self.tab_widget.addTab(self.table_list, "📊 表题注")
        self.tab_widget.addTab(self.figure_list, "📈 图题注")
        layout.addWidget(self.tab_widget)

        self.table_list.itemDoubleClicked.connect(self.on_table_double_clicked)
        self.figure_list.itemDoubleClicked.connect(self.on_figure_double_clicked)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.status_label)

    def _on_tab_changed(self, index):
        self.last_tab_index = index

    # ---------- 字体 ----------
    def change_font(self):
        result = QFontDialog.getFont(self.current_font, self, "选择字体")
        font = result[0] if isinstance(result[0], QFont) else result[1]
        ok = result[1] if isinstance(result[0], QFont) else result[0]
        if ok and isinstance(font, QFont):
            self.current_font = font
            self.setFont(font)
            self.save_config()

    def toggle_topmost(self, state):
        self.topmost = (state == Qt.Checked)
        self.save_config()
        if self.window_manager is not None:
            self.window_manager.set_palette_topmost(self.WINDOW_ID, self.topmost)

    # ---------- Word 连接与保存监测 ----------
    def _get_word_document(self):
        if not self._com_ok:
            self.status_label.setText("COM 初始化失败，无法连接 Word")
            return None, None, ""
        try:
            self.word = get_word_app()
            # 不在此处关闭 ScreenUpdating：连接动作不应改变渲染状态，
            # 各操作（扫描/跳转/返回/缓存页码更新）自行配对开关
            self.doc = self.word.ActiveDocument
            if self.doc is None:
                self._stop_save_monitor()
                return None, None, ""
            self._start_save_monitor()
            return self.word, self.doc, self.doc.FullName
        except Exception:
            self.word = None
            self.doc = None
            self._stop_save_monitor()
            return None, None, ""

    def _restore_screen_updating(self, word=None):
        """恢复 Word 屏幕刷新；COM 已断开时静默忽略。"""
        target = word or getattr(self, "_scan_word", None) or self.word
        if target is None:
            return
        try:
            target.ScreenUpdating = True
        except Exception:
            pass


    def _start_save_monitor(self):
        if not self._save_monitor_timer.isActive() and self.doc:
            self._last_saved_state = self.doc.Saved
            self._save_monitor_timer.start(1000)

    def _stop_save_monitor(self):
        if self._save_monitor_timer.isActive():
            self._save_monitor_timer.stop()

    # ---------- 保存监测 ----------
    def _check_document_saved(self):
        try:
            if not self.doc:
                return
            current = self.doc.Saved
            if not self._last_saved_state and current:  # 从未保存变为已保存
                QTimer.singleShot(3000, self._auto_refresh)   # 延迟3秒确保Word空闲
            self._last_saved_state = current
        except:
            self._stop_save_monitor()

    def _auto_refresh(self):
        """Word保存后自动刷新：更新文本和页码，静默执行"""
        if self.isVisible() and not self._scanning:
            self._refresh_captions(update_pages=True, silent=True)

    # ---------- 刷新入口 ----------
    def load_captions(self):
        """手动刷新：更新文本，不更新页码"""
        self._refresh_captions(update_pages=False, silent=False)


    def _refresh_captions(self, update_pages=True, silent=False):       #  2026.05.18
        self._captions_cache_data = []   # 每次刷新前都重置，保证属性存在
        word, doc, full_path = self._get_word_document()
        if not doc:
            self.status_label.setText("未找到打开的 Word 文档，请确认 Word 已启动并打开文件")
            return
        if self._scanning:
            self.status_label.setText("后台扫描正在进行中，请稍后...")
            return
            
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
                self.status_label.setText(f"缓存加载失败：{e}，即将进行全量扫描")
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
            self.status_label.setText("正在扫描题注..." if silent else f"扫描域 0/{total_fields}...")
            QTimer.singleShot(1, self._scan_fields_batch)
        except Exception as e:
            self.status_label.setText(f"初始化出错: {e}")
            self.progress_bar.setVisible(False)
            self._scanning = False
            self._restore_screen_updating(word)   # 异常路径也要恢复 Word 屏幕刷新

    # ---------- 高速域扫描（利用域类型筛选）----------
    def _scan_fields_batch(self):
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
                self.status_label.setText(f"扫描域 {end_index}/{self._scan_total}...")
            if self._scan_index <= self._scan_total:
                QTimer.singleShot(1, self._scan_fields_batch)
            else:
                self._process_seq_info()
        except Exception as e:
            self.status_label.setText(f"扫描中断: {e}")
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
        self.status_label.setText("更新题注中..." if self._scan_silent else f"处理题注 0/{total}...")
        QTimer.singleShot(1, self._process_seq_batch)

    def _process_seq_batch(self):
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
                        display = f"{text}  [第{page_num}页]"
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
                self.status_label.setText(f"处理题注 {end_index}/{total}...")
            if self._seq_index < total:
                QTimer.singleShot(1, self._process_seq_batch)
            else:
                self._finish_scan(len(self._new_table), len(self._new_figure))
        except Exception as e:
            # 外层兜底：QTimer 回调里未捕获的异常会吞掉且不再调度，
            # 必须在此恢复屏幕刷新与扫描状态
            self.status_label.setText(f"题注处理中断: {e}")
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
                display = f"{text}  [第{page_num}页]"
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

            # 3. 更新实例数据源
            self.table_captions = self._new_table
            self.figure_captions = self._new_figure

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
            msg = f"快速加载完成，共 {total} 个题注（表 {len(self._new_table)}，图 {len(self._new_figure)}）"
            if update_pages:
                msg += " [页码已更新]"
            else:
                msg += " [使用缓存页码]"
            self.status_label.setText(msg)

            if silent:
                QTimer.singleShot(3000, lambda: self.status_label.setText("就绪"))

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
            self.status_label.setText("自动更新完成")
            QTimer.singleShot(3000, lambda: self.status_label.setText("就绪"))
        else:
            self.status_label.setText(
                f"加载完成，共 {num_table + num_figure} 个题注（表 {num_table}，图 {num_figure}）"
                + (" [已更新页码]" if self._scan_update_pages else " [沿用页码]")
            )
        self.progress_bar.setVisible(False)
        self._scanning = False
        self._scan_silent = False

    # ---------- 过滤列表（不再自动切换标签页）----------
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
                self.status_label.setText(f"已跳转至「{list_item.text().split('[')[0].strip()}」")
            else:
                self.status_label.setText("跳转失败：无法定位题注")
        except Exception as e:
            QMessageBox.critical(self, "跳转失败", str(e))
        finally:
            try:
                word.ScreenUpdating = True
            except:
                pass

    def go_back(self):
        if not self.history_stack:
            self.status_label.setText("没有可返回的位置")
            return
        try:
            word, _, _ = self._get_word_document()
            if not word:
                return
            word.ScreenUpdating = False
            prev_range = self.history_stack.pop()
            prev_range.Select()
            word.ActiveWindow.ScrollIntoView(prev_range, True)
            self.status_label.setText("已返回跳转前位置")
        except Exception as e:
            QMessageBox.critical(self, "返回失败", str(e))
        finally:
            try:
                word.ScreenUpdating = True
            except:
                pass

    def closeEvent(self, event):        #  2026.05.18
        self._stop_save_monitor()
        self.cache.close()
        if self._com_ok:
            try:
                pythoncom.CoUninitialize()
            except:
                pass
        super().closeEvent(event)