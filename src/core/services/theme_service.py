# core/services/theme_service.py
"""主题服务：设计令牌 -> QSS 渲染 -> 应用级样式 + 用户覆盖层。

设计要点：
- 令牌（tokens.<theme>.json）+ 模板（base.qss，占位符 @token.name）分离，
  改一个色值即可全局生效；
- 用户覆盖层 config/user.qss 追加在内置样式之后（同优先级后者胜出），
  支持保存后热重载；
- 服务本身只产出「字符串与色值」，不碰 Qt 控件；
  真正 setStyleSheet 由 app 层通过 set_apply_hook 注入，
  因此内核侧保持可脱离界面测试。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from core.services.config_manager import CONFIG_DIR
from infra.utils import resource_path

logger = logging.getLogger(__name__)

THEMES_DIR = Path(resource_path("assets/themes"))
USER_QSS = CONFIG_DIR / "user.qss"
USER_QSS_TEMPLATE = THEMES_DIR / "user.example.qss"

_PLACEHOLDER = re.compile(r"@([A-Za-z0-9_.]+)")
# diff 高亮走 HTML 富文本，QSS 管不到，故单独以调色板形式暴露给业务代码
_DIFF_KEYS = {
    "del_bg": "color.diff.del.bg",
    "del_text": "color.diff.del.text",
    "ins_bg": "color.diff.ins.bg",
    "ins_text": "color.diff.ins.text",
}


class ThemeService:
    def __init__(self, config, themes_dir: Path | None = None) -> None:
        self.config = config
        self.themes_dir = Path(themes_dir) if themes_dir else THEMES_DIR
        self._tokens: dict[str, str] = {}
        self._base_qss = ""
        self._apply_hook = None
        self._watcher = None
        self._ensure_user_qss()

    # ---------------- 主题切换 ----------------
    @property
    def current(self) -> str:
        return self.config.get("theme", "light")

    def available(self) -> list[str]:
        """按令牌文件列出可用主题。"""
        if not self.themes_dir.is_dir():
            return ["light"]
        names = []
        for path in sorted(self.themes_dir.glob("tokens.*.json")):
            names.append(path.stem.split(".", 1)[1])
        return names or ["light"]

    def set_theme(self, name: str) -> None:
        if name not in self.available():
            logger.warning("未知主题: %s，回退 light", name)
            name = "light"
        self.config.set("theme", name)
        self.reload()

    def reload(self) -> None:
        """重新加载令牌与模板并应用（切主题 / user.qss 变更后调用）。"""
        self._load()
        self.apply()

    # ---------------- 渲染 ----------------
    def _load(self) -> None:
        self._tokens = self._load_tokens(self.current)
        base_path = self.themes_dir / "base.qss"
        try:
            self._base_qss = base_path.read_text(encoding="utf-8")
        except Exception:
            logger.exception("读取主题模板失败: %s", base_path)
            self._base_qss = ""

    def _load_tokens(self, name: str) -> dict[str, str]:
        path = self.themes_dir / f"tokens.{name}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("读取主题令牌失败: %s，回退 light", path)
            try:
                data = json.loads((self.themes_dir / "tokens.light.json").read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {k: str(v) for k, v in data.items() if not k.startswith("_")}

    def token(self, key: str, default: str = "") -> str:
        """取令牌字符串值（插件拼接动态样式时用）。"""
        return self._tokens.get(key, default)

    def render_fragment(self, template: str) -> str:
        """把带 @token 占位符的样式片段渲染成可直接使用的 QSS。"""
        return self._fill(template)

    def diff_palette(self) -> dict[str, str]:
        """diff 高亮配色（HTML 富文本用，随主题变化）。"""
        return {name: self.token(key, "#000000") for name, key in _DIFF_KEYS.items()}

    def render(self) -> str:
        """内置样式 + 用户覆盖层，交给 QApplication.setStyleSheet。"""
        parts = [self._fill(self._base_qss)]
        user_qss = self.user_qss_text()
        if user_qss.strip():
            parts.append(user_qss)
        return "\n".join(parts)

    def _fill(self, text: str) -> str:
        def _sub(match: re.Match) -> str:
            return self._tokens.get(match.group(1), match.group(0))

        filled = _PLACEHOLDER.sub(_sub, text)
        # Qt 的 CSS 解析器不接受数字与单位之间的空格（"6 px" 会整条丢弃），
        # 模板里 "@spacing.sm px" 写法替换后必须把空格压掉
        return re.sub(r"(\d+(?:\.\d+)?) px", r"\1px", filled)

    # ---------------- 用户覆盖层 ----------------
    def user_qss_text(self) -> str:
        try:
            return USER_QSS.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception:
            logger.exception("读取用户样式失败: %s", USER_QSS)
            return ""

    def _ensure_user_qss(self) -> None:
        """首次运行时用模板生成可编辑的用户样式文件。"""
        if USER_QSS.exists() or not USER_QSS_TEMPLATE.exists():
            return
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            USER_QSS.write_text(USER_QSS_TEMPLATE.read_text(encoding="utf-8"),
                                encoding="utf-8")
        except Exception:
            logger.exception("生成用户样式模板失败: %s", USER_QSS)

    # ---------------- 应用与热重载 ----------------
    def set_apply_hook(self, hook) -> None:
        """由 app 层注入真正的赋值动作：hook(qss_text)。"""
        self._apply_hook = hook

    def apply(self) -> None:
        qss = self.render()
        if self._apply_hook is None:
            return
        try:
            self._apply_hook(qss)
        except Exception:
            logger.exception("应用主题失败")

    def watch_user_qss(self) -> None:
        """监听 config/user.qss，保存后即时热重载（需在 QApplication 之后调用）。"""
        if self._watcher is not None:
            return
        try:
            from PySide6.QtCore import QFileSystemWatcher
        except Exception:
            logger.warning("Qt 不可用，跳过用户样式热重载")
            return
        self._watcher = QFileSystemWatcher([str(USER_QSS)])
        self._watcher.fileChanged.connect(lambda *_: self.reload())
