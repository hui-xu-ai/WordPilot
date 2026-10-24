# tools/smoke_p10_reference.py
"""参考文献助手 P3 冒烟：离屏构建主页 → 新建一条文献 → 断言 CSV 落盘与索引可用。

不依赖 Word、不碰真实 config（数据目录用临时目录），可在任意会话跑。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _Ctx:
    def tr(self, key, /, **params):
        return key


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from plugins.reference.library_page import LibraryPage
    from plugins.reference.reference_index import ReferenceIndex
    from plugins.reference.reference_store import ReferenceStore

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = ReferenceStore(directory=root)
        index = ReferenceIndex(db_path=root / "index.db")
        try:
            page = LibraryPage(_Ctx(), store, index)
            page.start_new()
            page.label_edit.setText("1")
            page.title_edit.setText("Global biodiversity patterns")
            page.authors_edit.setText("Tittensor DP")
            page.year_edit.setText("2004")
            page.note_edit.setPlainText("核心参考，讲全球生物多样性格局")
            page.save_current()

            assert page.table.rowCount() == 1, "保存后表格应出现一行"
            assert store.refs_path.exists(), "references.csv 必须落盘"

            reloaded = ReferenceStore(directory=root)
            saved = reloaded.find_by_label("1")
            assert saved is not None and saved.note.startswith("核心参考")

            index.ensure(reloaded)
            payload = index.get_payload(saved.ref_key)
            assert payload is not None and payload["title"].startswith("Global biodiversity")

            page.search_edit.setText("生物多样性")
            visible = [row for row in range(page.table.rowCount())
                       if not page.table.isRowHidden(row)]
            assert visible == [0], "搜索应能命中标题/备注"

            page.delete_current()
            assert page.table.rowCount() == 0
            assert ReferenceStore(directory=root).get(saved.ref_key) is None

            print("表头：", [page.table.horizontalHeaderItem(i).text()
                          for i in range(page.table.columnCount())])
            print("数据目录：", store.directory)
            print("ALL OK")
        finally:
            index.close()
            app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
