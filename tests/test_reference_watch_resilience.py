"""回归：监听线程单次循环不能因为签名不一致/异常而整条死掉（这次"完全不弹窗"的真因）。"""
from __future__ import annotations

import pytest

from plugins.reference.doc_index import DocIndex
from plugins.reference.word_watch import WordWatch


class _Text:
    def __init__(self, text: str):
        self.Text = text


class _Range:
    def __init__(self, start: int, end: int, text: str = ""):
        self.Start, self.End, self.Text = start, end, text


class _Field:
    def __init__(self, code: str, result: str, start: int, end: int):
        self.Code = _Text(code)
        self.Result = _Range(start, end, result)


class _Fields:
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def __call__(self, index):
        return self._items[index - 1]


class _Doc:
    Name = "论文.docx"
    FullName = "C:/tmp/论文.docx"

    def __init__(self, fields=None, fail_fields: bool = False):
        self._fail = fail_fields
        self.Fields = _Fields(fields or [])

    @property
    def Fields(self):                       # noqa: N802 - 模拟 COM 属性
        if self._fail:
            raise RuntimeError("读取域集合失败")
        return self._fields

    @Fields.setter
    def Fields(self, value):                # noqa: N802
        self._fields = value

    def Range(self, start, end):
        return _Range(start, end, "测试[5,6]")


class _Selection:
    Text = "[5,6]"
    Start = 4
    End = 4

    def Information(self, kind):
        return 100

    def Range(self, start, end):
        return _Range(start, end, "测试[5,6]")


class _Word:
    def __init__(self, doc):
        self.ActiveDocument = doc
        self.Selection = _Selection()

    def Range(self, start, end):
        return _Range(start, end, "测试[5,6]")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_tick_emits_all_labels_for_multi_citation(qapp):
    """`[5,6]` 必须一次给出两条 —— 顺带锁住 `_tick` 与 `_anchor_payload` 的签名一致。"""
    doc = _Doc([_Field(" ADDIN EN.JS.CITE ", "[5,6]", 2, 8)])
    word = _Word(doc)
    watch = WordWatch()
    seen: list[dict] = []
    watch.citationFound.connect(seen.append)

    watch._tick(lambda: word)      # 第一次：建索引
    watch._tick(lambda: word)      # 第二次：命中并上报

    assert seen, "命中引用必须发出信号（否则界面上就是'点了没弹窗'）"
    labels = [item["label"] for item in seen[-1]["items"]]
    assert labels == ["5", "6"]


def test_tick_does_not_raise_when_fields_unavailable(qapp):
    """域集合读不到时也不能抛异常把线程打死，且要记住文档名（否则每轮都重扫）。"""
    word = _Word(_Doc(fail_fields=True))
    watch = WordWatch()
    watch._tick(lambda: word)
    assert watch._index.doc_name == "论文.docx", "拿不到域也要记住文档名，避免无限重扫"
    watch._tick(lambda: word)      # 第二轮不应再重扫、也不应抛异常


def test_tick_keeps_working_when_label_parsing_fails(qapp, monkeypatch):
    """即便角标解析抛异常，单次循环也要被兜住（有 try/except），线程不能死。"""
    import plugins.reference.word_watch as wm

    def boom(*_args, **_kwargs):
        raise RuntimeError("解析炸了")

    monkeypatch.setattr(wm, "labels_at_position", boom)
    word = _Word(_Doc([_Field(" ADDIN EN.JS.CITE ", "[5]", 2, 8)]))
    watch = WordWatch()
    watch._tick(lambda: word)
    watch._tick(lambda: word)


def test_doc_index_retries_after_field_failure():
    """读域失败时**不能**标记成已扫描：否则这份文档永远不再重扫（点引用不弹窗）。"""
    index = DocIndex()
    assert index.rescan(_Word(_Doc(fail_fields=True))) == 0
    assert index.doc_name == "论文.docx", "拿不到域也要记住文档名"
    assert index.needs_rescan("C:/tmp/论文.docx") is True, "失败后下一轮必须允许重扫"


class _CacheStub:
    """缓存永远命中：模拟"切回文档直接装载锚点"（用户现场）。"""

    def __init__(self, anchors):
        self._anchors = anchors
        self.saved = 0

    def load_anchors(self, _path, _fp):
        return list(self._anchors)

    def save_anchors(self, *_args, **_kwargs):
        self.saved += 1


def test_cached_load_records_signature_so_lookup_happens(qapp):
    """缓存装载后必须记录内容指纹，否则每轮都重扫并 return，永远走不到"提取编号"。

    现场证据（app.log 00:33–00:39）：每轮都是
    `阶段=rescan 需重扫=True 存储指纹=(0, 0)` → 点了引文 ②③④ 全不出现。
    """
    from plugins.reference.doc_index import DocAnchor

    doc = _Doc([_Field(" ADDIN EN.JS.CITE ", "[2]", 4, 9)])
    word = _Word(doc)
    cached = [DocAnchor(start=4, end=9, anchor_key="doc:论文.docx:cite:2",
                        label="2", kind="cite")]
    watch = WordWatch(cache=_CacheStub(cached))
    seen: list[dict] = []
    watch.citationFound.connect(seen.append)

    watch._tick(lambda: word)          # 第一轮：装载缓存（不走重扫）
    assert watch._index.signature != (0, 0), "装载后要记住实时指纹"
    watch._tick(lambda: word)          # 第二轮：必须进入命中路径

    assert seen, "缓存装载后第二轮必须能命中（否则永远提取不到编号）"
    assert seen[-1]["items"][0]["label"] == "2"


def test_rescan_tick_does_not_close_visible_card(qapp):
    """重扫的那一轮不能算"光标离开引用"：否则卡片弹出来 0.6 秒就自己关掉。

    用户现象：切文档后弹窗"闪一下就没了"（切文档会连着重扫几轮）。
    """
    import time as _time

    doc = _Doc([_Field(" ADDIN EN.JS.CITE ", "[2]", 4, 9)])
    word = _Word(doc)
    watch = WordWatch()
    watch._tick(lambda: word)              # 建索引
    watch._tick(lambda: word)              # 命中 → 等于"卡片已显示"

    cleared: list[int] = []
    watch.citationCleared.connect(lambda: cleared.append(1))
    watch._index.clear()                   # 模拟切文档 / 手动重置索引
    watch._last_hit_at = _time.perf_counter() - 1.0     # 已超过 CLEAR_AFTER_MS
    watch._tick(lambda: word)              # 这一轮走重扫分支

    assert not cleared, "重扫不是 miss，不能把已显示的卡片关掉"


def test_genuine_miss_still_closes_card(qapp):
    """真正的 miss（光标离开引用）仍要关闭卡片，别把上面那条改成"永不关闭"。"""
    import time as _time

    doc = _Doc([_Field(" ADDIN EN.JS.CITE ", "[2]", 4, 9)])
    word = _Word(doc)
    watch = WordWatch()
    watch._tick(lambda: word)              # 建索引
    watch._tick(lambda: word)              # 命中

    cleared: list[int] = []
    watch.citationCleared.connect(lambda: cleared.append(1))
    word.Selection.Start = word.Selection.End = 500     # 光标移到没有引用的地方
    word.Selection.Text = "普通文字"                      # 选区里也没有角标
    doc.Range = lambda start, end: _Range(start, end, "远处没有角标")
    watch._last_hit_at = _time.perf_counter() - 1.0
    watch._tick(lambda: word)

    assert cleared, "光标离开引用后必须关闭卡片"


def test_stale_cached_anchors_self_heal_on_first_miss(qapp):
    """索引里的锚点位置与文档不符时，第一次"有角标但不匹配"就重扫，不劳用户点同步。

    实测现场（app.log 21:47:52）：`光标处锚点=0 索引锚点=0 已扫=True 需重扫=False`
    → 空/过期索引被当成健康状态 → 只能点「同步本文」。这里直接摆出那个状态。
    """
    from plugins.reference.doc_index import DocAnchor, document_signature

    doc = _Doc([_Field(" ADDIN EN.JS.CITE ", "[2]", 4, 9)])
    doc.Range = lambda start, end: _Range(start, end, " " * 5 + "[2]")   # 光标正落在角标上
    word = _Word(doc)
    stale = [DocAnchor(start=9000, end=9005, anchor_key="doc:论文.docx:cite:2",
                       label="2", kind="cite")]
    watch = WordWatch()
    watch._index.load("C:/tmp/论文.docx", stale)
    watch._index.signature = document_signature(doc)     # 指纹对得上 ⇒ needs_rescan 为假
    seen: list[dict] = []
    watch.citationFound.connect(seen.append)

    watch._tick(lambda: word)

    assert watch._index.anchors[0].start == 4, "过期锚点必须被重扫替换"
    assert seen, "自愈后必须当场弹出（否则用户还得点「同步本文」）"
    assert seen[-1]["items"][0]["label"] == "2"


def test_empty_index_is_not_treated_as_healthy(qapp):
    """索引为空但"已扫过"时必须自愈重扫（实测 21:47:52 就是这个状态卡住用户）。"""
    from plugins.reference.doc_index import document_signature

    doc = _Doc([_Field(" ADDIN EN.JS.CITE ", "[2]", 4, 9)])
    word = _Word(doc)
    watch = WordWatch()
    watch._index.load("C:/tmp/论文.docx", [])            # 空表也标成"已扫过"（缓存装载的真实行为）
    watch._index.signature = document_signature(doc)
    assert watch._index.anchors == []
    seen: list[dict] = []
    watch.citationFound.connect(seen.append)

    watch._tick(lambda: word)

    assert watch._index.anchors, "空索引必须被重扫（否则这篇文档永远是空索引）"
    assert seen, "重扫后应立刻弹出"
