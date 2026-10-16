# hotkey_manager.py
import keyboard as kb
import win32com.client as win32

from plugins.proofread.word_monitor import extract_current_sentence, get_context_sentences


class HotkeyManager:
    def __init__(self, config, main_window):
        self.config = config
        self.main = main_window
        self.hotkeys = []                  # 保存已注册热键的回调，用于清理
        self.register_all()

    def unregister_all(self):
        """移除所有已注册的热键，用于防止重复注册"""
        for hk in self.hotkeys:
            try:
                kb.remove_hotkey(hk)
            except:
                pass
        self.hotkeys.clear()

    def register_all(self):
        # 先取消旧的注册
        self.unregister_all()

        hk = self.config.get("hotkeys", {})
        # 记录每个 add_hotkey 返回的回调标识
        hotkey1 = kb.add_hotkey(hk.get("toggle_monitor", "ctrl+shift+e"), self.toggle_monitor)
        hotkey2 = kb.add_hotkey(hk.get("proof_cursor", "ctrl+shift+z"), self.proof_cursor)
        hotkey3 = kb.add_hotkey(hk.get("translate", "ctrl+shift+t"), self.translate_action)
        hotkey4 = kb.add_hotkey(hk.get("rewrite", "ctrl+shift+r"), self.rewrite_action)
        self.hotkeys = [hotkey1, hotkey2, hotkey3, hotkey4]

    def toggle_monitor(self):
        self.main.proof_btn.toggle()

    def proof_cursor(self):
        try:
            word = win32.GetActiveObject("Word.Application")
            sel = word.Selection
            cur = extract_current_sentence()   # 无参数
            if cur and len(cur) >= 4:
                prev_cur_next = get_context_sentences()
                api = self.main.api
                corrected = api.proof_text(cur)
                if corrected and corrected.strip() != cur.strip():
                    self.main.handle_suggestion(cur, corrected)
        except:
            pass

    def translate_action(self):
        self.main.clipboard_action("translate")

    def rewrite_action(self):
        self.main.clipboard_action("rewrite")