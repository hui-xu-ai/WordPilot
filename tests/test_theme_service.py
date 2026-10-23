# tests/test_theme_service.py
"""主题渲染门（令牌 + base.qss 模板）离线断言。

来源：`tools/smoke_p8.py:186-192`（SpinBox 箭头必须显式给图、数字与单位间不能留空格）、
`tools/smoke_p7_frozen.py:112-118`（外壳关键选择器段）。

两点实现事实（写断言前必须知道）：
- `ThemeService.__init__` 只生成用户覆盖层 `config/user.qss`，**不加载令牌与模板**，
  要 `reload()` 之后 `render()` 才有内置样式 ⇒ 夹具必须显式 reload；
- 覆盖层 `user.qss` 是用户自己的文本，不参与 `@token` 替换 ⇒ 令牌类断言只看内置模板。
"""
from __future__ import annotations

import re

import pytest

from core.services import theme_service
from core.services.theme_service import ThemeService

SHELL_SELECTORS = [
    "#titleBar",
    "#titleBarClose",
    "#pluginCard",
    "#settingsNav",
    "#contentCard",
    "#navDropIndicator",
]


@pytest.fixture
def theme(fake_config, themes_dir, tmp_path, monkeypatch):
    # __init__ 会生成用户覆盖层；指向 tmp_path，绝不写真实 src/config/
    monkeypatch.setattr(theme_service, "USER_QSS", tmp_path / "user.qss")
    service = ThemeService(fake_config, themes_dir)
    service.reload()
    return service


@pytest.fixture
def base_qss(theme, themes_dir) -> str:
    """内置模板渲染结果（不含用户覆盖层）。"""
    return theme.render_fragment((themes_dir / "base.qss").read_text(encoding="utf-8"))


def test_available_lists_bundled_themes(theme):
    assert {"light", "dark"} <= set(theme.available())


def test_user_qss_is_created_outside_real_config(theme, tmp_path):
    """用户覆盖层被写到注入的路径（证明测试没有污染 src/config/）。"""
    assert (tmp_path / "user.qss").is_file()


def test_render_keeps_spinbox_arrows(theme):
    """全局给 QSpinBox 加样式后系统不再画箭头 ⇒ 必须显式指定 ::up-arrow/::down-arrow 图片。"""
    qss = theme.render()
    assert "spin-up.svg" in qss
    assert "spin-down.svg" in qss


def test_base_has_no_space_between_number_and_unit(base_qss):
    """Qt 的 CSS 解析器不接受 "9 px"，出现即整条样式被丢弃。"""
    assert not re.search(r"\d\s+px", base_qss)


def test_base_rules_have_no_undefined_token(base_qss):
    """注释里会写 `@token.name` 作说明，故先剥注释：规则体里不该残留未定义令牌。"""
    rules_only = re.sub(r"/\*.*?\*/", "", base_qss, flags=re.S)
    assert not re.search(r"@[A-Za-z0-9_.]+", rules_only)


@pytest.mark.parametrize("selector", SHELL_SELECTORS)
def test_base_template_has_shell_selector(selector, themes_dir):
    assert selector in (themes_dir / "base.qss").read_text(encoding="utf-8"), (
        f"base.qss 缺少外壳选择器段 {selector}"
    )


def test_user_override_is_appended(theme, tmp_path):
    """用户覆盖层追加在内置样式之后（同优先级后者胜出）。"""
    marker = "#pytestOverrideProbe"
    (tmp_path / "user.qss").write_text(f"{marker} {{ color: #123456; }}", encoding="utf-8")
    assert marker in theme.render()


def test_render_fragment_substitutes_tokens_and_units(theme):
    rendered = theme.render_fragment("padding: @spacing.sm px;")
    assert rendered.strip() == "padding: 6px;"
