# core/services/update_service.py
"""检查更新：只读 GitHub Releases API，**纯逻辑、不依赖 Qt / 不依赖界面线程**。

设计取向（见 `work/tasks/2026-11-13-update-check/plan.md`）：
- v1 只做"知道有没有新版本 + 给一个下载入口"，不做下载替换（PyInstaller 目录产物替换风险高）；
- 数据源用现成发版流程（`docs/RELEASE.md` 已经在 Release 上传
  `WordPilot-v<ver>-win64.zip` + SHA256），因此无需自建服务端；
- 只发 GET，请求头只有 `User-Agent` / `Accept`，**不上传任何用户数据**；
- `http` 参数可注入 ⇒ 单测完全离线；将来要换源（自建 manifest）也只换注入实现，不动 UI。

失败一律抛 `UpdateCheckError`（带 `kind`），由调用方决定"静默"还是"给用户看哪句话"。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

REPO = "hui-xu-ai/WordPilot"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_TEMPLATE = "WordPilot-v{version}-win64.zip"

#: Release 正文截断长度（有的版本更新说明几千字，全塞进界面没意义）
NOTES_LIMIT = 4000
#: 自动检查默认间隔（小时）
DEFAULT_INTERVAL_HOURS = 24
#: 手动检查去抖（秒）：连点按钮不重复打网络
MANUAL_COOLDOWN_SECONDS = 10
#: 网络超时（秒）
DEFAULT_TIMEOUT = 6.0

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class UpdateInfo:
    """一次成功检查的结果。"""
    current: str
    latest: str
    has_update: bool
    release_url: str
    download_url: str | None
    notes: str
    published_at: str


class UpdateCheckError(Exception):
    """检查失败。`kind` 让界面给出不同文案（而不是统一的"检查失败"）。"""

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """`v3.2.0` / `3.2.0-beta.1` → `(3, 2, 0)`；非法返回 None（预发布后缀忽略）。"""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def compare_versions(current: str, latest: Any) -> int | None:
    """比较版本：返回 1（有新版本）/ 0（同版）/ -1（对方更旧）；任一侧非法返回 None。"""
    left, right = parse_version(current), parse_version(latest)
    if left is None or right is None:
        return None
    if right > left:
        return 1
    if right < left:
        return -1
    return 0


def pick_asset(assets: Any, version: str) -> str | None:
    """从 Release 附件里挑出这一版的 win64 包下载地址；没有就返回 None。"""
    wanted = ASSET_TEMPLATE.format(version=version).lower()
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name", "")).lower() == wanted:
            return asset.get("browser_download_url") or None
    return None


def _default_http(url: str, timeout: float) -> Any:
    """真实网络请求（惰性导入 requests：让本模块在没有第三方依赖时也能被导入）。"""
    import requests

    return requests.get(url, timeout=timeout, headers={
        "User-Agent": f"WordPilot/{_current_version()}",
        "Accept": "application/vnd.github+json",
    })


def _current_version() -> str:
    try:
        from __version__ import __version__

        return str(__version__)
    except Exception:                      # noqa: BLE001 - 拿不到版本也不该炸
        return "0"


def _raise_for_status(response: Any) -> None:
    status = getattr(response, "status_code", 200)
    if status == 404:
        raise UpdateCheckError("no_release", "还没有发布任何版本")
    if status in (403, 429):
        raise UpdateCheckError("http", "请求过于频繁（GitHub 限流），请稍后再试")
    if status != 200:
        raise UpdateCheckError("http", f"HTTP {status}")


def check_for_update(current: str, *, http: Callable[[str, float], Any] | None = None,
                     timeout: float = DEFAULT_TIMEOUT) -> UpdateInfo:
    """查最新 Release。

    Args:
        current: 本地版本号（`3.2.0`）。
        http: 注入 `http(url, timeout) -> response`；默认走 `requests.get`。
        timeout: 单次请求超时（秒）。

    Raises:
        UpdateCheckError: `timeout` / `network` / `http` / `parse` / `no_release`。
    """
    request = _default_http if http is None else http
    try:
        response = request(API_URL, timeout)
    except UpdateCheckError:
        raise
    except Exception as exc:               # noqa: BLE001 - 网络层异常种类繁多，按名字归类
        kind = "timeout" if "timeout" in type(exc).__name__.lower() else "network"
        raise UpdateCheckError(kind, str(exc)) from exc

    _raise_for_status(response)

    try:
        payload = response.json()
    except Exception as exc:               # noqa: BLE001
        raise UpdateCheckError("parse", f"响应不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateCheckError("parse", "响应不是对象")

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        raise UpdateCheckError("parse", f"tag_name 不可解析：{tag!r}")
    latest = tag.strip().lstrip("v")
    latest = ".".join(str(part) for part in parse_version(tag))

    relation = compare_versions(current, tag)
    if relation is None:
        raise UpdateCheckError("parse", f"本地版本号不可解析：{current!r}")

    notes = payload.get("body") or ""
    notes = str(notes)[:NOTES_LIMIT]
    info = UpdateInfo(
        current=str(current),
        latest=latest,
        has_update=relation > 0,
        release_url=str(payload.get("html_url") or RELEASE_PAGE),
        download_url=pick_asset(payload.get("assets"), latest),
        notes=notes,
        published_at=str(payload.get("published_at") or ""),
    )
    logger.info("检查更新完成：本地 %s / 最新 %s（%s）", info.current, info.latest,
                "有新版本" if info.has_update else "已是最新")
    return info


# ---------------- 自动检查节流 ----------------
def _store(config: Any, key: str, value: Any) -> None:
    """写配置：优先 `config.set()`（ConfigManager 会落盘），否则退回 dict 语义。"""
    setter = getattr(config, "set", None)
    if callable(setter):
        setter(key, value)
        return
    try:
        config[key] = value
    except Exception:                      # noqa: BLE001
        logger.warning("写入更新检查状态失败：%s", key, exc_info=True)


def _read(config: Any, key: str, default: Any) -> Any:
    try:
        value = config.get(key, default)
    except Exception:                      # noqa: BLE001
        return default
    return default if value is None else value


def needs_check(config: Any, *, now: float | None = None) -> bool:
    """现在是否该做自动检查（用户关了自动检查 / 间隔没到 ⇒ False）。"""
    if not _read(config, "auto_check_update", True):
        return False
    try:
        interval_hours = float(_read(config, "update_check_interval_hours",
                                     DEFAULT_INTERVAL_HOURS))
    except (TypeError, ValueError):
        interval_hours = DEFAULT_INTERVAL_HOURS
    if interval_hours <= 0:
        return False
    try:
        last = float(_read(config, "update_last_checked_at", 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    stamp = time.time() if now is None else now
    return (stamp - last) >= interval_hours * 3600


def mark_checked(config: Any, info: UpdateInfo | None = None, *,
                 now: float | None = None) -> None:
    """记录"刚检查过"；发现新版本时记下版本号（用于"只提醒一次"）。

    检查**失败**也要记时间戳，否则断网时会每秒重试。
    """
    _store(config, "update_last_checked_at", time.time() if now is None else now)
    if info is not None and info.has_update:
        _store(config, "update_last_seen_version", info.latest)


def is_unseen_update(config: Any, info: UpdateInfo | None) -> bool:
    """这个新版本是否还没提示过（启动时据此决定要不要在关于页标记）。"""
    if info is None or not info.has_update:
        return False
    return str(_read(config, "update_last_seen_version", "")) != info.latest
