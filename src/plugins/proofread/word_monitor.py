# word_monitor.py
#--修改18  整体替换
import difflib
import logging
import re
import time

from PySide6.QtCore import QThread, Signal

from core.services.proof_cache import get_default_cache
from core.services.word_bridge import get_word_app, com_initialized
from core.services.word_constants import wdConst as constants  # word常量修改  2026.05.19

logger = logging.getLogger(__name__)


class ProofingFailedError(RuntimeError):
    """校对未完成，不能作为“与原文相同/通过”的结果交给桌宠。"""
# 标记清除已拆到 word_marks（性能改造见该模块）；此处保留再导出供旧调用方使用
from plugins.proofread.word_marks import clear_error_marks  # noqa: E402,F401

wdUnderlineWavy = constants.wdUnderlineWavy
wdColorRed = constants.wdColorRed


class TokenizedSentence:
    """将 Word 句子 Range 解析为带对象信息的 token 列表"""
    def __init__(self, sentence_range):
        self.range = sentence_range.Duplicate
        self.tokens = []          # 每个元素: {'text': str, 'start': int, 'end': int, 'type': 'text'|'image'|'formula'}
        self.pure_text = ""       # 纯文本视图，对象用占位符表示
        self._tokenize()

    def _tokenize(self):
        # 使用 Range.Words 遍历，并识别对象
        r = self.range.Duplicate
        for i in range(1, r.Words.Count + 1):
            word = r.Words(i)
            if word.InlineShapes.Count > 0:
                self.tokens.append({
                    'text': f'[IMG{len(self.tokens)}]',
                    'start': word.Start,
                    'end': word.End,
                    'type': 'image'
                })
            elif word.OMaths.Count > 0:
                self.tokens.append({
                    'text': f'[EQ{len(self.tokens)}]',
                    'start': word.Start,
                    'end': word.End,
                    'type': 'formula'
                })
            else:
                self.tokens.append({
                    'text': word.Text,
                    'start': word.Start,
                    'end': word.End,
                    'type': 'text'
                })
        self.pure_text = "".join([t['text'] for t in self.tokens])

    def get_text_for_api(self):
        """返回发给 AI 的文本（占位符形式）"""
        return self.pure_text

    def has_special_objects(self):
        """判断是否包含图像或公式"""
        return any(t['type'] in ('image', 'formula') for t in self.tokens)
        
#  分块替换句子--修改18
    def get_blocks(self):           
        """
        返回分块列表，每个块是 {'type':'text'/'object', 'content':...}
           - 对于文本块，content 是原始文本
           - 对于对象块，content 是包含占位符和位置信息的字典
        """
        blocks = []
        for t in self.tokens:
            if t['type'] == 'text':
                blocks.append({'type': 'text', 'content': t['text']})
            else:
                blocks.append({
                    'type': 'object',
                    'content': {
                        'placeholder': t['text'],
                        'start': t['start'],
                        'end': t['end'],
                    }
                })
        return blocks
    def find_placeholder_positions(self, corrected_text):
        """
        在修正文本中按顺序查找所有占位符的出现位置（可能因文本变化而移位）。
        返回列表 [(placeholder, start_char_index, end_char_index), ...]
        """        
        pattern = re.compile(r'\[(IMG|EQ)\d+\]')
        positions = []
        for m in pattern.finditer(corrected_text):
            positions.append((m.group(), m.start(), m.end()))
        return positions
#  分块替换句子--修改18
def token_based_mark_differences(original_tokens, corrected_sequence, sentence_range):
    """
    基于 token 序列的差异标记。
    原理：将 corrected_sequence 视为期望的 token 文本序列（纯文本），与 original_tokens 的文本序列比对，
    找出需标记的 token，并对其对应的 Word Range 应用红色波浪下划线。
    """
    # 构建原文 token 文本列表
    org_texts = [t['text'] for t in original_tokens]
    # 使用 difflib 进行序列对比
    matcher = difflib.SequenceMatcher(None, org_texts, corrected_sequence)
    intervals = []  # 需要标记的 token 索引范围 [start, end)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != 'equal':
            intervals.append((i1, i2))
    # 合并重叠/相邻区间
    merged = []
    for start, end in sorted(intervals):
        if not merged:
            merged.append([start, end])
        else:
            last_end = merged[-1][1]
            if start <= last_end:  # 重叠或相邻
                merged[-1][1] = max(last_end, end)
            else:
                merged.append([start, end])
    # 应用红色下划线到对应 token 的 Range
    for start_idx, end_idx in merged:
        if start_idx >= len(original_tokens) or end_idx > len(original_tokens):
            continue
        # 取这些 token 的起止位置
        first_token_start = original_tokens[start_idx]['start']
        last_token_end = original_tokens[end_idx - 1]['end'] if end_idx > start_idx else original_tokens[start_idx]['end']
        try:
            sub_range = sentence_range.Duplicate
            sub_range.SetRange(first_token_start, last_token_end)
            sub_range.Font.Underline = wdUnderlineWavy
            sub_range.Font.UnderlineColor = wdColorRed
        except Exception:
            # 标记失败不能静默：否则界面说"已给出建议"而 Word 里一条下划线都没有
            logger.debug("标记 token 区间失败（%s-%s）", first_token_start, last_token_end, exc_info=True)
#  分块替换句子--修改18

def mark_text_differences(original, corrected, start, end):
    """
    保留旧版本兼容性（纯文本版本），实际调用新标记逻辑。
    如果句子包含对象，则使用 token_based_mark_differences。
    """
    try:
        word = get_word_app()
        doc = word.ActiveDocument
        sentence_range = doc.Range(start, end)
    except Exception:
        return

    # 尝试 tokenize
    try:
        tokenized = TokenizedSentence(sentence_range)
        if tokenized.has_special_objects():
            # 使用新标记
            # 获取 corrected 对应的 token 序列（假设 corrected 也是纯文本）
            # 需要将 corrected 与 token 的纯文本进行匹配
            # 为简化，直接字符 diff 再映射到 token
            # 这里将 corrected 按字符拆分（同长度），若长度变化可能不准确，但可接受
            # 更好的方式是用原文和修正文本的字符 diff，然后映射到 token
            # 我们采用：先计算原文 pure_text 与 corrected 的 diff，得到需要标记的字符区间，
            # 然后根据 token 的边界映射为 token 范围
            pure = tokenized.get_text_for_api()
            if corrected == pure:
                return  # 相同则不标记
            matcher = difflib.SequenceMatcher(None, pure, corrected)
            char_intervals = []
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag != 'equal':
                    char_intervals.append((i1, i2))
            # 合并
            merged_char = []
            for a, b in sorted(char_intervals):
                if not merged_char:
                    merged_char.append([a, b])
                else:
                    if a <= merged_char[-1][1]:
                        merged_char[-1][1] = max(merged_char[-1][1], b)
                    else:
                        merged_char.append([a, b])
                                    
           
            # --- 在对象分支的 merged_char 计算之后，此处插入扩展 ---        --修改19
            expanded = []
            for left, right in merged_char:
                new_left = max(0, left - 2)         #  --修改20 2改成1
                new_right = min(len(pure), right + 2)   # pure 是 tokenized 的纯文本      #  --修改20 2改成1
                expanded.append((new_left, new_right))
            # 重新合并扩展后的区间
            merged_char = []
            for a, b in sorted(expanded):
                if not merged_char:
                    merged_char.append([a, b])
                else:
                    if a <= merged_char[-1][1]:
                        merged_char[-1][1] = max(merged_char[-1][1], b)
                    else:
                        merged_char.append([a, b])
            # ---------- 扩展结束，继续执行后续的 token 映射 ----------       --修改19

           # 映射到 token 索引
            token_ranges = []
            offset = 0
            for i, t in enumerate(tokenized.tokens):
                txt = t['text']
                token_start = offset
                token_end = offset + len(txt)
                for mc_start, mc_end in merged_char:
                    if max(token_start, mc_start) < min(token_end, mc_end):
                        # 有重叠
                        token_ranges.append(i)
                        break
                offset = token_end
            # 标记
            if token_ranges:
                # 构造连续区间
                token_ranges.sort()
                first = token_ranges[0]
                last = first
                to_mark = []
                for idx in token_ranges[1:]:
                    if idx == last + 1:
                        last = idx
                    else:
                        to_mark.append((first, last + 1))
                        first = idx
                        last = idx
                to_mark.append((first, last + 1))
                for s_idx, e_idx in to_mark:
                    try:
                        first_token_start = tokenized.tokens[s_idx]['start']
                        last_token_end = tokenized.tokens[e_idx - 1]['end']
                        sub_range = doc.Range(first_token_start, last_token_end)
                        sub_range.Font.Underline = wdUnderlineWavy
                        sub_range.Font.UnderlineColor = wdColorRed
                    except Exception:
                        logger.debug("按 token 标记失败（%s-%s）", s_idx, e_idx, exc_info=True)
            return
    except Exception:
        logger.debug("token 标记分支整体失败，回退纯文本标记", exc_info=True)

    # 纯文本回退逻辑（保留原函数内容，稍作简化）
# --修改19    
    matcher = difflib.SequenceMatcher(None, original, corrected)
    intervals = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        left = max(0, i1 - 2)               # 改为 2     
        right = min(len(original), i2 + 2)  # 改为 2     
        intervals.append((left, right))
    merged = []
    for left, right in sorted(intervals):
        if not merged:
            merged.append([left, right])
        else:
            if left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
# --修改19    
        
    for left, right in merged:
        try:
            sub_range = sentence_range.Duplicate
            sub_range.SetRange(sentence_range.Start + left,
                               sentence_range.Start + right)
            sub_range.Font.Underline = wdUnderlineWavy
            sub_range.Font.UnderlineColor = wdColorRed
        except Exception:
            logger.debug("标记差异区间失败（%s-%s）", left, right, exc_info=True)




def get_sentence_range(selection):
    try:
        sentence = selection.Sentences(1)
        return sentence
    except Exception:
        range_copy = selection.Range.Duplicate
        range_copy.Expand(constants.wdSentence)
        return range_copy


def extract_current_sentence():
    try:
        word = get_word_app()
        sel = word.Selection
        sent_range = get_sentence_range(sel)
        return sent_range.Text.strip()
    except Exception:
        return ""


def get_context_sentences():
    try:
        word = get_word_app()
        sel = word.Selection
        current_range = get_sentence_range(sel)
        current_text = current_range.Text.strip()
        prev_range = current_range.Duplicate
        prev_range.MoveStart(constants.wdSentence, -1)
        prev_text = prev_range.Text.strip() if prev_range.Start < current_range.Start else ""
        next_range = current_range.Duplicate
        next_range.MoveEnd(constants.wdSentence, 1)
        next_text = next_range.Text.strip() if next_range.End > current_range.End else ""
        return [prev_text, current_text, next_text]
    except Exception:
        return ["", "", ""]


class WordMonitor(QThread):
    text_detected = Signal(str)
    debug_signal = Signal(str)
    suggestion_ready = Signal(str, str, int, int)
    status_update = Signal(str)
    #: 即将发起一次校对（供桌宠切到 busy 状态）
    proofing_started = Signal()
    proofing_failed = Signal(str)

    def __init__(self, config, api, ignore_mgr=None, i18n=None):
        super().__init__()
        self.config = config
        self.api = api
        self.ignore_mgr = ignore_mgr
        self._i18n = i18n
        self.running = True
        self.proof_enabled = True          # ← 新增这一行        --修改22
        self.last_text = ""
        self.last_marked_text = ""
        self.sentence_state = {}
        
       
        self.last_selection_range = None          # 上次选择的 Range (start, end)        # --修改20

    # ---------------- 多语言 ----------------
    def tr(self, key: str, /, **params) -> str:
        """状态栏文案：跟随界面语言；服务缺失时原样返回 key。"""
        if self._i18n is None:
            return key
        return self._i18n.tr(f"proofread.{key}", **params)

    def prompt_tr(self, key: str, /, **params) -> str:
        """发给 AI 的指令文案：跟随提示词语言，与界面语言解耦。"""
        if self._i18n is None:
            return key
        return self._i18n.prompt_tr(f"proofread.{key}", **params)

    # word_monitor.py - class WordMonitor       --修改18 重写校对逻辑
    def run(self):
        with com_initialized():
            self._run_loop()

    def _run_loop(self):
        self.debug_signal.emit(self.tr("monitor.started"))
        try:
            while self.running:
                proofing = False
                try:
                    if self.proof_enabled:  # ← 新增判断  --修改22
                        word = get_word_app()
                        selection = word.Selection
                        sentence_range = get_sentence_range(selection)
                        current_text = sentence_range.Text.strip()
                        start = sentence_range.Start

                        # ---- 新增：选中整个句子进行校对 ctrl + 鼠标左击 ----     --修改20
                        adjusted_sent = sentence_range.Duplicate
                        try:
                            sel_range = word.Selection.Range.Duplicate
                            sel_start, sel_end = sel_range.Start, sel_range.End
                            # 定义需要忽略的末尾字符（不可见字符和半角空格）       #  --修改 20260513 判断是否为一段最后一句
                            IGNORE_CHARS = {'\r', '\n', '\v', '\t'}
                            while adjusted_sent.Characters.Last.Text in IGNORE_CHARS:
                                # 防止退到范围起始位置之前
                                if adjusted_sent.End <= adjusted_sent.Start:
                                    break
                                adjusted_sent.MoveEnd(constants.wdCharacter, -1)
                            sent_text = adjusted_sent.Text
                            sent_text_stripped = sent_text.strip()
                            sel_text = sel_range.Text
                            sel_text_stripped = sel_text.strip()
                        except Exception:
                            logger.debug("读取选中句子失败，按无选区处理", exc_info=True)
                            sel_start = sel_end = None
                        
                        # if (sel_start is not None and sel_start < sel_end and              #       测试选中句子
                            # (sel_end - sel_start) == (adjusted_sent.End - adjusted_sent.Start)):
                            # print(sel_end - sel_start)
                            # print(adjusted_sent.End - adjusted_sent.Start)
                            
                        # 条件：之前是光标（last_selection_range起点=终点），当前选中了一个单词且长度是整个句子
                        if (sel_start is not None and sel_start < sel_end and
                            # (sel_end - sel_start) == (adjusted_sent.End - adjusted_sent.Start) and     #  --修改 20260513 判断是否恰好选中整个句子
                            (sent_text_stripped == sel_text_stripped) and     #  直接判断句子是否一样 更灵敏
                            self.last_selection_range is not None and
                            self.last_selection_range[0] == self.last_selection_range[1] and
                            current_text and len(current_text) >= 8):
                            
                            self.status_update.emit(self.tr("monitor.proofing_selection"))
                            proofing = True
                            self.proofing_started.emit()
                            try:
                                clear_error_marks(sentence_range.Start, sentence_range.End)
                                pure, corrected, s_start, s_end = proof_sentence(
                                    sentence_range, self.api, self.config, i18n=self._i18n
                                )
                                if pure:
                                    self.last_marked_text = pure
                                    self.suggestion_ready.emit(pure, corrected, s_start, s_end)
                                    if corrected != pure:
                                        self.status_update.emit(self.tr("monitor.suggestion_ready"))
                                    else:
                                        self.status_update.emit(self.tr("monitor.no_error"))
                                else:
                                    self.proofing_failed.emit(self.tr("monitor.too_short"))
                                    self.status_update.emit(self.tr("monitor.too_short"))
                            except Exception as e:
                                self.proofing_failed.emit(str(e))
                                self.status_update.emit(self.tr("monitor.selection_failed", error=e))
                            proofing = False

                        # 更新 last_selection_range
                        if sel_start is not None:
                            self.last_selection_range = (sel_start, sel_end)
                        else:
                            self.last_selection_range = (sentence_range.Start, sentence_range.Start)
                        # ---- 选中句子检测结束 ----      --修改20

                        if current_text and len(current_text) >= 8:
                            is_complete = current_text.endswith(('。', '？', '！', '?', '!'))
                            sent_key = start
                            prev_state = self.sentence_state.get(sent_key)

                            if prev_state is None:
                                # 首次记录该句子
                                self.sentence_state[sent_key] = (current_text, is_complete)
                            else:
                                prev_text, prev_complete = prev_state
                                if current_text != prev_text:
                                    # 句子内容发生了变化
                                    if not prev_complete and is_complete:
                                        # 句子刚完成，触发校对
                                        self.status_update.emit(self.tr("monitor.sentence_detected"))

                                        # 检查忽略列表
                                        if self.ignore_mgr and self.ignore_mgr.is_ignored(current_text):
                                            self.status_update.emit(self.tr("monitor.ignored"))
                                            self.sentence_state[sent_key] = (current_text, is_complete)
                                            continue

                                        proofing = True
                                        self.proofing_started.emit()

                                        # 调用统一校对函数（内部处理分词、缓存、API、标记）
                                        pure, corrected, start_pos, end_pos = proof_sentence(
                                            sentence_range, self.api, self.config, i18n=self._i18n
                                        )
                                        if pure is not None:
                                            self.last_marked_text = pure
                                            self.suggestion_ready.emit(pure, corrected, start_pos, end_pos)
                                            latency = getattr(self.api, "last_latency_ms", 0) if self.api else 0
                                            if corrected != pure:
                                                self.status_update.emit(self.tr("monitor.suggestion_ready_latency",
                                                                                ms=latency) if latency
                                                                        else self.tr("monitor.suggestion_ready"))
                                            else:
                                                self.status_update.emit(self.tr("monitor.no_error_latency",
                                                                                ms=latency) if latency
                                                                        else self.tr("monitor.no_error"))
                                        else:
                                            self.proofing_failed.emit(self.tr("monitor.too_short"))
                                            self.status_update.emit(self.tr("monitor.too_short"))
                                        proofing = False

                                        # 更新状态
                                        self.sentence_state[sent_key] = (current_text, is_complete)
                                    else:
                                        # 句子未完成或状态未变，只更新状态
                                        self.sentence_state[sent_key] = (current_text, is_complete)
                                else:
                                    # 文本无变化，只更新完成状态
                                    self.sentence_state[sent_key] = (current_text, is_complete)

                    time.sleep(0.2)
                except ProofingFailedError as e:
                    self.proofing_failed.emit(str(e))
                    self.status_update.emit(self.tr("monitor.error", error=e))
                    time.sleep(1)
                except Exception as e:
                    if proofing:
                        self.proofing_failed.emit(str(e))
                    self.debug_signal.emit(self.tr("monitor.error", error=e))
                    self.status_update.emit(self.tr("monitor.open_word"))
                    time.sleep(1)
        finally:
            self.debug_signal.emit(self.tr("monitor.stopped"))


    def stop(self):
        self.running = False
        self.debug_signal.emit(self.tr("monitor.stop_requested"))
        
###   --修改21
def proof_sentence(sentence_range, api, config, i18n=None):
    tokenized = TokenizedSentence(sentence_range)
    raw_text = tokenized.get_text_for_api()
    if not raw_text or len(raw_text) < 6:
        return None, None, None, None

    def prompt_tr(key: str) -> str:
        """提示词语言（与界面语言独立），服务缺失时回退到 key 本身。"""
        return i18n.prompt_tr(f"proofread.{key}") if i18n is not None else key

    # 1. 分离正文和尾部（尾部为结束符号+可能的换行符）
    body_text, tail_text = split_trailing_chars(raw_text)

    # 2. 构建发送给 AI 的提示（包含完整的 raw_text）
    has_placeholder = bool(re.search(r'\[(IMG|EQ)\d+\]', raw_text))
    api_prompt = raw_text
    if has_placeholder:
        api_prompt = prompt_tr("prompt.placeholder_notice") + raw_text

    # 3. 缓存查询（SQLite，键统一为 body_text；proof_text 内部用同一键读写）
    corrected_full = get_default_cache().get(body_text)
    if not corrected_full:
        if api is None:
            raise ProofingFailedError("API client is unavailable")
        corrected_full = api.proof_text(api_prompt, cache_key=body_text)
    if not corrected_full:
        reason = getattr(api, "last_error", "") or "API returned no correction result"
        raise ProofingFailedError(reason)

    # 4. 提取 AI 返回的正文，并强制恢复原始尾部
    body_new, _ = split_trailing_chars(corrected_full)
    corrected_with_tail = body_new + tail_text

    # 5. 如果实际无变化，不标记
    if corrected_with_tail == raw_text:
        return raw_text, corrected_with_tail, sentence_range.Start, sentence_range.End

    # 6. 标记差异（对比 raw_text 和 corrected_with_tail）
    mark_text_differences(raw_text, corrected_with_tail,
                          sentence_range.Start, sentence_range.End)

    # 7. 清除占位符和尾部标记
    clear_marks_on_objects_and_ending(sentence_range, body_len=len(body_text))

    return raw_text, corrected_with_tail, sentence_range.Start, sentence_range.End

#  --修改21  去除末尾可能存在的换行符用于校对（避免干扰）    
def clear_marks_on_objects_and_ending(sentence_range, body_len=None):
    """
    清除占位符区域、句尾结束符号及换行符上的红色标记。
    body_len: 主体文本长度，如果提供则直接计算尾部范围，否则动态计算。
    """
    try:
        word = get_word_app()
        doc = word.ActiveDocument
        tokenized = TokenizedSentence(sentence_range)
        blocks = tokenized.get_blocks()

        # 1. 清除所有对象占位符的标记
        for b in blocks:
            if b['type'] == 'object':
                start = b['content']['start']
                end = b['content']['end']
                r = doc.Range(start, end)
                # 清的是「红色波浪下划线」，不是字体颜色：原来写 Font.Color 会把用户的彩色文字改成黑色
                r.Font.Underline = 0

        # 2. 清除尾部（结束符号 + 换行等）标记
        sent_text = sentence_range.Text
        if body_len is None:
            body_text, _ = split_trailing_chars(sent_text)
            body_len = len(body_text)

        if body_len < len(sent_text):
            tail_range = doc.Range(sentence_range.Start + body_len, sentence_range.End)
            tail_range.Font.Underline = 0
    except Exception:
        logger.debug("清除对象/句尾标记失败", exc_info=True)

# --修改21 分离句尾结束符号        
def split_trailing_chars(text):
    """
    将文本拆分为 (body, tail)
    tail 包括末尾的结束符（。？！!?）及其后可能跟随的空白、回车等。
    若无结束符，tail 为空。
    """
    match = re.search(r'[。？！!?][\s\r\n]*$', text)
    if match:
        return text[:match.start()], text[match.start():]
    return text, ''
