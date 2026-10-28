# word_navigator_dialog.py
"""题录导航面板（主界面内容页）。

P10 后续：本文件原先是独立浮窗 `QDialog`（Layer.PALETTE + 置顶 + 几何记忆），
与其他插件（智能纠错/翻译改写/图片工具）都注册成主界面导航页的做法不一致——
用户看到的是"浮在主界面上的另一块窗口"，既没有导航轨条目也不跟主窗口布局。
现在改成 `QWidget` 面板，由插件用 `ctx.ui.add_page()` 注册；窗口专属件
（标题栏、置顶、关于、字体、几何记忆）随之下线。
"""
import json
from pathlib import Path

import pythoncom
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTabWidget, QListWidget, QLineEdit, QProgressBar, QMessageBox
)

from core.services.caption_cache import CaptionCache
from core.services.config_manager import CONFIG_DIR
from core.services.word_bridge import get_word_app
from core.services.word_constants import wdConst
from plugins.navigator.caption_item import CaptionItem  # noqa: F401  兼容旧引用
from plugins.navigator.navigator_jump import JumpMixin
from plugins.navigator.navigator_scan import ScanMixin

CONFIG_FILE = CONFIG_DIR / "navigator_config.json"
wdFieldSequence = wdConst.wdFieldSequence               # Word 序列域类型常量


class WordNavigatorPanel(ScanMixin, JumpMixin, QWidget):
    """题录导航内容页：扫标题/图表题注、筛选、双击跳转、返回上一位置。"""

    def __init__(self, parent=None, config=None, window_manager=None, i18n=None,
                 plugin_id="navigator"):
        super().__init__(parent)
        self.app_config = config                    # 内核 ConfigManager（原 navigator_config.json 收敛到这里）
        self.window_manager = window_manager        # 仅为兼容旧调用签名保留，页面形态不再用它
        self.i18n = i18n
        self._ns = plugin_id
        # ---------- 安全初始化 COM ----------
        self._com_ok = False
        try:
            pythoncom.CoInitialize()
            self._com_ok = True
        except Exception:
            pass  # 即使 COM 初始化失败，面板仍然可以显示，只是无法操作 Word

        # ---------- 预定义所有可能在异常路径中访问的属性 ----------
        self._scan_silent = False
        self._captions_cache_data = []       # 防止后续 AttributeError
        self._new_table = []
        self._new_figure = []

        self.setMinimumWidth(360)

        self.config = self.load_config()

        self.word = None
        self.doc = None
        self.table_captions = []
        self.figure_captions = []
        self.history_stack = []
        self.max_history = 10

        self.cache = CaptionCache()
        self._scanning = False
        self._scan_fingerprint = None
        # 关闭标志：closeEvent 先置 True，所有排队中的 QTimer 回调据此立即返回
        # （历史实现在 CoUninitialize 之后仍会跑批处理，在已释放的 COM 公寓上访问 Word）
        self._closing = False
        self._chapters: list[str] = []      # 章节原始标题（离线扫描填充，章节下拉按语言现拼）
        self._status_parts: list[tuple[str, dict]] = []   # 状态栏词条 key（切语言时重译）

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
    # ---------- 多语言 ----------
    def tr(self, key: str, /, **params) -> str:
        """带插件命名空间的查词；i18n 不可用时原样返回 key。"""
        if self.i18n is None:
            return key
        return self.i18n.tr(f"{self._ns}.{key}", **params)

    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """切换语言后刷新常驻界面的文案。

        覆盖常驻控件：按钮文字与工具提示、筛选标签与占位符、章节下拉项
        （「全部章节」+ 各章名按存下来的章节标题重建）、两个标签页、
        题录列表里的页码后缀，以及状态栏（按存下来的 key 重译）。
        """
        self.btn_refresh.setText(self.tr("btn.refresh"))
        self.btn_refresh.setToolTip(self.tr("btn.refresh.tip"))
        self.btn_return.setText(self.tr("btn.return"))
        self.search_label.setText(self.tr("search.label"))
        self.search_edit.setPlaceholderText(self.tr("search.placeholder"))
        self.tab_widget.setTabText(0, self.tr("tab.table"))
        self.tab_widget.setTabText(1, self.tr("tab.figure"))
        self._populate_chapters()       # 下拉项必须重建（存 key/标题，不存成品文案）
        self._retranslate_captions()    # 题录的「[第N页]」后缀
        self.filter_lists()             # 用新文案重画两份列表
        self._render_status()

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

    def apply_config(self):
        family = self.config.get("font_family", "Microsoft YaHei")
        size = self.config.get("font_size", 12)
        self.current_font = QFont(family, size)
        self.setFont(self.current_font)

    # ---------- UI ----------
    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 一行放「刷新列表」与「返回上一位置」：原来各占一行，竖向空间浪费
        toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton(self.tr("btn.refresh"))
        self.btn_refresh.setToolTip(self.tr("btn.refresh.tip"))
        # 手动刷新 = 全量扫描（走 COM，会更新页码）；平时的自动刷新走离线解析、不动页码
        self.btn_refresh.clicked.connect(lambda: self.load_captions(force_com=True))
        toolbar.addWidget(self.btn_refresh)

        self.btn_return = QPushButton(self.tr("btn.return"))
        # 样式交给主题（base.qss 里的 QPushButton#navReturnBtn）
        self.btn_return.setObjectName("navReturnBtn")
        self.btn_return.clicked.connect(self.go_back)
        toolbar.addWidget(self.btn_return, 1)
        layout.addLayout(toolbar)

        search_layout = QHBoxLayout()
        self.search_label = QLabel(self.tr("search.label"))   # 存下来，切语言时才能重译
        search_layout.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("search.placeholder"))
        self.search_edit.textChanged.connect(self.filter_lists)
        search_layout.addWidget(self.search_edit, 1)
        # 章节过滤：离线扫描后按一级章节（模板最小标题层级）填充
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(150)
        self.chapter_combo.addItem(self.tr("chapter.all"), -1)
        self.chapter_combo.setEnabled(False)
        self.chapter_combo.currentIndexChanged.connect(self.filter_lists)
        # 点「全部章节」时索引常常没变（本来就是 0），只有 activated 才会发出 ⇒ 两个信号都接
        self.chapter_combo.activated.connect(self.filter_lists)
        search_layout.addWidget(self.chapter_combo)
        layout.addLayout(search_layout)

        self.tab_widget = QTabWidget()
        self.tab_widget.currentChanged.connect(self._on_tab_changed)  # 记录标签页切换
        self.table_list = QListWidget()
        self.figure_list = QListWidget()
        # 标签页文案里的 emoji 在不同字体下辨识度差，改用矢量图标（表格/折线）
        self.tab_widget.addTab(self.table_list, self.tr("tab.table"))
        self.tab_widget.addTab(self.figure_list, self.tr("tab.figure"))
        self._apply_tab_icons()
        layout.addWidget(self.tab_widget)

        self.table_list.itemDoubleClicked.connect(self.on_table_double_clicked)
        self.figure_list.itemDoubleClicked.connect(self.on_figure_double_clicked)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setObjectName("hintLabel")   # 样式见 base.qss
        layout.addWidget(self.status_label)
        self._set_status("status.ready")

    def _on_tab_changed(self, index):
        self.last_tab_index = index

    def _apply_tab_icons(self) -> None:
        """给两个标签页换成矢量图标（表格 / 折线），颜色跟随当前文字色。

        走 `infra.utils.resource_path` + `QtSvg`，不依赖 app 层（插件只允许依赖 core/infra）。
        """
        color = self.palette().color(self.foregroundRole()).name()
        self.tab_widget.setTabIcon(0, self._svg_icon("table", color))
        self.tab_widget.setTabIcon(1, self._svg_icon("chart", color))

    @staticmethod
    def _svg_icon(name: str, color: str) -> QIcon:
        from PySide6.QtCore import QSize, QByteArray
        from PySide6.QtSvg import QSvgRenderer

        from infra.utils import resource_path

        try:
            path = resource_path(f"assets/icons/{name}.svg")
            data = Path(path).read_text(encoding="utf-8").replace("$COLOR", color)
            renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
            pixmap = QPixmap(QSize(16, 16))
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return QIcon(pixmap)
        except Exception:  # noqa: BLE001  图标缺失不影响功能
            return QIcon()

    # ---------- Word 连接与保存监测 ----------
    def _get_word_document(self):
        if not self._com_ok:
            self._set_status("com_error")
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
        """Word 保存后自动刷新：只走离线解析（快、不动页码、不占 COM）。"""
        if self._closing:
            return
        if self.isVisible() and not self._scanning:
            self._refresh_captions(force_com=False, silent=True)

    # ---------- 刷新入口 ----------
    def load_captions(self, force_com: bool = False):
        """刷新题录。

        - 默认（打开软件时的首次扫描、Word 保存后的自动刷新）：**离线解析**，
          只更新章节与题录文本，不碰页码 ⇒ 不再每次启动都触发全量 COM 扫描；
        - `force_com=True`（工具栏「刷新列表」）：走 COM 全量扫描并刷新页码。
        """
        self._refresh_captions(force_com=force_com, silent=False)


    # ---------- 过滤列表（不再自动切换标签页）----------

    def shutdown(self) -> None:
        """释放现场：**先置关闭标志**，再停监测/恢复 ScreenUpdating/关缓存/释放 COM/引用。

        由插件在 `deactivate()` 里调用（页面形态下不再有 closeEvent）。
        置标志必须最先做：排队中的 `QTimer.singleShot` 批处理回调据此立刻返回，
        否则会在 `CoUninitialize` 之后仍去访问 Word（已释放的 COM 公寓）。
        """
        self._closing = True
        try:
            self._stop_save_monitor()
        except Exception:
            pass
        try:
            self._restore_screen_updating()
        except Exception:
            pass
        try:
            self.cache.close()
        except Exception:
            pass
        self.doc = None
        self.word = None
        if self._com_ok:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
