# suggestion_window.py
import difflib

from PySide6.QtCore import Qt, Signal, QPoint, QEvent, QSize
from PySide6.QtGui import QColor, QPalette, QIcon, QCursor, QPixmap, QFont  # 悬浮按钮位置--修改17   QPixmap--修改21
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QApplication, QToolButton, \
    QGridLayout, QSizePolicy, QFrame, QGraphicsDropShadowEffect

from infra.utils import resource_path  # 或直接定义在文件内   #  -修改21
from plugins.proofread.word_monitor import clear_error_marks  # --修改17  --修改19 proof_sentence


# def diff_fragments(original: str, corrected: str) -> list:
    # """返回差异片段列表，每个片段包含 original 和 suggest 字符串"""
    # sm = difflib.SequenceMatcher(None, original, corrected)
    # frags = []
    # for tag, i1, i2, j1, j2 in sm.get_opcodes():
        # if tag in ('replace', 'insert', 'delete'):
            # org = original[i1:i2] if tag != 'insert' else ''
            # sug = corrected[j1:j2] if tag != 'delete' else ''
            # if org.strip() == sug.strip():
                # continue
            # frags.append({'original': org, 'suggest': sug})
    # return frags
def diff_fragments(original: str, corrected: str) -> list:      #  --修改22
    """
    精确返回 original 与 corrected 之间的所有字符级差异片段。
    每个片段包含：
        - 'original': 原文中被替换/删除的内容（插入时为空）
        - 'suggest': 建议中替换/插入的内容（删除时为空）
        - 'type': 操作类型 ('replace', 'delete', 'insert')
        - 'ostart', 'oend': 在 original 中的起止索引
        - 'sstart', 'send': 在 corrected 中的起止索引
    """
    sm = difflib.SequenceMatcher(None, original, corrected, autojunk=False)
    frags = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        org = original[i1:i2] if tag != 'insert' else ''
        sug = corrected[j1:j2] if tag != 'delete' else ''
        frags.append({
            'original': org,
            'suggest': sug,
            'type': tag,
            'ostart': i1,
            'oend': i2,
            'sstart': j1,
            'send': j2
        })
    return frags

class SuggestionCard(QWidget):
    accepted = Signal(str, int, int, str)
    ignored = Signal(str, int, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 无边框 + 置顶 + 工具窗口
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)   # 背景透明

        self.original_text = ""
        self.corrected_text = ""
        self.start_pos = 0
        self.end_pos = 0

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---- 内部容器（透明无边框）----
        self.container = QWidget()
        self.container.setObjectName("suggestion_container")
        self.container.setStyleSheet("background: transparent; border: none;")
        main_layout.addWidget(self.container)

        # 容器内部垂直布局
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)  # 无边距，让行直接贴边
        container_layout.setSpacing(6)

        # 差异片段显示区域
        self.diff_widget = QWidget()
        self.diff_layout = QVBoxLayout(self.diff_widget)
        self.diff_layout.setContentsMargins(4, 2, 4, 2)   # 行两侧留一点呼吸空间
        self.diff_layout.setSpacing(4)
        container_layout.addWidget(self.diff_widget)

        # 按钮行（确认 / 忽略）
        self.btn_container = QWidget()
        btn_layout = QHBoxLayout(self.btn_container)
        btn_layout.setContentsMargins(4, 2, 4, 0)       #       左上右下间距
        btn_layout.setSpacing(8)
        self.accept_btn = QPushButton("✔ 确认")
        self.ignore_btn = QPushButton("✘ 忽略")
        self.btn_container.setStyleSheet("border: none;")   #  无边框
        btn_layout.addWidget(self.accept_btn)
        btn_layout.addWidget(self.ignore_btn)
        container_layout.addWidget(self.btn_container)

        # 信号连接（保持不变）
        self.accept_btn.clicked.connect(
            lambda: self.accepted.emit(self.corrected_text, self.start_pos, self.end_pos, self.original_text)
        )
        self.ignore_btn.clicked.connect(
            lambda: self.ignored.emit(self.original_text, self.start_pos, self.end_pos, self.original_text)
        )

        # 默认样式（可被 config 覆盖）
        self._apply_default_style()

    def _apply_default_style(self):
        """设置默认的现代样式（不影响 config 后的自定义）"""
        self.setStyleSheet("""
            QLabel {
                background: transparent;
                border: none;
                font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
                font-size: 14px;
            }
            #diff_row {
                background-color: #eef3ff;      /* 建议窗口颜色 */
                border-radius: 8px;
                padding: 6px 8px;
            }
            #diff_row:hover {
                background-color: #79FFF2;      /* 建议窗口鼠标悬停时的颜色 */
            }
            QPushButton {
                background-color: #eef3ff;      /* 按钮背景 按钮边框 */
                border: 1px solid #ffffff;
                border-radius: 4px;
                padding: 4px 12px;
                color: #333;                /* 文字颜色 */
            }
            QPushButton:hover {
                background-color: #79FFF2;  /* 悬停背景 */
            }
        """)

    def clear_diff(self):
        """清空旧差异条目（不清除按钮容器）"""
        while self.diff_layout.count():
            item = self.diff_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def show_suggestion(self, original, corrected, anchor_point=None, config=None, start=None, end=None):
        self.original_text = original
        self.corrected_text = corrected
        self.start_pos = start if start is not None else 0
        self.end_pos = end if end is not None else 0
        self.clear_diff()

        if not corrected:
            frags = []
        else:
            frags = diff_fragments(original, corrected)   # 请确保此函数已定义

        has_diff = bool(frags) and (original.strip() != corrected.strip())

        if has_diff:
            for frag in frags:
                ostart, oend = frag['ostart'], frag['oend']
                sstart, send = frag['sstart'], frag['send']
                tag = frag['type']
                ctx_pre = 2
                ctx_post = 2

                def escape_vis(s):
                    return s.replace(' ', '␣').replace('\t', '→   ').replace('\n', '↵')

                pre_o = escape_vis(original[max(0, ostart - ctx_pre):ostart])
                post_o = escape_vis(original[oend:min(oend + ctx_post, len(original))])
                pre_s = escape_vis(corrected[max(0, sstart - ctx_pre):sstart])
                post_s = escape_vis(corrected[send:min(send + ctx_post, len(corrected))])

                # ---------- 原文列（左侧） ----------
                if tag == 'insert':
                    left_display = (
                        f"<span style='color:#888; font-family:Consolas,monospace;'>{pre_o}</span>"
                        f"<span style='color:#d32f2f; background:#ffcdd2; font-weight:bold; padding:1px 4px; border-radius:4px;'>　插入　</span>"
                        f"<span style='color:#888; font-family:Consolas,monospace;'>{post_o}</span>"
                    )
                else:
                    org_frag = escape_vis(original[ostart:oend])
                    left_display = (
                        f"<span style='color:#888; font-family:Consolas,monospace;'>{pre_o}</span>"
                        f"<span style='color:#c62828; font-weight:bold; text-decoration:line-through; background:#ffebee; padding:0 2px; border-radius:3px; font-family:Consolas,monospace;'>{org_frag}</span>"
                        f"<span style='color:#888; font-family:Consolas,monospace;'>{post_o}</span>"
                    )

                # ---------- 建议列（右侧） ----------
                if tag == 'delete':
                    right_display = (
                        f"<span style='color:#888; font-family:Consolas,monospace;'>{pre_s}</span>"
                        f"<span style='color:#2e7d32; background:#c8e6c9; font-weight:bold; padding:1px 4px; border-radius:4px;'>　删除　</span>"
                        f"<span style='color:#888; font-family:Consolas,monospace;'>{post_s}</span>"
                    )
                else:
                    sug_frag = escape_vis(corrected[sstart:send])
                    right_display = (
                        f"<span style='color:#888; font-family:Consolas,monospace;'>{pre_s}</span>"
                        f"<span style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:0 2px; border-radius:3px; font-family:Consolas,monospace;'>{sug_frag}</span>"
                        f"<span style='color:#888; font-family:Consolas,monospace;'>{post_s}</span>"
                    )

                # ---------- 构建一行（三列网格） ----------
                row_widget = QWidget()
                row_widget.setObjectName("diff_row")
                grid = QGridLayout(row_widget)
                grid.setContentsMargins(8, 4, 8, 4)
                grid.setHorizontalSpacing(10)
                grid.setVerticalSpacing(0)

                lbl_left = QLabel(left_display)
                lbl_left.setTextFormat(Qt.RichText)
                lbl_left.setWordWrap(False)               # 禁止换行
                lbl_left.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
                lbl_left.setMinimumWidth(0)               # 宽度完全由内容决定

                arrow = QLabel("→")
                arrow.setStyleSheet("color: #999; font-weight: bold; font-size: 15px; border: none;")
                arrow.setAlignment(Qt.AlignCenter)
                arrow.setFixedWidth(24)

                lbl_right = QLabel(right_display)
                lbl_right.setTextFormat(Qt.RichText)
                lbl_right.setWordWrap(False)              # 禁止换行
                lbl_right.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
                lbl_right.setMinimumWidth(0)

                grid.addWidget(lbl_left, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
                grid.addWidget(arrow, 0, 1, Qt.AlignCenter)
                grid.addWidget(lbl_right, 0, 2, Qt.AlignLeft | Qt.AlignVCenter)

                self.diff_layout.addWidget(row_widget)

            self.btn_container.setVisible(True)
        else:
            no_error = QLabel("✔ 完美，继续加油")
            no_error.setStyleSheet("color: #2e7d32; font-size: 15px; font-weight: bold; border: none;")
            no_error.setAlignment(Qt.AlignCenter)
            no_error.setWordWrap(False)
            self.diff_layout.addWidget(no_error)
            self.btn_container.setVisible(False)

        # ---- 应用 config（完全兼容原有参数） ----
        if config:
            bg = config.get("suggestion_bg_color", "#eef3ff")
            fg = config.get("suggestion_font_color", "#000000")
            opacity = config.get("suggestion_opacity", 0.9)

            # 仅改变文字颜色和按钮颜色，容器保持透明，行背景用自定义色
            self.container.setStyleSheet(f"""
                #suggestion_container {{
                    background: transparent;
                    border: none;
                }}
                #diff_row {{
                    background-color: {bg};
                    border-radius: 8px;
                    padding: 6px 8px;
                }}
                QLabel {{
                    color: {fg};
                    background: transparent;
                    border: none;
                }}
                QPushButton {{
                    background-color: #eef3ff;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    padding: 4px 12px;
                    color: #333;
                }}
            """)
            self.setWindowOpacity(opacity)
        else:
            self._apply_default_style()   # 恢复默认样式

        # 重新确定大小
        self.adjustSize()

        # 定位：底部中心点固定在锚点上方 8px 处（窗口向上生长）
        if anchor_point:
            x = anchor_point.x() - self.width() // 2 - 20
            y = anchor_point.y() - self.height() - 15

            # 防止超出屏幕边界
            screen = QApplication.primaryScreen().availableGeometry()
            if y < screen.top():
                y = screen.top()
            # if x < screen.left():
                # x = screen.left()
            # elif x + self.width() > screen.right():
                # x = screen.right() - self.width()

            self.move(x, y)

        self.show()
        self.raise_()

class FloatingButton(QToolButton):
    suggestion_accepted = Signal(str, int, int)   # 原有信号    --修改18
    def __init__(self, config, api=None, parent=None, ignore_mgr=None, main_window=None):        # 新增 ignore_mgr     api=None, --修改17  main_window=None --修改18
        super().__init__(parent)
        self.config = config
        self.api = api                    # 新增
        self.ignore_mgr = ignore_mgr          # 存储      --修改17
        self.main_window = main_window          # 新增这一行     --修改18         
        self._shift_held = False   # 记录鼠标按下时 Shift 是否被按住       --修改19
        self.current_sentence_range = None
        self.current_start = -1
        self.current_end = -1
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        
        # self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TranslucentBackground, True)   # 允许透明背景     # --修改22
        self.setAttribute(Qt.WA_NoSystemBackground, True)       # --修改22
        self.setAutoFillBackground(False)       # --修改22
        
        self.current_original = ""
        self.current_corrected = ""
        self.dragPos = None
        self.main_window = main_window   # 保存引用     --修改18

        # 创建建议卡片（唯一实例）
        self.suggestion_card = SuggestionCard()
        self.suggestion_card.accepted.connect(self.on_accepted)
        # self.suggestion_card.rejected.connect(self.on_rejected)       #   --修改17
        self.suggestion_card.ignored.connect(self.on_ignored)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 创建按钮，仅用图标
        self.btn = QPushButton()
        icon_size = QSize(32, 32)   #   --修改22  图标显示大小，可按需调整
        button_size = QSize(32, 32)     #   --修改22  按钮显示大小，可按需调整
        self.btn.setFixedSize(button_size)                   # 按钮尺寸，建议与图标大小一致
        self.btn.setIconSize(icon_size)            #   --修改22
        icon_pixmap = QPixmap(resource_path("assets/icon_float.png"))          #   --修改21
        icon_pixmap = icon_pixmap.scaled(icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)       #   --修改22
        self.btn.setIcon(QIcon(icon_pixmap))        #   --修改21

        # ===================================

        self.btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border-radius: 16px;        /* 圆角半径 */
                border: none;
                outline: none;               /* 去除焦点虚线框 */
            }
            QPushButton:hover {
                background-color: rgba(76, 175, 80, 0.2);
                border-radius: 16px;
            }
            QPushButton:focus {
            outline: none;               /* 确保聚焦时也不显示虚线 */
            }
        """)
        self.setStyleSheet("background: transparent;")      #  --修改19 透明图标
        self.btn.setToolTip("智能校对, ctrl+点击切换显示/隐藏")           #  悬浮图标的提示语
        layout.addWidget(self.btn)

#--修改16
        self.btn.clicked.connect(self._query_and_show_cached)   # 替换原来的 toggle_suggestion      
        
        self.btn.installEventFilter(self)

        # 默认显示，根据配置决定
        self.show()
        if not self.config.get("show_floating_button", True):
            self.hide()

    def eventFilter(self, watched, event):
        if watched == self.btn:
            # ---- 悬停光晕（每次动态创建，安全无副作用）----      #-- 修改22
            # if event.type() == QEvent.Enter:
                # shadow = QGraphicsDropShadowEffect()
                # shadow.setBlurRadius(80)                # 光晕扩散半径
                # shadow.setOffset(0, 0)                  # 居中
                # shadow.setColor(QColor(0, 120, 255, 200))  # 蓝色半透明
                # self.btn.setGraphicsEffect(shadow)
                # return True
            # elif event.type() == QEvent.Leave:
                # self.btn.setGraphicsEffect(None)        # 移除效果，按钮会自动删除它
                # return True
            # ---- 原有拖拽逻辑（保持不变）----
            if event.type() == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    self.dragPos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    return False
            elif event.type() == QEvent.MouseMove:
                if event.buttons() == Qt.LeftButton and self.dragPos is not None:
                    self.move(event.globalPosition().toPoint() - self.dragPos)
                    return True
        return super().eventFilter(watched, event)            

    def moveEvent(self, event):                 #--修改17 悬浮按钮移动时卡片跟随
            super().moveEvent(event)
            if self.suggestion_card.isVisible():
                anchor = self.mapToGlobal(QPoint(self.width(), 0))
                self.suggestion_card.move(anchor.x() - self.suggestion_card.width() / 2 - 20,
                                          anchor.y() - self.suggestion_card.height() - 15)

#  --修改20
    def _query_and_show_cached(self):
        """点击悬浮按钮仅切换建议卡片的显示/隐藏，不触发校对 """
        if QApplication.keyboardModifiers() != Qt.ControlModifier:      #  ctrl + 点击切换显示隐藏  2026.05.14
            return
        if self.suggestion_card.isVisible():
            self.suggestion_card.hide()
        else:
            # if self.current_original:     #  上一次没有建议则打不开
                # self.show_card()
            self.show_card()        
#  --修改20


                                                                                                                        
    def show_card(self):        #--修改17
        # 计算锚点：优先使用悬浮按钮的位置（如果有有效尺寸），否则使用当前鼠标光标位置
        if self.isVisible() and self.width() > 0:
            anchor = self.mapToGlobal(QPoint(self.width(), 0))
        else:
            anchor = QCursor.pos()
        self.suggestion_card.show_suggestion(
            self.current_original, self.current_corrected,
            anchor, self.config,
            start=self.current_start, end=self.current_end
        )       
        
    def set_suggestion(self, original, corrected, start=-1, end=-1):        #--修改12
        if not original or not corrected or original.strip() == corrected.strip():
            # self.suggestion_card.hide()           #  2026.05.13  没有错误，自动隐藏建议窗口
            self.current_original = ""
            self.current_corrected = ""
            self.current_start = -1
            self.current_end = -1
            if self.suggestion_card.isVisible():    #  2026.05.13  没有错误，如果窗口是打开的，则显示没有错误
                self.suggestion_card.hide()     #    先隐藏，再显示，避免受到之前建议窗口的位置影响
                self.show_card()                
            return
        self.current_original = original
        self.current_corrected = corrected
        self.current_start = start
        self.current_end = end
        if self.config.get("auto_show_suggestion", True):
            self.suggestion_card.hide()     #    先隐藏，再显示，避免受到之前建议窗口的位置影响
            self.show_card()
        else:
            self.show()
            
    def on_accepted(self):
        """用户点击‘替换’按钮"""
        if not self.current_corrected or self.current_start is None:
            return
        # 直接调用主窗口的替换方法
        if self.main_window:
            self.main_window.apply_correction(self.current_corrected,
                                              start=self.current_start,
                                              end=self.current_end)
        # self._clear_state()             #    清空内容
        self.suggestion_card.hide()     #    先隐藏，再显示，避免受到之前建议窗口的位置影响
        self.show_card()
                
# 执行替换操作（回调由外部设置）       --修改18
        # if hasattr(self, '_accepted_callback') and self._accepted_callback:
            # self._accepted_callback(text, start=start, end=end, original_text=original)
        # self.hide_card()
        # self._clear_state()

    def _clear_state(self):
        self.current_original = ""
        self.current_corrected = ""
        self.current_start = -1
        self.current_end = -1
            
 
    def on_ignored(self, text, start, end, original):   #--修改17 清除word标注
        # 将原句加入忽略列表
        if self.ignore_mgr:
            self.ignore_mgr.add_ignore(original)
        # 清除 Word 中当前句子的错误标记
        if start >= 0 and end > start:
            try:
                clear_error_marks(start, end)
            except Exception:
                pass  # 忽略可能的 COM 错误（如 Word 已关闭）
        # self._clear_state()         #    清空内容
        self.suggestion_card.hide()     #    先隐藏，再显示，避免受到之前建议窗口的位置影响
        self.show_card()
               
    # def hide_card(self):
        # self.suggestion_card.hide()

