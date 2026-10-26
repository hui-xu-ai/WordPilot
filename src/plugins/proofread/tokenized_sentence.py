# plugins/proofread/tokenized_sentence.py
"""把 Word 句子 Range 解析为 token 列表（对象用占位符表示）。

从 word_monitor.py 抽出来：那个文件已接近行数阈值，而这块与线程/监控无关。
"""
from __future__ import annotations

from core.services.word_constants import wdConst as constants


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
