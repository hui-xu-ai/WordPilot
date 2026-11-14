# settings_page.py
"""设置页（主窗口内置页面，取代原先的模态对话框）。

形态：左侧子导航（API / 提示词 / 界面 / 外观）+ 右侧内容堆叠，
底部一条「保存」栏——点保存立即写配置并发 saved 信号，
后续动作（重建 AI 客户端、热键重绑、语言/皮肤热切换）由主窗口统一处理。

- 所有文案走 i18n（构造时可传入 i18n 服务）；
- 「界面语言」与「提示词语言」是两个独立配置：
  前者只影响 UI，后者决定送 AI 的提示词语种，互不影响。
"""
import logging
import os
import subprocess
import sys
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QApplication, QPushButton, QLabel, QTextEdit, QSpinBox, QStackedWidget, QWidget,
    QMessageBox, QSlider, QGroupBox, QCheckBox, QComboBox, QDoubleSpinBox,
    QListWidget, QScrollArea,
)

from core.services.api_client import DeepSeekClient
from core.services.skin_service import DEFAULT_SKIN_ID
from core.services.theme_service import USER_QSS
from core.services.update_service import MANUAL_COOLDOWN_SECONDS

logger = logging.getLogger(__name__)

#: 界面语言与提示词语言共用的可选语种
PROMPT_LANGUAGE_NAMES = {
    "zh_CN": "中文",
    "en_US": "English",
}

_NAV_KEYS = ("settings.tab.api", "settings.tab.appearance", "settings.tab.monitor",
             "settings.tab.storage",
             "settings.tab.about")

#: 即改即存的防抖窗口（毫秒）：连续打字只写一次盘，又不能让用户觉得"没保存"
SAVE_DEBOUNCE_MS = 500
#: 底部"已保存"提示停留时长（毫秒）
SAVED_TOAST_MS = 2000


def _qt_version() -> str:
    """PySide6/Qt 版本（关于页展示用；导入失败时退化为未知）。"""
    try:
        from PySide6 import __version__ as pyside_version

        return pyside_version
    except Exception:  # noqa: BLE001  展示信息，取不到就写未知
        return "?"


class _ConnectionTester(QThread):
    """在后台线程里测 API 连通性。

    历史实现在 GUI 线程同步 ``requests.post(timeout=30)``：点一次「测试连接」
    最长冻结界面 30 秒，Windows 会把窗口标成「未响应」，且"测试中"提示根本来不及重绘。
    """

    done = Signal(bool, str)      # (是否成功, 失败原因)

    def __init__(self, api_key: str, model: str, endpoint: str = "", parent=None) -> None:
        # U03：不要以 SettingsPage 为父对象。页面销毁会让父对象连带析构运行中的
        # QThread，进程直接 0xC0000409 崩溃（审查用 2 秒延迟连接复现）。
        super().__init__(None)
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint or "https://api.deepseek.com"

    def run(self) -> None:        # noqa: D401 - QThread 入口
        try:
            client = DeepSeekClient({"api_key": self._api_key, "model": self._model,
                                     "api_endpoint": self._endpoint})
            ok = bool(client.test_connection())
            reason = "" if ok else (getattr(client, "last_error", "") or "")
        except Exception as exc:  # noqa: BLE001  后台线程里任何异常都必须回传，不能吞
            ok, reason = False, str(exc)
        self.done.emit(ok, reason)


class _UpdateChecker(QThread):
    """在后台线程里查 GitHub Releases（网络请求绝不放 GUI 线程，同 `_ConnectionTester`）。"""

    done = Signal(object, str, str)      # (UpdateInfo | None, 错误类型, 错误详情)

    def __init__(self) -> None:
        # 同 `_ConnectionTester`：不要以页面为父对象，页面析构会连带杀掉运行中的线程
        super().__init__(None)

    def run(self) -> None:        # noqa: D401 - QThread 入口
        from __version__ import __version__

        from core.services import update_service

        try:
            info = update_service.check_for_update(__version__)
        except update_service.UpdateCheckError as err:
            self.done.emit(None, err.kind, err.detail)
        except Exception as exc:          # noqa: BLE001  兜底：异常绝不能逃出线程
            self.done.emit(None, "network", str(exc))
        else:
            self.done.emit(info, "", "")


class SettingsPage(QWidget):
    saved = Signal()
    #: 外观类配置（界面语言/皮肤/主题）发生了变化：外壳据此切语言/皮肤、重刷样式
    appearanceSaved = Signal()

    def __init__(self, config, parent=None, theme=None, i18n=None, skins=None):
        super().__init__(parent)
        self.config = config
        self.theme = theme
        self.i18n = i18n
        self.skins = skins
        self._tester: _ConnectionTester | None = None
        # 检查更新（v1：只提示 + 给下载入口，不自动替换安装）
        self._update_checker: _UpdateChecker | None = None
        self._update_info = None
        self._last_manual_check = 0.0
        # 即改即存：文本/数值改动防抖 500ms 后落盘；load_settings 期间不触发
        self._loading = False
        self._last_payload: dict | None = None
        self._last_appearance: tuple | None = None
        self._toast_token: object | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.save_settings)

        self.init_ui()
        self.load_settings()
        self.retranslateUi()
        # 退出前必须把后台检查线程收掉：QThread 还在跑时解释器析构会直接崩（0xC0000409）
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_update_checker)

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        if self.i18n is None:
            return key
        return self.i18n.tr(key, **params)

    def prompt_tr(self, key: str) -> str:
        """默认提示词按提示词语言取，与界面语言解耦。"""
        if self.i18n is None:
            return key
        return self.i18n.prompt_tr(key)

    # ---------------- 界面 ----------------
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 16)
        main_layout.setSpacing(14)

        # 不放页头标题：左侧子导航与侧边栏已经说明当前位置
        body = QHBoxLayout()
        body.setSpacing(16)

        # 左侧子导航
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("settingsNav")
        self.nav_list.setFixedWidth(132)
        for _ in _NAV_KEYS:
            self.nav_list.addItem("")
        self.nav_list.currentRowChanged.connect(self._switch_section)
        body.addWidget(self.nav_list)

        # 右侧内容（可滚动，小窗口也不挤爆）
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_api_section())
        self.stack.addWidget(self._build_appearance_section())
        self.stack.addWidget(self._build_monitor_section())
        self.stack.addWidget(self._build_storage_section())
        self.stack.addWidget(self._build_about_section())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.stack)
        body.addWidget(scroll, 1)
        main_layout.addLayout(body, 1)

        self.nav_list.setCurrentRow(0)
        self.destroyed.connect(self._stop_tester)
        self._bind_autosave()

        # 底部状态栏：只放"已保存/保存失败"提示（保存按钮已移除，改为即改即存）
        bottom = QHBoxLayout()
        self.save_status = QLabel("")
        self.save_status.setObjectName("hintLabel")
        self.save_status.setWordWrap(True)
        bottom.addWidget(self.save_status, 1)
        main_layout.addLayout(bottom)

    def _switch_section(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    # ---------------- 即改即存 ----------------
    def _bind_autosave(self) -> None:
        """把可编辑控件接到自动保存上。

        文本/数值用防抖（打字过程中不写盘），下拉框立即落盘并即时生效：
        主题、界面语言以前只有"点保存"才生效，正是"改了没变"的来源。
        """
        for edit in (self.model_edit, self.endpoint_edit, self.api_key_edit):
            edit.textChanged.connect(self._schedule_save)
        for spin in (self.temp_edit, self.max_tokens_edit):
            spin.valueChanged.connect(self._schedule_save)
        self.theme_combo.currentIndexChanged.connect(self._flush_save)
        self.ui_language_combo.currentIndexChanged.connect(self._flush_save)

    def _schedule_save(self, *_args) -> None:
        if self._loading:
            return
        self._save_timer.start()

    def _flush_save(self, *_args) -> None:
        """立即落盘（下拉框用；也便于测试直接调用）。"""
        self._save_timer.stop()
        if self._loading:
            return
        self.save_settings()

    # ---------------- 桌宠外观 ----------------
    def _on_toggle_api_key(self, shown: bool) -> None:
        """U07：允许用户主动查看，默认保持掩码。"""
        self.api_key_edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.api_key_toggle.setText(
            self.tr("settings.api.key_hide") if shown else self.tr("settings.api.key_show"))

    def _retranslate_skin_combo(self) -> None:
        """切语言后重建皮肤名（下拉存 id，显示文案随界面语言）。"""
        combo = getattr(self, "skin_combo", None)
        skins = getattr(self, "skins", None)
        if combo is None or skins is None:
            return
        from core.services.ui_labels import skin_label

        for index in range(combo.count()):
            skin_id = combo.itemData(index)
            combo.setItemText(index, skin_label(str(skin_id), combo.itemText(index), self.tr))

    def _on_pet_skin_changed(self, _index: int) -> None:
        """外观页切皮肤：同时更新内核状态、皮肤服务与宠物窗口。"""
        skin_id = self.skin_combo.currentData()
        if not skin_id:
            return
        try:
            from core.services.pet_host import PetHost
            from core.services.pet_service import PetService

            PetService.instance().set_skin(str(skin_id))
            if getattr(self, "skins", None) is not None:
                try:
                    self.skins.set_skin(str(skin_id))     # 让窗口内的皮肤服务也切过去
                except Exception:                       # noqa: BLE001
                    logger.warning("切换窗口皮肤服务失败", exc_info=True)
            host = PetHost.instance()
            host.apply_skin(str(skin_id))
            host.apply_size(PetService.instance().size)
        except Exception:                      # noqa: BLE001
            logger.warning("应用桌宠皮肤失败，皮肤可能未生效", exc_info=True)

    def _on_pet_size_changed(self, value: int) -> None:
        self.pet_size_value.setText(f"{value} px")
        try:
            from core.services.pet_host import PetHost
            from core.services.pet_service import PetService

            PetService.instance().set_size(int(value))
            host = PetHost.instance()
            host.apply_size(int(value))
            window = host.window()
            if window is not None:                 # 直接落到窗口，排除信号链问题
                window.set_pet_size(int(value))
        except Exception:                      # noqa: BLE001
            logger.warning("设置桌宠尺寸失败", exc_info=True)

    def _on_pet_visible_toggled(self, visible: bool) -> None:
        try:
            from core.services.pet_host import PetHost
            from core.services.pet_service import PetService

            PetService.instance().set_visible(bool(visible))
            PetHost.instance().apply_visibility(bool(visible))
        except Exception:                      # noqa: BLE001
            logger.warning("设置桌宠显示/隐藏失败", exc_info=True)
    def _card(self) -> tuple[QWidget, QVBoxLayout]:
        """一张通栏卡片（样式见 base.qss #contentCard）。"""
        card = QWidget()
        card.setObjectName("contentCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        return card, lay

    # ---------- 各分区 ----------
    def _build_api_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        card, form_host = self._card()
        form = QFormLayout()
        form.setSpacing(10)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)      # U07：默认掩码，避免明文暴露
        self.api_key_toggle = QPushButton()
        self.api_key_toggle.setCheckable(True)
        self.api_key_toggle.toggled.connect(self._on_toggle_api_key)
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.api_key_edit, 1)
        key_layout.addWidget(self.api_key_toggle)
        self.model_edit = QLineEdit()
        self.label_api_key = QLabel()
        self.label_model = QLabel()
        form.addRow(self.label_api_key, key_row)
        # 接口地址（OpenAI 兼容端点）：与其它行同一 form，边框自然对齐
        self.endpoint_edit = QLineEdit()
        self.label_endpoint = QLabel()
        form.addRow(self.label_endpoint, self.endpoint_edit)
        form.addRow(self.label_model, self.model_edit)

        self.temp_edit = QDoubleSpinBox()
        self.temp_edit.setRange(0, 2.0)
        self.temp_edit.setSingleStep(0.1)
        self.label_temperature = QLabel()
        form.addRow(self.label_temperature, self.temp_edit)

        self.max_tokens_edit = QSpinBox()
        self.max_tokens_edit.setRange(1, 8192)
        self.label_max_tokens = QLabel()
        form.addRow(self.label_max_tokens, self.max_tokens_edit)
        form_host.addLayout(form)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton()
        self.test_btn.clicked.connect(self.test_connection)
        test_row.addWidget(self.test_btn)
        self.test_status = QLabel("")
        self.test_status.setObjectName("hintLabel")
        test_row.addWidget(self.test_status, 1)
        form_host.addLayout(test_row)
        
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_appearance_section(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        card, lay = self._card()
        self.theme_group_title = QLabel()
        self.theme_group_title.setObjectName("cardTitle")
        lay.addWidget(self.theme_group_title)

        form = QFormLayout()
        form.setSpacing(10)
        self.theme_combo = QComboBox()
        self._theme_names = self.theme.available() if self.theme is not None else ["light"]
        for name in self._theme_names:
            self.theme_combo.addItem(name, name)   # 文本由 retranslateUi 填
        self.label_theme = QLabel()
        form.addRow(self.label_theme, self.theme_combo)

        self.ui_language_combo = QComboBox()
        if self.i18n is not None:
            for code in self.i18n.available():
                self.ui_language_combo.addItem(self.i18n.display_name(code), code)
        self.label_ui_language = QLabel()
        form.addRow(self.label_ui_language, self.ui_language_combo)

        self.skin_combo = QComboBox()
        if self.skins is not None:
            for item in self.skins.available():
                self.skin_combo.addItem(item["name"], item["id"])
        self.skin_combo.currentIndexChanged.connect(self._on_pet_skin_changed)
        self.label_skin = QLabel()
        form.addRow(self.label_skin, self.skin_combo)

        # ---- 桌宠尺寸 / 显隐：状态在内核 PetService（插件解耦，任何插件都不再控制宠物）----
        from core.services.pet_service import MAX_SIZE, MIN_SIZE, PetService

        pet_service = PetService.instance()
        self.pet_size_slider = QSlider(Qt.Horizontal)
        self.pet_size_slider.setRange(MIN_SIZE, MAX_SIZE)

        self.pet_size_slider.setValue(int(pet_service.size))
        self.pet_size_value = QLabel(f"{pet_service.size} px")
        self.pet_size_slider.valueChanged.connect(self._on_pet_size_changed)
        size_row = QHBoxLayout()
        size_row.addWidget(self.pet_size_slider, 1)
        size_row.addWidget(self.pet_size_value)
        self.label_pet_size = QLabel()
        form.addRow(self.label_pet_size, size_row)

        self.pet_visible_check = QCheckBox()  # 文案在 retranslateUi 里设置
        self.pet_visible_check.setChecked(bool(pet_service.visible))
        self.pet_visible_check.toggled.connect(self._on_pet_visible_toggled)
        form.addRow(self.pet_visible_check)
        lay.addLayout(form)

        self.skin_hint = QLabel()
        self.skin_hint.setWordWrap(True)
        self.skin_hint.setObjectName("hintLabel")
        lay.addWidget(self.skin_hint)
        layout.addWidget(card)

        card2, lay2 = self._card()
        self.custom_group_title = QLabel()
        self.custom_group_title.setObjectName("cardTitle")
        lay2.addWidget(self.custom_group_title)
        self.qss_hint = QLabel()
        self.qss_hint.setWordWrap(True)
        self.qss_hint.setObjectName("hintLabel")
        lay2.addWidget(self.qss_hint)
        self.open_qss_btn = QPushButton()
        self.open_qss_btn.clicked.connect(self.open_user_qss)
        lay2.addWidget(self.open_qss_btn, 0, Qt.AlignLeft)
        layout.addWidget(card2)
        layout.addStretch(1)
        return page

    # ---------------- 关于 ----------------
    def _build_monitor_section(self) -> QWidget:
        """API 监控：任务 token 清单 + 单次明细 + 每日/每月消耗（与插件页面解耦）。"""
        from core.widgets.api_monitor import ApiMonitorPanel

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        self.monitor_panel = ApiMonitorPanel(i18n=self.i18n, parent=page)
        layout.addWidget(self.monitor_panel, 1)
        return page

    def _build_storage_section(self) -> QWidget:
        """存储管理：区分用户资产与运行垃圾（用户要求：不能误删资产；独立 Tab，不塞在关于里）。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card3, lay3 = self._card()
        self.storage_title = QLabel()
        self.storage_title.setObjectName("cardTitle")
        lay3.addWidget(self.storage_title)
        self.storage_value = QLabel()
        self.storage_value.setObjectName("hintLabel")
        self.storage_value.setWordWrap(True)
        lay3.addWidget(self.storage_value)
        storage_row = QHBoxLayout()
        storage_row.setSpacing(8)
        self.storage_logs_btn = QPushButton()
        self.storage_logs_btn.clicked.connect(lambda: self._clean_storage(["logs", "wal"]))
        self.storage_cache_btn = QPushButton()
        self.storage_cache_btn.clicked.connect(lambda: self._clean_storage(["cache"]))
        self.storage_trash_btn = QPushButton()
        self.storage_trash_btn.clicked.connect(self._confirm_empty_trash)
        storage_row.addWidget(self.storage_logs_btn)
        storage_row.addWidget(self.storage_cache_btn)
        storage_row.addWidget(self.storage_trash_btn)
        storage_row.addStretch(1)
        lay3.addLayout(storage_row)
        self.storage_result = QLabel()
        self.storage_result.setObjectName("hintLabel")
        self.storage_result.setWordWrap(True)
        lay3.addWidget(self.storage_result)
        self.storage_hint = QLabel()
        self.storage_hint.setObjectName("hintLabel")
        self.storage_hint.setWordWrap(True)
        lay3.addWidget(self.storage_hint)
        layout.addWidget(card3)

        layout.addStretch(1)
        self._refresh_storage()
        return page

    def _build_about_section(self) -> QWidget:
        """软件名/版本/作者等元信息：版本号统一取自 `src/__version__.py`（单一来源）。"""
        from __version__ import APP_NAME, AUTHOR, __version__

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card, lay = self._card()
        self.about_title = QLabel()
        self.about_title.setObjectName("cardTitle")
        lay.addWidget(self.about_title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setHorizontalSpacing(16)
        self.about_name_label = QLabel()
        self.about_name_value = QLabel(APP_NAME)
        form.addRow(self.about_name_label, self.about_name_value)
        self.about_version_label = QLabel()
        self.about_version_value = QLabel(__version__)
        form.addRow(self.about_version_label, self.about_version_value)
        self.about_author_label = QLabel()
        self.about_author_value = QLabel(AUTHOR)
        form.addRow(self.about_author_label, self.about_author_value)
        self.about_env_label = QLabel()
        self.about_env_value = QLabel(f"Python {sys.version.split()[0]} · PySide6 {_qt_version()}")
        self.about_env_value.setWordWrap(True)
        form.addRow(self.about_env_label, self.about_env_value)
        lay.addLayout(form)

        # ---- 检查更新（v1：只提示 + 打开下载页，不做自动替换安装）----
        self.auto_check_update_check = QCheckBox()
        self.auto_check_update_check.toggled.connect(self._on_auto_check_toggled)
        lay.addWidget(self.auto_check_update_check)

        # 状态文案独占一行（新版本提示可能很长，挤在同一行会把按钮推出可视区）
        self.update_status = QLabel()
        self.update_status.setObjectName("hintLabel")
        self.update_status.setWordWrap(True)
        lay.addWidget(self.update_status)

        update_buttons = QHBoxLayout()
        update_buttons.setSpacing(8)
        update_buttons.addStretch(1)
        self.update_open_btn = QPushButton()
        self.update_open_btn.setVisible(False)
        self.update_open_btn.clicked.connect(self._open_release_page)
        self.update_btn = QPushButton()
        self.update_btn.clicked.connect(self.check_update_now)
        update_buttons.addWidget(self.update_open_btn)
        update_buttons.addWidget(self.update_btn)
        lay.addLayout(update_buttons)
        layout.addWidget(card)

        card2, lay2 = self._card()
        self.about_paths_title = QLabel()
        self.about_paths_title.setObjectName("cardTitle")
        lay2.addWidget(self.about_paths_title)
        self.about_paths_value = QLabel(f"{self._config_dir()}\n{self._config_dir() / 'app.log'}")
        self.about_paths_value.setWordWrap(True)
        self.about_paths_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.about_paths_value.setObjectName("hintLabel")
        lay2.addWidget(self.about_paths_value)
        layout.addWidget(card2)

        self._refresh_storage()
        layout.addWidget(card2)
        layout.addStretch(1)
        return page

    def _config_dir(self):
        from core.services.config_manager import CONFIG_DIR

        return CONFIG_DIR

    # ---------------- 检查更新 ----------------
    def check_update_now(self) -> None:
        """手动检查：不看节流，但 10 秒内不重复打网络（连点按钮不该刷接口）。"""
        if self._update_checker is not None and self._update_checker.isRunning():
            return
        now = time.monotonic()
        if now - self._last_manual_check < MANUAL_COOLDOWN_SECONDS:
            return
        self._last_manual_check = now
        self._run_update_check(manual=True)

    def start_background_update_check(self) -> None:
        """启动时的自动检查：按配置节流；失败静默（只记日志）。"""
        from core.services import update_service

        if not update_service.needs_check(self.config):
            return
        if self._update_checker is not None and self._update_checker.isRunning():
            return
        self._run_update_check(manual=False)

    def _run_update_check(self, *, manual: bool) -> None:
        self.update_btn.setEnabled(False)
        self.update_status.setText(self.tr("app.update.checking"))
        checker = _UpdateChecker()
        checker.done.connect(
            lambda info, kind, detail: self._on_update_checked(info, kind, detail, manual=manual))
        self._update_checker = checker
        checker.start()

    def _on_update_checked(self, info, kind: str, detail: str, *, manual: bool) -> None:
        from core.services import update_service

        self.update_btn.setEnabled(True)
        update_service.mark_checked(self.config, info)      # 失败也记时间戳，避免断网时反复重试

        if info is None:
            self._update_info = None
            self.update_open_btn.setVisible(False)
            if manual:
                self.update_status.setText(self._update_error_text(kind, detail))
            else:
                logger.info("自动检查更新失败（%s：%s），静默忽略", kind, detail)
                self.update_status.setText("")
            return

        self._update_info = info if info.has_update else None
        self.update_open_btn.setVisible(bool(info.has_update))
        if info.has_update:
            self.update_status.setText(self.tr("app.update.available", version=info.latest))
            logger.info("发现新版本：本地 %s → 最新 %s", info.current, info.latest)
        else:
            self.update_status.setText(self.tr("app.update.up_to_date", version=info.current))

    def _update_error_text(self, kind: str, detail: str) -> str:
        key = {
            "timeout": "app.update.failed.timeout",
            "network": "app.update.failed.network",
            "no_release": "app.update.failed.no_release",
            "parse": "app.update.failed.parse",
        }.get(kind, "app.update.failed.http")
        text = self.tr(key)
        return f"{text}（{detail}）" if kind == "http" and detail else text

    def _open_release_page(self) -> None:
        """打开下载入口：优先直连本版 zip，其次 Release 页面。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from core.services.update_service import RELEASE_PAGE

        info = self._update_info
        target = ""
        if info is not None:
            target = info.download_url or info.release_url
        QDesktopServices.openUrl(QUrl(target or RELEASE_PAGE))

    def _on_auto_check_toggled(self, enabled: bool) -> None:
        """自动检查开关：单个布尔偏好，直接落盘（不混进「保存设置」的一揽子表单）。"""
        self.config.set("auto_check_update", bool(enabled))

    def _stop_update_checker(self) -> None:
        """退出/销毁前收掉后台检查线程（QThread 运行中析构会让进程直接崩）。"""
        checker = self._update_checker
        if checker is not None and checker.isRunning():
            checker.requestInterruption()
            checker.wait(1500)
        self._update_checker = None

    # ---------------- 存储管理 ----------------
    def _refresh_storage(self) -> None:
        """按类别统计可清理的空间（只统计，不删除）。"""
        try:
            from core.services import housekeeping

            groups = housekeeping.inventory()
            self.storage_value.setText("\n".join(
                self.tr("settings.storage.item",
                        title=self.tr(f"settings.storage.group.{group.key}"),
                        size=f"{group.size / 1024:.0f}", count=group.count)
                for group in groups))
        except Exception as exc:               # noqa: BLE001 - 统计失败不该挡住设置页
            self.storage_value.setText(str(exc))

    def _clean_storage(self, keys: list[str], keep_days: int = 0) -> None:
        """清理指定类别；用户资产一律不动（白名单在 housekeeping 里）。

        被本进程占用的文件（缓存库/-wal）删不掉时排队到下次启动再清 —— 否则用户点了没反应。
        """
        from core.services import housekeeping

        try:
            housekeeping.checkpoint_wal()
            result = housekeeping.clean(keys, keep_days=keep_days)
            removed = sum(count for count, _, _ in result.values())
            freed = sum(size for _, size, _ in result.values())
            skipped = sum(skip for _, _, skip in result.values())
            if skipped:
                housekeeping.mark_pending(
                    [key for key, (_, _, skip) in result.items() if skip])
            self.storage_result.setText(self.tr(
                "settings.storage.done_skipped" if skipped else "settings.storage.done",
                count=removed, size=f"{freed / 1024:.0f}", skipped=skipped))
        except Exception as exc:               # noqa: BLE001 - 清理失败要能看见
            self.storage_result.setText(self.tr("settings.storage.failed", error=exc))
        self._refresh_storage()

    def _confirm_empty_trash(self) -> None:
        answer = QMessageBox.question(self, self.tr("settings.storage.title"),
                                      self.tr("settings.storage.trash_confirm"))
        if answer == QMessageBox.Yes:
            self._clean_storage(["trash"], keep_days=0)

    # ---------------- 多语言（热切换） ----------------
    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """语言切换后刷新本页的全部可见文案。"""
        for index, key in enumerate(_NAV_KEYS):
            item = self.nav_list.item(index)
            if item is not None:
                item.setText(self.tr(key))

        self.label_api_key.setText(self.tr("settings.api.key"))
        self.label_model.setText(self.tr("settings.api.model"))
        self.label_endpoint.setText(self.tr("settings.api.endpoint"))
        self.endpoint_edit.setPlaceholderText(self.tr("settings.api.endpoint_placeholder"))
        self.label_temperature.setText(self.tr("settings.api.temperature"))
        self.label_max_tokens.setText(self.tr("settings.api.max_tokens"))
        self.api_key_edit.setPlaceholderText(self.tr("settings.api.key_placeholder"))
        self.api_key_toggle.setText(
            self.tr("settings.api.key_hide") if self.api_key_toggle.isChecked()
            else self.tr("settings.api.key_show"))
        self.model_edit.setPlaceholderText(self.tr("settings.api.model_placeholder"))
        self.test_btn.setText(self.tr("settings.api.test"))

        # 关于页（版本号等值来自 __version__.py，这里只翻译标签）
        self.about_title.setText(self.tr("settings.about.title"))
        self.about_name_label.setText(self.tr("settings.about.name"))
        self.about_version_label.setText(self.tr("settings.about.version"))
        self.about_author_label.setText(self.tr("settings.about.author"))
        self.about_env_label.setText(self.tr("settings.about.env"))
        # 检查更新（文案随语言/状态刷新；已查过就把结论重新渲染一遍）
        self.auto_check_update_check.setText(self.tr("app.update.auto"))
        self.auto_check_update_check.setToolTip(self.tr("app.update.privacy"))
        self.update_btn.setText(self.tr("app.update.check"))
        self.update_open_btn.setText(self.tr("app.update.open"))
        info = self._update_info
        if info is not None:
            self.update_status.setText(self.tr("app.update.available", version=info.latest))
        elif not self.update_status.text():
            self.update_status.setText("")
        self.about_paths_title.setText(self.tr("settings.about.paths"))
        if hasattr(self, "monitor_panel"):
            self.monitor_panel.retranslate()      # 表格表头/按钮也要跟着换语言
        # 清单文案是"组名 + 数量"拼出来的，切语言必须重算，否则仍是旧语言
        if hasattr(self, "storage_value"):
            self._refresh_storage()
        self._retranslate_skin_combo()      # 皮肤名同样只随界面语言
        self.storage_title.setText(self.tr("settings.storage.title"))
        self.storage_hint.setText(self.tr("settings.storage.hint"))
        self.storage_logs_btn.setText(self.tr("settings.storage.logs"))
        self.storage_cache_btn.setText(self.tr("settings.storage.cache"))
        self.storage_trash_btn.setText(self.tr("settings.storage.trash"))

        self.theme_group_title.setText(self.tr("settings.appearance.theme_group"))
        self.label_theme.setText(self.tr("settings.appearance.theme"))
        self.label_ui_language.setText(self.tr("settings.appearance.language"))
        self.label_skin.setText(self.tr("settings.appearance.skin"))
        self.label_pet_size.setText(self.tr("app.settings.pet_size"))
        self.pet_visible_check.setText(self.tr("app.settings.pet_visible"))
        self.skin_hint.setText(self.tr("settings.appearance.skin_hint"))
        for index in range(self.theme_combo.count()):
            self.theme_combo.setItemText(index, self.tr(f"theme.{self.theme_combo.itemData(index)}"))
        self.custom_group_title.setText(self.tr("settings.appearance.custom_group"))
        self.qss_hint.setText(self.tr("settings.appearance.custom_hint", path=USER_QSS))
        self.open_qss_btn.setText(self.tr("settings.appearance.open_qss"))


    # ---------------- 数据 ----------------
    def open_user_qss(self):
        """用系统默认程序打开用户自定义样式文件。"""
        if not USER_QSS.exists():
            QMessageBox.information(self, self.tr("settings.appearance.qss_missing_title"),
                                    self.tr("settings.appearance.qss_missing", path=USER_QSS))
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(USER_QSS))          # noqa: S606 - 用户主动打开自己的配置文件
            else:
                subprocess.Popen(["xdg-open", str(USER_QSS)])
        except Exception as exc:
            QMessageBox.warning(self, self.tr("settings.appearance.open_fail_title"), str(exc))

    def load_settings(self):
        # 程序化赋值会触发 textChanged/valueChanged：这段必须挡住自动保存，
        # 否则每次打开设置页都会"写盘 + 闪一下已保存"。
        self._loading = True
        try:
            self.api_key_edit.setText(self.config.get_api_key())
            self.model_edit.setText(self.config.get("model", "deepseek-flash"))
            self.endpoint_edit.setText(
                str(self.config.get("api_endpoint", "https://api.deepseek.com") or ""))
            self.temp_edit.setValue(self.config.get("temperature", 0.4))
            self.max_tokens_edit.setValue(self.config.get("max_tokens", 2000))
            if self.theme is not None:
                self._select_data(self.theme_combo, self.theme.current)
            self._select_data(self.ui_language_combo,
                              self.i18n.current if self.i18n is not None else "zh_CN")
            self._select_data(self.skin_combo,
                              self.skins.current_id if self.skins is not None else DEFAULT_SKIN_ID)
            auto_check = bool(self.config.get("auto_check_update", True))
            self.auto_check_update_check.blockSignals(True)
            self.auto_check_update_check.setChecked(auto_check)
            self.auto_check_update_check.blockSignals(False)
        finally:
            self._loading = False
        # 记下"当前已落盘的样子"：之后没有真实改动就不写盘
        self._last_payload = {
            "model": self.model_edit.text(),
            "api_endpoint": self.endpoint_edit.text().strip() or "https://api.deepseek.com",
            "temperature": self.temp_edit.value(),
            "max_tokens": self.max_tokens_edit.value(),
            "ui_language": self.ui_language_combo.currentData(),
        }
        if self.skins is not None and self.skin_combo.currentData():
            self._last_payload["skin"] = self.skin_combo.currentData()
        self._last_appearance = (
            self._last_payload.get("ui_language"), self._last_payload.get("skin"),
            self.theme_combo.currentData() if self.theme is not None else None)

    @staticmethod
    def _select_data(combo: QComboBox, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save_settings(self):
        """落盘 + 应用一次（即改即存的唯一出口，页面上已没有"保存"按钮）。

        - 一次 `config.update()` 写盘：逐项 `set()` 会重写配置文件十余次，中途失败会留下
          "一半新一半旧"的配置；
        - **payload 与上次成功写入相同且密钥没改 ⇒ 直接返回**：防抖空转、切 Tab、重复点同一项
          都不该产生写盘与"已保存"提示；
        - 写盘失败要可见（底部保留原因），且不广播 saved。
        """
        payload = {
            "model": self.model_edit.text(),
            "api_endpoint": self.endpoint_edit.text().strip() or "https://api.deepseek.com",
            "temperature": self.temp_edit.value(),
            "max_tokens": self.max_tokens_edit.value(),
            # 界面语言：落盘后由主窗口拉起全局重翻译
            "ui_language": self.ui_language_combo.currentData(),
        }
        # 桌宠皮肤：写入配置，由主窗口调用 kernel.set_skin 广播 skin.changed
        if self.skins is not None and self.skin_combo.currentData():
            payload["skin"] = self.skin_combo.currentData()

        # 只有用户**真的改了**密钥才写：否则清空/未改都会把已存密钥覆盖成空串
        typed_key = self.api_key_edit.text().strip()
        stored_key = ""
        try:
            stored_key = (self.config.get_api_key() or "").strip()
        except Exception:                      # noqa: BLE001 - 读失败按"未改"处理
            stored_key = ""
        api_key_arg = typed_key if typed_key != stored_key else None
        if api_key_arg is None and payload == self._last_payload:
            return                             # 没有任何变化：不写盘、不提示、不广播

        ok = (self.config.update(payload, api_key=api_key_arg) if api_key_arg is not None
              else self.config.update(payload))
        if not ok:
            reason = getattr(self.config, "last_save_error", "") or ""
            self.save_status.setText(self.tr("settings.save_failed")
                                     + (f"：{reason}" if reason else ""))
            return

        appearance_before = self._last_appearance
        self._last_payload = dict(payload)

        # 主题：保存到配置并立即应用（服务会广播 theme.changed）
        if self.theme is not None:
            selected = self.theme_combo.currentData()
            if selected and selected != self.theme.current:
                self.theme.set_theme(selected)

        appearance_now = (payload.get("ui_language"), payload.get("skin"),
                          self.theme_combo.currentData() if self.theme is not None else None)
        self._last_appearance = appearance_now
        self._show_saved_toast()
        self.saved.emit()
        if appearance_now != appearance_before:
            # 外观真的变了才让外壳切语言/皮肤/重刷样式（打字时别每 0.5s 全量重刷）
            self.appearanceSaved.emit()

    def _show_saved_toast(self) -> None:
        """"已保存"提示 2 秒后自动消失；失败提示保留到下次成功。"""
        token = object()
        self._toast_token = token
        self.save_status.setText(self.tr("settings.saved_hint"))

        def _clear() -> None:
            if getattr(self, "_toast_token", None) is token:
                self.save_status.setText("")

        QTimer.singleShot(SAVED_TOAST_MS, _clear)

    def _stop_tester(self, *_args) -> None:
        """页面销毁时先让连接测试线程结束，避免 QThread 运行中被析构（U03）。"""
        tester = getattr(self, "_tester", None)
        if tester is None:
            return
        try:
            tester.requestInterruption()
            tester.quit()
        except Exception:                      # noqa: BLE001 - 已在收尾阶段，忽略
            pass  # silent-ok: 收尾阶段对象可能已释放
        if tester.isRunning():
            tester.wait(3000)
        self._tester = None

    def test_connection(self):
        """测连通性：走后台线程，界面不冻结；按钮期间禁用，结束后恢复。"""
        key = self.api_key_edit.text().strip()
        model = self.model_edit.text().strip()
        if not key:
            QMessageBox.warning(self, self.tr("settings.title"),
                                self.tr("settings.api.test_empty"))
            return

        self.test_btn.setEnabled(False)
        self.test_status.setText(self.tr("settings.api.testing"))
        endpoint = self.endpoint_edit.text().strip() or "https://api.deepseek.com"
        self._tester = _ConnectionTester(key, model, endpoint)  # 无父对象：生命周期自管
        self._tester.done.connect(self._on_test_done)
        self._tester.finished.connect(self._tester.deleteLater)
        self._tester.start()

    def _on_test_done(self, ok: bool, reason: str) -> None:
        text = self.tr("settings.api.test_ok") if ok else self.tr("settings.api.test_fail")
        if not ok and reason:
            text += f"：{reason}"
        # 测试连接不会保存 Key：通过后提醒一次，避免"测过就以为生效了"（用户现场）
        if ok and self.api_key_edit.text().strip() != (self.config.get_api_key() or "").strip():
            text += " · " + self.tr("settings.api.remember_save")
        self.test_status.setText(text)
        self.test_btn.setEnabled(True)
