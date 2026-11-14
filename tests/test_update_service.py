# tests/test_update_service.py
"""更新检查（离线单测）：版本比较、错误分类、资产选择、节流。

为什么全是假 http：更新检查会打真实网络，CI/无网机器上必须不依赖外网，
同时要能覆盖"超时/连接失败/403 限流/404 还没发版/JSON 缺字段"这些真实会遇到的失败。
"""
from __future__ import annotations

import json

import pytest

from core.services import update_service as us


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _release(tag="v3.3.0", assets=None, notes="修复若干问题", published="2026-11-01T10:00:00Z"):
    if assets is None:
        assets = [{"name": f"WordPilot-{tag}-win64.zip",
                   "browser_download_url": "https://example.invalid/pkg.zip"}]
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/hui-xu-ai/WordPilot/releases/tag/{tag}",
        "body": notes,
        "published_at": published,
        "assets": assets,
    }


# ---------------- 版本比较 ----------------
@pytest.mark.parametrize(("latest", "expected"), [
    ("v3.3.0", 1),
    ("3.3.0", 1),
    ("3.2.0", 0),
    ("v3.2.0", 0),
    ("3.1.9", -1),
    ("v3.2.0-beta.1", 0),      # 预发布按同版处理（v1 不做预发布通道）
    ("3.10.0", 1),             # 字符串比较会错：3.10 < 3.9
    ("2.99.99", -1),
])
def test_compare_versions(latest, expected):
    assert us.compare_versions("3.2.0", latest) == expected


@pytest.mark.parametrize("bad", ["", "abc", "v3", "3.2", "3.2.x", None])
def test_compare_versions_rejects_garbage(bad):
    assert us.compare_versions("3.2.0", bad) is None


# ---------------- 检查更新 ----------------
def test_check_reports_newer_release():
    info = us.check_for_update("3.2.0", http=lambda _url, _timeout: _Resp(_release()))

    assert info.current == "3.2.0"
    assert info.latest == "3.3.0"
    assert info.has_update is True
    assert info.download_url == "https://example.invalid/pkg.zip"
    assert info.release_url.endswith("/releases/tag/v3.3.0") or "/releases" in info.release_url
    assert "修复若干问题" in info.notes


def test_check_on_same_version_has_no_update():
    info = us.check_for_update("3.2.0", http=lambda _u, _t: _Resp(_release(tag="v3.2.0")))
    assert info.has_update is False
    assert info.latest == "3.2.0"


def test_check_with_older_release_has_no_update():
    info = us.check_for_update("3.9.0", http=lambda _u, _t: _Resp(_release(tag="v3.2.0")))
    assert info.has_update is False


def test_check_without_matching_asset_falls_back_to_release_page():
    info = us.check_for_update("3.2.0", http=lambda _u, _t: _Resp(_release(assets=[])))
    assert info.has_update is True
    assert info.download_url is None
    assert info.release_url


def test_check_truncates_long_notes():
    info = us.check_for_update("3.2.0", http=lambda _u, _t: _Resp(_release(notes="长" * 9000)))
    assert len(info.notes) <= us.NOTES_LIMIT


def test_check_timeout_kind():
    def http(_url, _timeout):
        raise TimeoutError("timed out")

    with pytest.raises(us.UpdateCheckError) as err:
        us.check_for_update("3.2.0", http=http)
    assert err.value.kind == "timeout"


def test_check_network_kind():
    def http(_url, _timeout):
        raise OSError("no route to host")

    with pytest.raises(us.UpdateCheckError) as err:
        us.check_for_update("3.2.0", http=http)
    assert err.value.kind == "network"


def test_check_rate_limited_kind():
    with pytest.raises(us.UpdateCheckError) as err:
        us.check_for_update("3.2.0", http=lambda _u, _t: _Resp({}, status_code=403))
    assert err.value.kind == "http"
    assert "频繁" in str(err.value) or "rate" in str(err.value).lower()


def test_check_no_release_kind():
    with pytest.raises(us.UpdateCheckError) as err:
        us.check_for_update("3.2.0", http=lambda _u, _t: _Resp({}, status_code=404))
    assert err.value.kind == "no_release"


def test_check_bad_json_kind():
    with pytest.raises(us.UpdateCheckError) as err:
        us.check_for_update("3.2.0", http=lambda _u, _t: _Resp(json.JSONDecodeError("x", "y", 0)))
    assert err.value.kind == "parse"


def test_check_missing_tag_kind():
    with pytest.raises(us.UpdateCheckError) as err:
        us.check_for_update("3.2.0", http=lambda _u, _t: _Resp({"body": "no tag"}))
    assert err.value.kind == "parse"


# ---------------- 节流 ----------------
class _Cfg(dict):
    """假配置：dict 语义 + set() 记录写盘（ConfigManager 的 set 会落盘，这里只记录）。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_calls: list[tuple[str, object]] = []

    def set(self, key, value):
        self.set_calls.append((key, value))
        self[key] = value


def test_needs_check_when_never_checked():
    assert us.needs_check(_Cfg(), now=1_000_000.0) is True


def test_needs_check_respects_interval():
    cfg = _Cfg(update_last_checked_at=1_000_000.0)
    assert us.needs_check(cfg, now=1_000_000.0 + 3600) is False
    assert us.needs_check(cfg, now=1_000_000.0 + 25 * 3600) is True


def test_needs_check_respects_auto_flag_and_interval_override():
    assert us.needs_check(_Cfg(auto_check_update=False), now=1e9) is False
    # 从没查过 ⇒ 无论间隔设多少都该查
    assert us.needs_check(_Cfg(update_check_interval_hours=1), now=1_000_000.0 + 3700) is True
    cfg = _Cfg(update_last_checked_at=1_000_000.0, update_check_interval_hours=1)
    assert us.needs_check(cfg, now=1_000_000.0 + 3700) is True
    assert us.needs_check(cfg, now=1_000_000.0 + 600) is False


def test_mark_checked_persists_timestamp_and_seen_version():
    cfg = _Cfg()
    info = us.check_for_update("3.2.0", http=lambda _u, _t: _Resp(_release()))
    us.mark_checked(cfg, info, now=1_234_567.0)

    assert cfg["update_last_checked_at"] == 1_234_567.0
    assert cfg["update_last_seen_version"] == "3.3.0"
    assert ("update_last_checked_at", 1_234_567.0) in cfg.set_calls, "必须走 set() 落盘"


def test_mark_checked_on_failure_only_stamps_time():
    cfg = _Cfg(update_last_seen_version="3.3.0")
    us.mark_checked(cfg, None, now=42.0)

    assert cfg["update_last_checked_at"] == 42.0
    assert cfg["update_last_seen_version"] == "3.3.0", "检查失败不该清掉已知的新版本"


def test_unseen_update_only_true_for_new_version():
    info = us.check_for_update("3.2.0", http=lambda _u, _t: _Resp(_release()))

    assert us.is_unseen_update(_Cfg(), info) is True
    assert us.is_unseen_update(_Cfg(update_last_seen_version="3.3.0"), info) is False
    same = us.check_for_update("3.3.0", http=lambda _u, _t: _Resp(_release()))
    assert us.is_unseen_update(_Cfg(), same) is False
