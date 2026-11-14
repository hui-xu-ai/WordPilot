# tests/test_word_bridge_attach.py
"""附着 Word 只查只附，绝不新建实例；并清掉自动化残留的隐藏实例。

2026-11-13 现场（"所有文档都识别不到"）：图片工具箱原先用 `GetObject(Class="Word.Application")`
附着，Word 没跑时它**新建了一个隐藏 Word**（`WINWORD.EXE -Embedding` + `Visible=False`）。
此后 `GetActiveObject` 永远返回这个幽灵实例（里面只有 Image.docx），用户自己打开的 模板.docx
在**另一个** WINWORD 进程里 ⇒ 题注导航 0 题注、引文不弹。
"""
from __future__ import annotations

import pytest

from core.services import word_bridge


class _Doc:
    def __init__(self, name: str, full_name: str):
        self.Name = name
        self.FullName = full_name


class _Docs(list):
    """仿 Word 的 Documents 集合（有 `Count`）。"""

    @property
    def Count(self):                        # noqa: N802 - Word COM 命名
        return len(self)


class _Word:
    def __init__(self, visible: bool, documents):
        self.Visible = visible
        self.Documents = _Docs(documents)
        self.quit_calls: list[int] = []

    def Quit(self, save_changes=0):         # noqa: N802 - Word COM 命名
        self.quit_calls.append(save_changes)


IMAGE = "D:/app/src/config/Image.docx"
PAPER = "D:/docs/模板.docx"


def test_attach_never_creates_word(monkeypatch):
    """`attach_word_app` 只能附着；`GetObject(Class=...)` 会新建隐藏实例，必须不再调用。"""
    import win32com.client

    word = _Word(visible=True, documents=[])

    def _boom(*_args, **_kwargs):
        raise AssertionError("不许用 GetObject(Class=...) 新建 Word 实例")

    monkeypatch.setattr(win32com.client, "GetObject", _boom)
    monkeypatch.setattr(win32com.client, "GetActiveObject", lambda _cls: word)

    assert word_bridge.attach_word_app() is word


def test_attach_raises_when_word_not_running(monkeypatch):
    import win32com.client

    def _fail(_cls):
        raise RuntimeError("没有在运行的 Word")

    monkeypatch.setattr(win32com.client, "GetActiveObject", _fail)
    # 不碰真实的运行对象表（本机可能真开着 Word，那样用例就不隔离了）
    monkeypatch.setattr(word_bridge, "_applications_of_open_documents", lambda *a, **k: [])

    with pytest.raises(word_bridge.WordNotRunningError):
        word_bridge.attach_word_app()


def test_headless_instance_with_only_work_document(monkeypatch):
    monkeypatch.setattr(word_bridge, "_work_documents", set())
    monkeypatch.setattr(word_bridge, "_is_workspace_document", lambda _doc: False)
    word_bridge.register_work_document(IMAGE)

    assert word_bridge._is_headless_word(_Word(False, [_Doc("Image.docx", IMAGE)])) is True


def test_headless_false_when_visible(monkeypatch):
    monkeypatch.setattr(word_bridge, "_work_documents", set())
    word_bridge.register_work_document(IMAGE)

    assert word_bridge._is_headless_word(_Word(True, [_Doc("Image.docx", IMAGE)])) is False


def test_headless_false_when_user_document_open(monkeypatch):
    monkeypatch.setattr(word_bridge, "_work_documents", set())
    monkeypatch.setattr(word_bridge, "_is_workspace_document", lambda _doc: False)
    word_bridge.register_work_document(IMAGE)

    word = _Word(False, [_Doc("Image.docx", IMAGE), _Doc("模板.docx", PAPER)])

    assert word_bridge._is_headless_word(word) is False, "有用户文档就绝不能碰"


def test_headless_true_for_unregistered_config_document(monkeypatch):
    """工具箱还没登记时（页面惰性构造），靠"文档在 config/ 下"也能认出来。"""
    monkeypatch.setattr(word_bridge, "_work_documents", set())
    monkeypatch.setattr(word_bridge, "_is_workspace_document",
                        lambda doc: _normalize(doc.FullName).startswith(_normalize("D:/app/src/config")))

    assert word_bridge._is_headless_word(_Word(False, [_Doc("Image.docx", IMAGE)])) is True


def _normalize(path: str) -> str:
    import os

    return os.path.normcase(os.path.normpath(path))


def test_get_word_app_recycles_headless_instance_once(monkeypatch):
    """幽灵实例被退出一次，随后附着到用户的 Word。"""
    monkeypatch.setattr(word_bridge, "_work_documents", set())
    monkeypatch.setattr(word_bridge, "_is_workspace_document", lambda _doc: False)
    monkeypatch.setattr(word_bridge, "_headless_checked", False)
    # 不碰真实运行对象表/前台窗口：本机真开着 Word 时用例必须依旧隔离
    monkeypatch.setattr(word_bridge, "_applications_of_open_documents", lambda *a, **k: [])
    monkeypatch.setattr(word_bridge, "_foreground_word_pid", lambda: 0)
    word_bridge.register_work_document(IMAGE)

    phantom = _Word(False, [_Doc("Image.docx", IMAGE)])
    user = _Word(True, [_Doc("模板.docx", PAPER)])
    attached = iter([phantom, user])
    monkeypatch.setattr(word_bridge, "_attach_word", lambda: next(attached))

    assert word_bridge.get_word_app() is user
    assert phantom.quit_calls == [0], "退出隐藏实例时必须不保存改动"


def test_dead_attachment_is_skipped(monkeypatch):
    """刚 `Quit()` 的 Word 会留下"取属性就抛 AttributeError"的死对象，必须跳过它。"""

    class _Dead:
        @property
        def Documents(self):
            raise AttributeError("Word.Application.Documents")

    user = _Word(True, [_Doc("模板.docx", PAPER)])
    attached = iter([_Dead(), user])
    monkeypatch.setattr(word_bridge, "_attach_word", lambda: next(attached))

    assert word_bridge._attach_word_ready(timeout=1.0) is user


def test_get_word_app_skips_dead_object_and_uses_open_document(monkeypatch):
    """Word 退出/重启后注册表里留着死对象时，改用"已打开文档"所属的活实例。

    用户现场（2026-11-13）：Word 关掉后每秒一条 `AttributeError: Word.Application.Windows`，
    题录离线自动更新与引文识别全部静默失效 —— 因为死对象被当成了可用的 Word。
    """

    class _Dead:
        @property
        def Documents(self):
            raise AttributeError("Word.Application.Documents")

    live = _Word(True, [_Doc("模板.docx", PAPER)])
    monkeypatch.setattr(word_bridge, "_attach_word", lambda: _Dead())
    monkeypatch.setattr(word_bridge, "_applications_of_open_documents", lambda *a, **k: [live])
    monkeypatch.setattr(word_bridge, "_headless_checked", True)

    assert word_bridge.get_word_app() is live


def test_get_word_app_raises_when_only_dead_object_left(monkeypatch):
    """只剩死对象时宁可按"Word 未运行"报错，也不能把死对象交出去。"""

    class _Dead:
        @property
        def Documents(self):
            raise AttributeError("Word.Application.Documents")

    monkeypatch.setattr(word_bridge, "_attach_word", lambda: _Dead())
    monkeypatch.setattr(word_bridge, "_applications_of_open_documents", lambda *a, **k: [])
    monkeypatch.setattr(word_bridge, "_headless_checked", True)

    with pytest.raises(word_bridge.WordNotRunningError):
        word_bridge.get_word_app()


def test_get_word_app_prefers_foreground_word_process(monkeypatch):
    """多实例：必须选"前台 Word 窗口所属进程"那个实例，而不是注册表里先到的那个。

    用户现场：EndNote 那篇能自动刷新、模板.docx 完全识别不到 —— 就是挑错了实例。
    """
    app_a = _Word(True, [_Doc("EndNote.docx", "C:/docs/EndNote.docx")])
    app_b = _Word(True, [_Doc("模板.docx", PAPER)])
    monkeypatch.setattr(word_bridge, "_attach_word", lambda: app_a)
    monkeypatch.setattr(word_bridge, "_applications_of_open_documents", lambda *a, **k: [app_b])
    monkeypatch.setattr(word_bridge, "_application_pid",
                        lambda application: 111 if application is app_a else 222)
    monkeypatch.setattr(word_bridge, "_foreground_word_pid", lambda: 222)

    assert word_bridge.get_word_app() is app_b


def test_get_word_app_falls_back_to_instance_with_visible_document(monkeypatch):
    """前台不是 Word 时（用户点了 WordPilot 的按钮），选"有可见用户文档"的实例。"""
    app_a = _Word(True, [_Doc("EndNote.docx", "C:/docs/EndNote.docx")])
    app_b = _Word(True, [_Doc("模板.docx", PAPER)])
    monkeypatch.setattr(word_bridge, "_attach_word", lambda: app_a)
    monkeypatch.setattr(word_bridge, "_applications_of_open_documents", lambda *a, **k: [app_b])
    monkeypatch.setattr(word_bridge, "_application_pid",
                        lambda application: 111 if application is app_a else 222)
    monkeypatch.setattr(word_bridge, "_foreground_word_pid", lambda: 0)
    monkeypatch.setattr(word_bridge, "_has_visible_user_document",
                        lambda application: application is app_b)

    assert word_bridge.get_word_app() is app_b


class _DeadApp:
    @property
    def Documents(self):
        raise AttributeError("Word.Application.Documents")


class _BoundDocument:
    """伪造 `win32com.client.GetObject(路径)` 的返回值：Document + 它所属的 Application。"""

    def __init__(self, document: _Doc, application):
        self.FullName = document.FullName
        self.Name = document.Name
        self.Application = application


def test_attach_from_documents_skips_work_document(monkeypatch):
    """图片工具箱的草稿文档不算"用户的 Word 文档"，不能拿它当附着入口。"""
    monkeypatch.setattr(word_bridge, "_work_documents", set())
    word_bridge.register_work_document(IMAGE)
    live = _Word(True, [_Doc("模板.docx", PAPER)])
    documents = {
        IMAGE: _BoundDocument(_Doc("Image.docx", IMAGE), live),
        PAPER: _BoundDocument(_Doc("模板.docx", PAPER), live),
    }

    application = word_bridge._attach_from_documents(
        paths=[IMAGE, PAPER], opener=lambda path: documents[path])

    assert application is live


def test_attach_from_documents_skips_dead_application(monkeypatch):
    """第一篇文档所属的应用是死对象时，继续试下一篇（僵尸注册表的典型形态）。"""
    monkeypatch.setattr(word_bridge, "_work_documents", set())
    live = _Word(True, [_Doc("模板.docx", PAPER)])
    documents = {
        "C:/docs/旧.docx": _BoundDocument(_Doc("旧.docx", "C:/docs/旧.docx"), _DeadApp()),
        PAPER: _BoundDocument(_Doc("模板.docx", PAPER), live),
    }

    application = word_bridge._attach_from_documents(
        paths=["C:/docs/旧.docx", PAPER], opener=lambda path: documents[path])

    assert application is live


def test_attach_from_documents_returns_none_when_nothing_alive(monkeypatch):
    monkeypatch.setattr(word_bridge, "_work_documents", set())

    assert word_bridge._attach_from_documents(paths=[], opener=lambda _p: None) is None


def test_recycle_happens_only_once(monkeypatch):
    monkeypatch.setattr(word_bridge, "_work_documents", set())
    monkeypatch.setattr(word_bridge, "_is_workspace_document", lambda _doc: False)
    monkeypatch.setattr(word_bridge, "_headless_checked", False)
    # 不碰真实运行对象表/前台窗口：本机真开着 Word 时用例必须依旧隔离
    monkeypatch.setattr(word_bridge, "_applications_of_open_documents", lambda *a, **k: [])
    monkeypatch.setattr(word_bridge, "_foreground_word_pid", lambda: 0)
    word_bridge.register_work_document(IMAGE)

    phantom = _Word(False, [_Doc("Image.docx", IMAGE)])
    monkeypatch.setattr(word_bridge, "_attach_word", lambda: phantom)

    word_bridge.get_word_app()                      # 第一次：退出幽灵实例
    word_bridge.get_word_app()                      # 第二次：不再重复退出

    assert phantom.quit_calls == [0]
