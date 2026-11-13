# plugins/navigator/caption_item.py
"""题录条目（从 word_navigator_dialog.py 抽出，供面板与扫描/跳转 mixin 共用，避免循环导入）。"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: 题注编号前缀（"图 1.1" / "表 2-3" / "Figure 1.1"）：离线来源定位时要去掉，
#: 因为 Word 里的编号常由 SEQ 域渲染，按"图 1.1"整串搜不到
_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:图|表|Figure|Table)\s*[0-9０-９]+(?:[.\-－．_][0-9０-９]+)*\s*")

#: 序列域里表示"表"与"图"的关键词。中英文都要认：中文学位论文写 `SEQ 图`/`SEQ 表`，
#: 英文模板与 EndNote 导出写 `SEQ figure`/`SEQ table` —— 只认中文会让 COM 刷新扫出 0 条，
#: 把离线扫描已经拿到的题录整份清空（用户现场：点「刷新列表」后题录全没了）。
_TABLE_SEQ_HINTS = ("表", "TABLE")
_FIGURE_SEQ_HINTS = ("图", "FIGURE")


def caption_kind(field_code) -> str | None:
    """按序列域指令判断题注种类：`table` / `figure` / None（不是题注域）。

    离线扫描（docx 的 `instrText`）与 COM 刷新（`Field.Code.Text`）共用这一份判定，
    避免两边口径不一致（一边认出 2 条、另一边 0 条，刷新就把列表清空了）。
    `SEQ chapter \\c`（章号）、`SEQ 公式` 之类本来就不含图/表关键词，自然返回 None。
    """
    code = str(field_code or "").upper()
    if "SEQ" not in code:
        return None
    if any(hint in code for hint in _TABLE_SEQ_HINTS):
        return "table"
    if any(hint in code for hint in _FIGURE_SEQ_HINTS):
        return "figure"
    return None


#: 比对文案时要忽略的字符：空格与常见分隔符（文档里写 `图 2.2 · 演示图片`，
#: 扫描/列表里可能是 `图2.2 演示图片`，本质是同一段题注）
_SEPARATOR_CHARS = " \t\r\n\u3000·・•-—–－_"


def _squash(text) -> str:
    return "".join(ch for ch in str(text or "") if ch not in _SEPARATOR_CHARS)


class CaptionItem:
    """一条题录：文案 + 可选序列域 + 起止偏移 + 页码 + 所属章节。

    离线扫描（`offline_scan`）只填 `display` 与 `chapter`（拿不到 COM 引用与页码）；
    COM 扫描填 `field` / `start` / `end` / `page`。跳转对两种来源都成立：
    有域或区间就用它们精确定位，否则按文案 `Find`。
    """

    def __init__(self, display, field=None, start=None, end=None, page="", chapter=-1,
                 ordinal=0, paragraph_index=None):
        self.display = display
        self.field = field
        self.start = start
        self.end = end
        self.page = page
        self.chapter = chapter
        #: 同一份文案在文档里出现多次时，这是第几条（0 起）。离线来源只有文案，
        #: 靠它区分"图1.1 演示图片"与"图1.2 演示图片"这类同名题注。
        self.ordinal = ordinal
        #: 题注是文档里的第几段（0 起，离线扫描提供）。跳转优先用它。
        self.paragraph_index = paragraph_index

    def select_in_document(self, doc):
        """在 Word 里选中该题录 —— 同时就是"跳转 + 高亮"。"""
        if self.field is not None and self._select_paragraph_of_field(doc):
            return True
        if self.start is not None and self.end is not None:
            try:
                doc.Range(self.start, self.end).Select()
                return True
            except Exception:  # noqa: BLE001  区间失效时退回按文案查找
                pass  # silent-ok: 区间失效时退回按段落/文案查找
        if self._select_paragraph_by_index(doc):
            return True
        return self._select_by_text(doc)

    def _select_paragraph_by_index(self, doc) -> bool:
        """按"第几段"定位（离线扫描记下的段落下标）。

        为什么优先于按文案找：文案会随排版变（`图2.2 演示图片` ↔ `图 2.2 · 演示图片`），
        而段落下标不变。选中前用"去掉空格与分隔符"的文本核对一次，对不上就退回按文案找
        —— 不核对就可能在文档结构变过之后硬按旧下标跳到别的段落。
        """
        if self.paragraph_index is None or int(self.paragraph_index) < 0:
            return False
        try:
            para = doc.Paragraphs(int(self.paragraph_index) + 1).Range
            start, end = int(para.Start), int(para.End)
            text = str(para.Text)
        except Exception:  # noqa: BLE001  下标越界/COM 失效
            return False
        if _squash(text) != _squash(str(self.display).split("  [", 1)[0]):
            return False
        if end <= start:
            return False
        try:
            doc.Range(start, end - 1).Select()      # -1 去掉段尾 ¶
            return True
        except Exception:  # noqa: BLE001
            return False

    def _select_paragraph_of_field(self, doc) -> bool:
        """选中域所在**整段**（去掉段尾标记）。

        为什么不能只 `field.Result.Select()`：题注编号常用带 `\\h`（隐藏）的序列域，它的
        Result 是隐藏文本 ⇒ 选出来是 **0 宽的空选区**，用户看到"跳过去了但没有高亮"
        （2026-11-13 实测：`SEQ figure \\h \\s 1` → 3939..3939 空选区）。
        """
        try:
            para = self.field.Result.Paragraphs(1).Range
            start, end = int(para.Start), int(para.End)
        except Exception:  # noqa: BLE001  域对象可能已失效
            return False
        if end <= start:
            return False
        try:
            doc.Range(start, end - 1).Select()      # -1 去掉段尾 ¶，避免连带选中段落标记
            return True
        except Exception:  # noqa: BLE001
            return False

    def search_needles(self) -> list[str]:
        """按优先级给出要搜的文本：整串 → 去掉编号前缀（图 1.1）→ 更短的前缀片段。"""
        text = (self.display or "").split("  [", 1)[0].strip()
        if not text:
            return []
        needles = [text]
        stripped = _NUMBER_PREFIX_RE.sub("", text).strip()
        if stripped and stripped != text:
            needles.append(stripped)
            needles.append(stripped[:16])
        else:
            needles.append(text[:16])
        seen: list[str] = []
        for needle in needles:
            if needle and needle not in seen:
                seen.append(needle)
        return seen

    def _select_by_text(self, doc) -> bool:
        """按文案定位（离线扫描没有 COM 引用，也没有字符区间）。

        必须**从文档开头**搜，同文案出现多次时取第 `ordinal` 条。老实现用
        `doc.Application.Selection.Find`，那是"**从当前光标往后**找"：光标停在哪里，
        点同一条会跳到不同位置 —— 用户现场正是"点击的题注和实际跳转的对应不上、时好时坏"。
        """
        for needle in self.search_needles():
            hits = self._find_all(doc, needle)
            if not hits:
                continue
            target = hits[min(self.ordinal, len(hits) - 1)]
            try:
                target.Select()
                logger.info("跳转按文案定位：%r 命中 %d 处，取第 %d 条（该条目没有域/区间/段落下标）",
                            needle, len(hits), min(self.ordinal, len(hits) - 1) + 1)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    @staticmethod
    def _find_all(doc, needle: str, limit: int = 200) -> list:
        """找出该文案在整个文档里的全部命中区间（从文档开头，不依赖当前光标）。"""
        hits: list = []
        if not needle:
            return hits
        try:
            end_of_doc = int(doc.Content.End)
        except Exception:  # noqa: BLE001  测试替身/Word 异常
            return hits
        position = 0
        while position < end_of_doc and len(hits) < limit:
            try:
                rng = doc.Range(position, end_of_doc)
                finder = rng.Find
                finder.ClearFormatting()
                finder.Text = needle
                finder.Forward = True
                finder.Wrap = 0            # wdFindStop：别绕回文档开头
                if not finder.Execute():
                    break
                hits.append(rng.Duplicate)
                next_position = int(rng.End)
            except Exception:  # noqa: BLE001  搜索失败不该抛到界面
                break
            if next_position <= position:  # 防御：位置不前进就停，别死循环
                break
            position = next_position
        return hits
