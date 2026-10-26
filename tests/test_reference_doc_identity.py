# tests/test_reference_doc_identity.py
"""S10：文档身份必须用全路径，同名不同目录的文档不能共用锚点。

审查证据：`word_watch` 用 `Document.Name` 判断是否重扫，
面对另一目录的 same.docx 仍返回"无需重扫"，点击查表沿用了旧锚点。
"""
from __future__ import annotations


def test_identity_uses_full_name():
    from plugins.reference.word_watch import WordWatch

    doc = type("D", (), {"Name": "same.docx", "FullName": "C:/b/same.docx"})()
    word = type("W", (), {"ActiveDocument": doc})()

    assert WordWatch._doc_identity(word) == "C:/b/same.docx"


def test_same_name_different_dirs_are_different_identities():
    from plugins.reference.word_watch import WordWatch

    def _word(full_name: str):
        doc = type("D", (), {"Name": "same.docx", "FullName": full_name})()
        return type("W", (), {"ActiveDocument": doc})()

    first = WordWatch._doc_identity(_word("C:/a/same.docx"))
    second = WordWatch._doc_identity(_word("C:/b/same.docx"))

    assert first != second, "不同目录的同名文档必须是两个身份"


def test_identity_is_empty_when_word_unavailable():
    """Word 关闭/未打开时返回空串，由调用方回退到 Name（不能抛异常）。"""
    from plugins.reference.word_watch import WordWatch

    class _Boom:
        @property
        def ActiveDocument(self):
            raise RuntimeError("Word closed")

    assert WordWatch._doc_identity(_Boom()) == ""
