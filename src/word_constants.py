# word_constants.py
"""
Word 常量兼容模块
- 优先使用 win32com 动态常量（完整、最新）
- 若缓存损坏则自动回退到硬编码值（稳定、跨版本）
"""
import win32com.client

# 所有可能用到的常量（可按需扩展）
_FALLBACK = {
    'wdFormatOriginalFormatting': 16,
    'wdSelectionNormal': 2,
    'wdInlineShapePicture': 3,
    'wdShapeRectangle': 1,
    'wdExportFormatPNG': 2,
    'wdPasteDefault': 0,
    'wdSentence': 3,
    'wdCharacter': 1,
    'wdColorAutomatic': -16777216,
    'wdUnderlineWavy': 2,
    'wdColorRed': 255,
    'wdFieldSequence': 53,                 # Word 序列域类型常量
    'wdSelectionShape': 8,                 # Word 浮动图形（形状）
    'wdSelectionInlineShape': 7,                 # Word 浮动图形（形状）
    'wdWindowStateMinimize': 1,                 #  Word 最小化窗口状态码
    
    
    # 继续补充……
}

class _FallbackConstants:
    """代理对象：优先从 win32com 获取，失败则用硬编码回退"""
    def __getattr__(self, name):
        try:
            # 尝试获取真实的动态常量
            return getattr(win32com.client.constants, name)
        except (AttributeError, Exception):
            if name in _FALLBACK:
                return _FALLBACK[name]
            raise AttributeError(
                f"Word 常量 '{name}' 无法获取，且未定义回退值。"
                f"请检查常量名或手动添加到 _FALLBACK 字典中。"
            )

# 实例化，作为模块的公开对象
wdConst = _FallbackConstants()