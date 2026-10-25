# settings_page.py
"""设置页（主窗口内置页面，取代原先的模态对话框）。

形态：左侧子导航（API / 提示词 / 界面 / 外观）+ 右侧内容堆叠，
底部一条「保存」栏——点保存立即写配置并发 saved 信号，
后续动作（重建 AI 客户端、热键重绑、语言/皮肤热切换）由主窗口统一处理。

- 所有文案走 i18n（构造时可传入 i18n 服务）；
- 「界面语言」与「提示词语言」是两个独立配置：
  前者只影响 UI，后者决定送 AI 的提示词语种，互不影响。
"""
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QTextEdit, QSpinBox, QStackedWidget, QWidget,
    QMessageBox, QSlider, QGroupBox, QCheckBox, QComboBox, QDoubleSpinBox,
    QListWidget, QScrollArea,
)

from core.services.api_client import DeepSeekClient
from core.services.theme_service import USER_QSS

#: 界面语言与提示词语言共用的可选语种
PROMPT_LANGUAGE_NAMES = {
    "zh_CN": "中文",
    "en_US": "English",
}

_NAV_KEYS = ("settings.tab.api", "settings.tab.appearance", "settings.tab.about")


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

    def __init__(self, api_key: str, model: str, parent=None) -> None:
        super().__init__(parent)
        self._api_key = api_key
        self._model = model

    def run(self) -> None:        # noqa: D401 - QThread 入口
        try:
            client = DeepSeekClient({"api_key": self._api_key, "model": self._model})
            ok = bool(client.test_connection())
            reason = "" if ok else (getattr(client, "last_error", "") or "")
        except Exception as exc:  # noqa: BLE001  后台线程里任何异常都必须回传，不能吞
            ok, reason = False, str(exc)
        self.done.emit(ok, reason)


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, config, parent=None, theme=None, i18n=None, skins=None):
        super().__init__(parent)
        self.config = config
        self.theme = theme
        self.i18n = i18n
        self.skins = skins
        self._tester: _ConnectionTester | None = None

        self.init_ui()
        self.load_settings()
        self.retranslateUi()

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
        self.stack.addWidget(self._build_about_section())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.stack)
        body.addWidget(scroll, 1)
        main_layout.addLayout(body, 1)

        self.nav_list.setCurrentRow(0)

        # 底部保存栏
        bottom = QHBoxLayout()
        self.save_status = QLabel("")
        self.save_status.setObjectName("hintLabel")
        bottom.addWidget(self.save_status, 1)
        self.save_btn = QPushButton()
        self.save_btn.setProperty("accent", True)
        self.save_btn.clicked.connect(self.save_settings)
        bottom.addWidget(self.save_btn)
        main_layout.addLayout(bottom)

    def _switch_section(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    # ---------------- 桌宠外观 ----------------
    def _on_pet_size_changed(self, value: int) -> None:
        self.pet_size_value.setText(f"{value} px")
        try:
            from core.services.pet_host import PetHost
            from core.services.pet_service import PetService

            PetService.instance().set_size(int(value))
            PetHost.instance().apply_size(int(value))       # 立即生效，不依赖信号
        except Exception:                      # noqa: BLE001
            pass

    def _on_pet_visible_toggled(self, visible: bool) -> None:
        try:
            from core.services.pet_host import PetHost
            from core.services.pet_service import PetService

            PetService.instance().set_visible(bool(visible))
            PetHost.instance().apply_visibility(bool(visible))
        except Exception:                      # noqa: BLE001
            pass
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
        self.model_edit = QLineEdit()
        self.label_api_key = QLabel()
        self.label_model = QLabel()
        form.addRow(self.label_api_key, self.api_key_edit)
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
        
        # 提示词语言属全局（同时影响校对与翻译），随 API 配置一起放这里
        lang_row = QHBoxLayout()
        self.label_prompt_language = QLabel()
        self.prompt_language_combo = QComboBox()
        for code, name in PROMPT_LANGUAGE_NAMES.items():
            self.prompt_language_combo.addItem(name, code)
        lang_row.addWidget(self.label_prompt_language)
        lang_row.addWidget(self.prompt_language_combo, 1)
        form_host.addLayout(lang_row)
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
        layout.addStretch(1)
        return page

    def _config_dir(self):
        from core.services.config_manager import CONFIG_DIR

        return CONFIG_DIR

    # ---------------- 多语言（热切换） ----------------
    def retranslateUi(self):        # noqa: N802 - Qt 命名惯例
        """语言切换后刷新本页的全部可见文案。"""
        for index, key in enumerate(_NAV_KEYS):
            item = self.nav_list.item(index)
            if item is not None:
                item.setText(self.tr(key))

        self.label_api_key.setText(self.tr("settings.api.key"))
        self.label_model.setText(self.tr("settings.api.model"))
        self.label_temperature.setText(self.tr("settings.api.temperature"))
        self.label_max_tokens.setText(self.tr("settings.api.max_tokens"))
        self.api_key_edit.setPlaceholderText(self.tr("settings.api.key_placeholder"))
        self.model_edit.setPlaceholderText(self.tr("settings.api.model_placeholder"))
        self.test_btn.setText(self.tr("settings.api.test"))

        # 关于页（版本号等值来自 __version__.py，这里只翻译标签）
        self.about_title.setText(self.tr("settings.about.title"))
        self.about_name_label.setText(self.tr("settings.about.name"))
        self.about_version_label.setText(self.tr("settings.about.version"))
        self.about_author_label.setText(self.tr("settings.about.author"))
        self.about_env_label.setText(self.tr("settings.about.env"))
        self.about_paths_title.setText(self.tr("settings.about.paths"))
        self.label_prompt_language.setText(self.tr("settings.prompt.language"))

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

        self.save_btn.setText(self.tr("settings.btn.save"))

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
        self.api_key_edit.setText(self.config.get_api_key())
        self.model_edit.setText(self.config.get("model", "deepseek-v4-flash"))
        self.temp_edit.setValue(self.config.get("temperature", 0.4))
        self.max_tokens_edit.setValue(self.config.get("max_tokens", 2000))
        if self.theme is not None:
            self._select_data(self.theme_combo, self.theme.current)
        self._select_data(self.ui_language_combo,
                          self.i18n.current if self.i18n is not None else "zh_CN")
        self._select_data(self.prompt_language_combo, self.config.get("prompt_language", "zh_CN"))
        self._select_data(self.skin_combo,
                          self.skins.current_id if self.skins is not None else "default")

    @staticmethod
    def _select_data(combo: QComboBox, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save_settings(self):
        """把整页设置**一次性**落盘；写盘失败要可见，且不广播 saved。

        历史实现逐项 `config.set(...)`：一次保存重写配置文件十余次，
        中途失败会留下前后不一致的配置，异常也只冒到 stderr（打包后不可见）。
        """
        payload = {
            "model": self.model_edit.text(),
            "temperature": self.temp_edit.value(),
            "max_tokens": self.max_tokens_edit.value(),
            # 动态窗口样式（配色改由主题令牌统一控制）
            # 提示词语言（与界面语言解耦，由调用方决定是否重建客户端）
            "prompt_language": self.prompt_language_combo.currentData(),
            # 界面语言：保存后由主窗口拉起全局重翻译
            "ui_language": self.ui_language_combo.currentData(),
        }
        # 桌宠皮肤：写入配置，由主窗口调用 kernel.set_skin 广播 skin.changed
        if self.skins is not None and self.skin_combo.currentData():
            payload["skin"] = self.skin_combo.currentData()

        ok = self.config.update(payload, api_key=self.api_key_edit.text().strip())
        if not ok:
            reason = getattr(self.config, "last_save_error", "") or ""
            self.save_status.setText(self.tr("settings.save_failed")
                                     + (f"：{reason}" if reason else ""))
            return

        # 主题：保存到配置并立即应用（服务会广播 theme.changed）
        if self.theme is not None:
            selected = self.theme_combo.currentData()
            if selected and selected != self.theme.current:
                self.theme.set_theme(selected)

        self.save_status.setText(self.tr("settings.saved_hint"))
        self.saved.emit()

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
        self._tester = _ConnectionTester(key, model, self)
        self._tester.done.connect(self._on_test_done)
        self._tester.finished.connect(self._tester.deleteLater)
        self._tester.start()

    def _on_test_done(self, ok: bool, reason: str) -> None:
        text = self.tr("settings.api.test_ok") if ok else self.tr("settings.api.test_fail")
        if not ok and reason:
            text += f"：{reason}"
        self.test_status.setText(text)
        self.test_btn.setEnabled(True)
