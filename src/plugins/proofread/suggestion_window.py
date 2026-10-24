# suggestion_window.py
import difflib

from PySide6.QtCore import Qt, Signal, QPoint, QEvent, QSize
from PySide6.QtGui import QColor, QPalette, QIcon, QCursor, QPixmap, QFont  # 悬浮按钮位置--修改17   QPixmap--修改21
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QApplication, QToolButton, \
    QGridLayout, QSizePolicy, QFrame, QGraphicsDropShadowEffect

from infra.utils import resource_path  # 或直接定义在文件内   #  -修改21
from plugins.proofread.word_monitor import clear_error_marks  # --修改17  --修改19 proof_sentence

PLUGIN_ID = "proofread"


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

    def __init__(self, parent=None, theme=None, i18n=None, embedded=False):
        """embedded=True 时作为桌宠窗口内的子部件，不再自建顶层窗口。"""
        super().__init__(parent)
        self._theme = theme
        self._i18n = i18n
        self._embedded = embedded
        if not embedded:
            # 无边框 + 置顶 + 工具窗口
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setAttribute(Qt.WA_TranslucentBackground, True)   # 背景透明

        self.original_text = ""
        self.corrected_text = ""
        self.start_pos = 0
        self.end_pos = 0
        self._perfect_label = None      # 「无错误」标签，供 retranslateUi 刷新

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---- 内部容器：整块就是一张卡片（边框/圆角/底色由主题 #suggestionCard 提供）----
        self.container = QWidget()
        self.container.setObjectName("suggestionPill")
        main_layout.addWidget(self.container)

        # 容器内部垂直布局
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(10, 8, 10, 8)
        container_layout.setSpacing(8)

        # 差异片段显示区域
        self.diff_widget = QWidget()
        self.diff_layout = QVBoxLayout(self.diff_widget)
        self.diff_layout.setContentsMargins(0, 0, 0, 0)
        self.diff_layout.setSpacing(2)
        container_layout.addWidget(self.diff_widget)

        # 按钮行（确认 / 忽略）：右对齐、小尺寸，别占半屏
        self.btn_container = QWidget()
        btn_layout = QHBoxLayout(self.btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)
        btn_layout.addStretch(1)
        self.accept_btn = QPushButton(self.tr("bubble.accept"))
        self.ignore_btn = QPushButton(self.tr("bubble.ignore"))
        self.accept_btn.setProperty("accent", True)
        for button in (self.accept_btn, self.ignore_btn):
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(26)
            button.setMinimumWidth(58)
            button.setMaximumWidth(96)      # 字体缺失/长文案时也不要撑满整行
            # 固定宽度，否则按钮会把整行撑满（QPushButton 默认会随布局扩展）
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
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

        # 样式统一由主题提供（base.qss 的 #diff_row 等选择器），
        # 这里不再写内联 setStyleSheet，避免 widget 级样式盖掉应用级主题

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        """带插件命名空间的查词；i18n 不可用时原样返回 key。"""
        if self._i18n is None:
            return key
        return self._i18n.tr(f"{PLUGIN_ID}.{key}", **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.accept_btn.setText(self.tr("bubble.accept"))
        self.ignore_btn.setText(self.tr("bubble.ignore"))
        if self._perfect_label is not None:
            self._perfect_label.setText(self.tr("bubble.perfect"))

    def clear_diff(self):
        """清空旧差异条目（不清除按钮容器）"""
        while self.diff_layout.count():
            item = self.diff_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._perfect_label = None

    def show_suggestion(self, original, corrected, anchor_point=None, config=None, start=None, end=None):
        self.original_text = original
        self.corrected_text = corrected
        self.start_pos = start if start is not None else 0
        self.end_pos = end if end is not None else 0
        self.clear_diff()

        # diff 高亮走 HTML 富文本，QSS 管不到，颜色取自主题调色板
        palette = self._theme.diff_palette() if self._theme is not None else {}
        del_bg = palette.get("del_bg", "#ffebee")
        del_text = palette.get("del_text", "#c62828")
        ins_bg = palette.get("ins_bg", "#e8f5e9")
        ins_text = palette.get("ins_text", "#2e7d32")
        muted = self._theme.token("color.text.secondary", "#888888") if self._theme is not None else "#888888"

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

                # ---------- 原文列（左侧）：只展示被删除片段（删除线），不再塞"插入/删除"标签 ----------
                mono = "font-family:Consolas,monospace;"
                if tag == 'insert':
                    left_display = f"<span style='color:{muted}; {mono}'>{pre_o}{post_o}</span>"
                else:
                    org_frag = escape_vis(original[ostart:oend])
                    left_display = (
                        f"<span style='color:{muted}; {mono}'>{pre_o}</span>"
                        f"<span style='color:{del_text}; text-decoration:line-through; background:{del_bg}; padding:0 2px; border-radius:3px; {mono}'>{org_frag}</span>"
                        f"<span style='color:{muted}; {mono}'>{post_o}</span>"
                    )

                # ---------- 建议列（右侧）：只展示被插入片段（浅色高亮），保持单行可读 ----------
                if tag == 'delete':
                    right_display = f"<span style='color:{muted}; {mono}'>{pre_s}{post_s}</span>"
                else:
                    sug_frag = escape_vis(corrected[sstart:send])
                    right_display = (
                        f"<span style='color:{muted}; {mono}'>{pre_s}</span>"
                        f"<span style='color:{ins_text}; background:{ins_bg}; padding:0 2px; border-radius:3px; {mono}'>{sug_frag}</span>"
                        f"<span style='color:{muted}; {mono}'>{post_s}</span>"
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
                arrow.setObjectName("diffArrow")   # 样式见主题 base.qss
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
            no_error = QLabel(self.tr("bubble.perfect"))
            no_error.setObjectName("stateOk")     # 绿色由主题令牌提供
            no_error.setAlignment(Qt.AlignCenter)
            no_error.setWordWrap(False)
            self._perfect_label = no_error
            self.diff_layout.addWidget(no_error)
            self.btn_container.setVisible(False)

        # ---- 仅保留与主题无关的动态项：窗口透明度 ----
        # 颜色一律跟随主题（原 suggestion_bg_color / font_color 已由主题令牌取代）
        if config:
            self.setWindowOpacity(config.get("suggestion_opacity", 0.95))

        # 重新确定大小
        self.adjustSize()

        # 嵌入桌宠窗口时由父窗口负责定位与显隐
        if self._embedded:
            self.setVisible(True)
            return

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
    def __init__(self, config, api=None, parent=None, ignore_mgr=None, correction_handler=None,
                 theme=None, i18n=None):
        """悬浮按钮 + 建议卡片（属 proofread 插件，随插件启停）。

        correction_handler(corrected_text, start, end) 由插件注入，
        避免 UI 反向依赖主窗口。
        """
        super().__init__(parent)
        self.config = config
        self._theme = theme
        self._i18n = i18n
        self.api = api                    # 新增
        self.ignore_mgr = ignore_mgr          # 存储      --修改17
        self._correction_handler = correction_handler
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

        # 创建建议卡片（唯一实例）
        self.suggestion_card = SuggestionCard(theme=self._theme, i18n=self._i18n)
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

        # 悬浮图标样式交给主题（base.qss 的 #petButton），
        # 这里只标记身份，避免 widget 级样式覆盖主题
        self.btn.setObjectName("petButton")
        self.setObjectName("petWindow")
        self.btn.setToolTip(self.tr("pet.tooltip"))           #  悬浮图标的提示语
        layout.addWidget(self.btn)

#--修改16
        self.btn.clicked.connect(self._query_and_show_cached)   # 替换原来的 toggle_suggestion      
        
        self.btn.installEventFilter(self)

        # 默认显示，根据配置决定
        self.show()
        if not self.config.get("show_floating_button", True):
            self.hide()

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        """带插件命名空间的查词；i18n 不可用时原样返回 key。"""
        if self._i18n is None:
            return key
        return self._i18n.tr(f"{PLUGIN_ID}.{key}", **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        self.btn.setToolTip(self.tr("pet.tooltip"))
        self.suggestion_card.retranslateUi()

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
        # 调用插件注入的替换实现
        if self._correction_handler:
            self._correction_handler(self.current_corrected,
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

