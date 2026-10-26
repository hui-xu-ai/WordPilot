"""校对状态必须区分：文本过短、API失败和检查无错；不触碰真实Word/API/缓存。"""
from types import SimpleNamespace

import pytest


@pytest.fixture
def monitor_module(monkeypatch):
    from plugins.proofread import word_monitor as module

    monkeypatch.setattr(module, "get_default_cache", lambda: SimpleNamespace(get=lambda key: None))
    monkeypatch.setattr(module, "TokenizedSentence", lambda span: SimpleNamespace(get_text_for_api=lambda: span.Text))
    return module


def test_api_failure_is_not_reported_as_unchanged_text(monitor_module):
    span = SimpleNamespace(Text="这是一个测试句子。", Start=0, End=9)
    api = SimpleNamespace(last_error="HTTP 401", proof_text=lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        monitor_module.proof_sentence(span, api, {})


def test_missing_api_does_not_report_success(monitor_module):
    span = SimpleNamespace(Text="这是一个测试句子。", Start=0, End=9)
    with pytest.raises(RuntimeError):
        monitor_module.proof_sentence(span, None, {})


def test_short_input_remains_a_non_failure_skip(monitor_module):
    span = SimpleNamespace(Text="短句。", Start=0, End=3)
    api = SimpleNamespace(proof_text=lambda *a, **k: pytest.fail("短句不应请求API"))
    assert monitor_module.proof_sentence(span, api, {}) == (None, None, None, None)


def test_real_unchanged_response_is_a_success(monitor_module):
    text = "这是一个测试句子。"
    span = SimpleNamespace(Text=text, Start=0, End=len(text))
    api = SimpleNamespace(proof_text=lambda *a, **k: text)
    assert monitor_module.proof_sentence(span, api, {}) == (text, text, 0, len(text))


def test_word_poll_failure_is_not_a_new_proofing_result(monitor_module, monkeypatch):
    monitor = monitor_module.WordMonitor({}, None)
    failures = []
    monitor.proofing_failed.connect(failures.append)
    monkeypatch.setattr(monitor_module, "get_word_app", lambda: (_ for _ in ()).throw(RuntimeError("Word closed")))
    monkeypatch.setattr(monitor_module.time, "sleep", lambda seconds: setattr(monitor, "running", False))
    monitor._run_loop()
    assert failures == [], "未发起校对时的Word轮询错误不得清掉待处理建议状态"


def test_automatic_api_failure_emits_failure_but_no_suggestion(monitor_module, monkeypatch):
    text = "这是一个测试句子。"
    span = SimpleNamespace(Text=text, Start=0, End=len(text),
                           Characters=SimpleNamespace(Last=SimpleNamespace(Text="。")))
    span.Duplicate = span
    word = SimpleNamespace(Selection=SimpleNamespace(Range=span))
    api = SimpleNamespace(last_error="HTTP 401", proof_text=lambda *a, **k: None)
    monitor = monitor_module.WordMonitor({}, api)
    monitor.sentence_state[0] = ("这是一个测试句子", False)
    failures, suggestions, started = [], [], []
    monitor.proofing_failed.connect(failures.append)
    monitor.suggestion_ready.connect(lambda *args: suggestions.append(args))
    monitor.proofing_started.connect(lambda: started.append(True))
    monkeypatch.setattr(monitor_module, "get_word_app", lambda: word)
    monkeypatch.setattr(monitor_module, "get_sentence_range", lambda selection: span)
    monkeypatch.setattr(monitor_module.time, "sleep", lambda seconds: setattr(monitor, "running", False))
    monitor._run_loop()
    assert started == [True]
    assert failures == ["HTTP 401"]
    assert suggestions == []
