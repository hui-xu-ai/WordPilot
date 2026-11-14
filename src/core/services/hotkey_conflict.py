# core/services/hotkey_conflict.py
"""查询 Word 是否占用了某个组合键（**只读**，且绝不新建 Word 进程）。

为什么需要这个模块：
热键通过 `keyboard` 的全局钩子注册，且**不拦截按键**（`suppress=False`），所以按键会同时送进
前台程序。Word 的 `Ctrl+Shift+E` 正是「修订模式开关」——用户按我们的「切换校对」时，
文档会静默进入修订模式（改过正文却没人察觉，对论文写作是很严重的副作用）。
这里让 Word 自己回答"这个键绑了什么"（`Application.BuildKeyCode` + `FindKey`），从而做到：
默认键可验证、用户改键时可检测、启动时可自检。

约束：Word 没在运行就返回 None（**不** `Dispatch`/`GetObject(Class=…)` 新建实例 ——
那会留下一个永不退出的隐藏 Word 并长期占住运行对象表）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Word 的 WdKey 修饰键常量（取自 Word 类型库实测：Shift 是 256，不是 528）
WD_KEY_CONTROL = 512
WD_KEY_SHIFT = 256
WD_KEY_ALT = 1024

_MODIFIERS = {"ctrl": WD_KEY_CONTROL, "control": WD_KEY_CONTROL,
              "shift": WD_KEY_SHIFT, "alt": WD_KEY_ALT}

#: 组合键里"主键"→ Word 键码
_KEYCODES: dict[str, int] = {chr(code).lower(): code for code in range(65, 91)}   # a..z
_KEYCODES.update({str(n): 48 + n for n in range(10)})                            # 0..9
_KEYCODES.update({f"f{n}": 111 + n for n in range(1, 13)})                       # f1..f12
_KEYCODES.update({"space": 32, "tab": 9, "enter": 13, "esc": 27, "escape": 27})

#: 会被写进界面的"这个键在 Word 里干什么"（只做展示，判定只看命令是否为空）
COMMAND_NAMES = {
    "ToolsRevisionMarksToggle": "修订模式开关",
    "ResetChar": "清除格式",
    "FormatAutoFormat": "自动套用格式",
    "MailMergeEditDataSource": "邮件合并数据源",
}

#: assess() 的状态
STATUS_FREE = "free"          # Word 里无动作，可放心用
STATUS_CONFLICT = "conflict"  # Word 里有实际命令（可能改文档状态）
STATUS_UNKNOWN = "unknown"    # 检测不到（Word 未运行 / 组合键非法 / 接口不可用）


@dataclass(frozen=True)
class WordBinding:
    """Word 对某个组合键的绑定。"""

    command: str
    key_string: str

    @property
    def is_risky(self) -> bool:
        """有非空命令就算有副作用 —— 连插入 `€` 这类符号也算（会写进文档）。"""
        return bool((self.command or "").strip())


def normalize_combo(combo: str) -> str:
    """`Ctrl + Shift + Alt + E` → `ctrl+alt+shift+e`（去空格、小写、修饰键固定顺序）。"""
    tokens = [token.strip().lower() for token in str(combo or "").replace(" ", "").split("+")]
    tokens = [token for token in tokens if token]
    order = ("ctrl", "alt", "shift")
    mods = [m for m in order if m in tokens or (m == "ctrl" and "control" in tokens)]
    rest = [token for token in tokens if token not in _MODIFIERS]
    return "+".join(mods + rest)


def parse_combo(combo: str) -> tuple[list[str], str] | None:
    """拆成（修饰键, 主键）；主键不认识就返回 None。"""
    normalized = normalize_combo(combo)
    if not normalized:
        return None
    tokens = normalized.split("+")
    mods = [token for token in tokens if token in _MODIFIERS]
    keys = [token for token in tokens if token not in _MODIFIERS]
    if not keys or keys[-1] not in _KEYCODES:
        return None
    return [("ctrl" if m == "control" else m) for m in mods], keys[-1]


def is_valid_combo(combo: str) -> bool:
    """至少要带 Ctrl 或 Alt：只有 Shift+字母/纯字母的"全局热键"会抢掉所有程序的正常输入。"""
    parsed = parse_combo(combo)
    if parsed is None:
        return False
    mods, _key = parsed
    return bool({"ctrl", "alt"} & set(mods))


def word_binding(combo: str, *, word=None) -> WordBinding | None:
    """Word 里这个组合键实际绑了什么；None = 查不到（Word 未运行 / 组合键非法 / 接口不可用）。"""
    parsed = parse_combo(combo)
    if parsed is None:
        return None
    mods, key = parsed

    app = word
    if app is None:
        try:
            import win32com.client

            app = win32com.client.GetActiveObject("Word.Application")   # 只附不建
        except Exception:                  # noqa: BLE001 - Word 没开是最常见的情况
            logger.debug("Word 未运行，跳过快捷键冲突检测", exc_info=True)
            return None

    try:
        code = int(app.BuildKeyCode(*[_MODIFIERS[m] for m in mods], _KEYCODES[key]))
    except Exception:                      # noqa: BLE001
        logger.info("Word BuildKeyCode 不可用，跳过冲突检测", exc_info=True)
        return None
    if not code:                           # 该组合键在 Word 里不合法
        return None
    try:
        binding = app.FindKey(KeyCode=code)
    except Exception:                      # noqa: BLE001
        logger.info("Word FindKey 查询失败，跳过冲突检测", exc_info=True)
        return None
    if binding is None:
        return None
    return WordBinding(command=str(getattr(binding, "Command", "") or ""),
                       key_string=str(getattr(binding, "KeyString", "") or ""))


def assess(combo: str, *, word=None) -> dict:
    """给界面/日志用的结论。

    Returns:
        `{"status": free|conflict|unknown, "combo": 归一化键, "command": str,
          "human": 中文动作名（有就用）, "detail": 给人看的说明}`
    """
    normalized = normalize_combo(combo)
    if parse_combo(normalized) is None:
        return {"status": STATUS_UNKNOWN, "combo": normalized, "command": "",
                "human": "", "detail": "组合键无法识别"}
    binding = word_binding(normalized, word=word)
    if binding is None:
        return {"status": STATUS_UNKNOWN, "combo": normalized, "command": "",
                "human": "", "detail": "检测不到（Word 未运行或该组合键无效）"}
    human = COMMAND_NAMES.get(binding.command, "")
    if binding.is_risky:
        return {"status": STATUS_CONFLICT, "combo": normalized, "command": binding.command,
                "human": human, "detail": f"Word: {human or binding.command}"}
    return {"status": STATUS_FREE, "combo": normalized, "command": "", "human": "",
            "detail": "Word: 无动作"}
