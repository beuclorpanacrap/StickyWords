import threading
import time
import tkinter as tk
import ctypes
import difflib
import os
import re
import json

from ctypes import wintypes, byref, sizeof
from pynput import keyboard

#config

MAX_SUGGESTIONS = 4
DEBOUNCE_DELAY = 0.07
USAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage_stats.json")
PREDICTION_CACHE_MAX = 500

_save_lock = threading.Lock()

#scoring system:

#  standard   word_frequencies.txt
#      ~50 000 words, score 10–10,000.
#      higher score = more common= ranked earlier.

#  user   (user_frequency.json)
#      every word the user types is tracked here.
#      A word the user types 5 times will significantly
#      outrank a rare dictionary word.
#      Delete this file to reset preferences (UI testing - Gabi)

#  formula used during ranking:
#   combined = (standard_score * 0.4) + (user_score * 600 * 0.6)


STANDARD_FREQ_FILE = "word_frequencies.txt"
USER_FREQ_FILE     = "user_frequency.json"

# how fast personal usage takes over
STANDARD_WEIGHT = 0.4
USER_WEIGHT     = 0.6
USER_SCORE_MULTIPLIER = 600

standard_freq: dict[str, int] = {}
user_freq: dict[str, int]     = {}


def load_standard_frequencies():
    global standard_freq
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, STANDARD_FREQ_FILE)
    if not os.path.exists(path):
        print(f"[FREQ] Warning: {STANDARD_FREQ_FILE} not found. Standard frequency disabled.")
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) == 2:
                    word, score = parts[0].strip().lower(), parts[1].strip()
                    if score.isdigit():
                        standard_freq[word] = int(score)
        print(f"[FREQ] Loaded {len(standard_freq):,} words from {STANDARD_FREQ_FILE}")
    except Exception as e:
        print(f"[FREQ ERROR] Could not load standard frequencies: {e}")


def load_user_frequencies():
    global user_freq
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, USER_FREQ_FILE)
    if not os.path.exists(path):
        user_freq = {}
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            user_freq = json.load(f)
        print(f"[FREQ] Loaded user frequency data ({len(user_freq):,} words tracked)")
    except Exception as e:
        print(f"[FREQ ERROR] Could not load user frequencies: {e}")
        user_freq = {}


def save_user_frequencies():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, USER_FREQ_FILE)
    with _save_lock:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(user_freq, f, indent=2)
        except Exception as e:
            print(f"[FREQ ERROR] Could not save user frequencies: {e}")


def record_user_word(word: str):
    w = word.strip().lower()
    if not w or not w.isalpha():
        return
    user_freq[w] = user_freq.get(w, 0) + 1
    save_user_frequencies()


def get_combined_score(word: str) -> float:
    w = word.lower()
    s_score = standard_freq.get(w, 0)
    u_score = user_freq.get(w, 0)
    return (s_score * STANDARD_WEIGHT) + (u_score * USER_SCORE_MULTIPLIER * USER_WEIGHT)


def rank_suggestions(candidates: list[str]) -> list[str]:
    return sorted(candidates, key=lambda w: get_combined_score(w), reverse=True)


load_standard_frequencies()
load_user_frequencies()

#v2 UI design

C = dict(
    bg="#1e1e1e",
    bg_header="#252525",
    bg_top_row="#1a2535",
    bg_hover="#2a2a2a",
    border="#3a3a3a",
    text_main="#e2e8f0",
    text_muted="#64748b",
    text_hint="#475569",
    text_top="#93c5fd",
    num_bg="#2a2a2a",
    num_fg="#64748b",
    num_bg_top="#1e3a5f",
    num_fg_top="#60a5fa",
    echo_fg="#475569",
)

#vocab

WORD_LIST = []
WORD_SET:  set[str] = set()

def load_local_vocabulary():
    global WORD_LIST, WORD_SET

    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_library_file = os.path.join(script_dir, "vocab.txt")

    try:
        with open(local_library_file, "r", encoding="utf-8") as f:
            WORD_LIST = [
                line.strip().lower()
                for line in f
                if line.strip()
            ]
        WORD_SET = set(WORD_LIST)
        print(f"\n[VOCAB] Loaded {len(WORD_LIST):,} words from vocab.txt\n")

    except Exception as e:
        print("[VOCAB ERROR]", e)


load_local_vocabulary()

#windows type shi

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long)
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT)
    ]


# app detect

_BROWSER_CLASSES = {
    "Chrome_WidgetWin_1",   
    "MozillaWindowClass",   
    "OperaWindowClass",     
}

def get_active_app_name() -> str:
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return "Unknown"
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if title:
                parts = [p.strip() for p in title.split(" - ")]
                return parts[-1] if len(parts) > 1 else parts[0]
        pid = ctypes.c_ulong(0)
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h_proc = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
        if h_proc:
            buf2 = ctypes.create_unicode_buffer(260)
            ctypes.windll.psapi.GetModuleFileNameExW(h_proc, None, buf2, 260)
            ctypes.windll.kernel32.CloseHandle(h_proc)
            exe = buf2.value
            if exe:
                name = os.path.splitext(os.path.basename(exe))[0]
                _nice = {
                    "chrome": "Google Chrome", "msedge": "Microsoft Edge",
                    "firefox": "Firefox", "notepad": "Notepad",
                    "Code": "Visual Studio Code", "Discord": "Discord",
                }
                return _nice.get(name, name)
    except Exception:
        pass
    return "Unknown"

def _foreground_is_browser() -> bool:
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value in _BROWSER_CLASSES or "brave" in buf.value.lower()
    except Exception:
        return False


#UI initialization

class DesktopAssistantUI:

    def __init__(self):
        self.root = tk.Tk()
        self.word_buffer = ""
        self.context_words = []
        self.current_suggestions = []
        self.selected_index = 0
        self.is_injecting = False
        self.last_foreground_hwnd = None
        self.last_foreground_title = ""
        self._active_app = "Unknown"
        self.keyboard_controller = keyboard.Controller()
        self.prediction_cache = {}
        self.last_request_time = 0
        self.overlay_watch_running = False


        self.last_ui_x = 0
        self.last_ui_y = 0
        self._last_hwnd_for_buffer = None

        self.load_usage_stats()
        self.setup_overlay()

        print("\n[StickyWords]")
        print("Press Keys 1, 2, 3, or 4 to choose corrections.\n")

        self.modifiers = set()
        self.listener = keyboard.Listener(
            on_press=self.on_key_press,
            on_release=self.on_key_release
        )
        self.listener.start()

#learning

    def load_usage_stats(self):
        if os.path.exists(USAGE_FILE):
            try:
                with open(USAGE_FILE, "r") as f:
                    self.usage_stats = json.load(f)
            except:
                self.usage_stats = {}
        else:
            self.usage_stats = {}

    def save_usage_stats(self):
        with _save_lock:
            try:
                with open(USAGE_FILE, "w") as f:
                    json.dump(self.usage_stats, f, indent=2)
            except:
                pass

    def learn_choice(self, typed, selected):
        import datetime
        typed    = typed.lower().strip()
        selected = selected.lower().strip()
        if not typed or not selected:
            return

        self.usage_stats.setdefault(typed, {})
        self.usage_stats[typed][selected] = self.usage_stats[typed].get(selected, 0) + 1

        app_key = f"__app__{self._active_app or 'Unknown'}"
        self.usage_stats.setdefault(app_key, {})
        self.usage_stats[app_key][typed] = self.usage_stats[app_key].get(typed, 0) + 1

        hour_key = datetime.datetime.now().strftime("%Y-%m-%dT%H:00")
        tl = self.usage_stats.setdefault("__timeline__", {})
        tl[hour_key] = tl.get(hour_key, 0) + 1

        self.prediction_cache.pop(typed, None)
        self.save_usage_stats()

        user_freq[selected] = user_freq.get(selected, 0) + 3
        save_user_frequencies()

    #UI setup

    def setup_overlay(self):
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)
        self.root.configure(bg=C["bg"])

        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000

        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        )

        self.frame = tk.Frame(
            self.root,
            bg=C["bg"],
            highlightbackground=C["border"],
            highlightthickness=1,
            bd=0,
        )
        self.frame.pack(fill="both", expand=True)

        hdr = tk.Frame(self.frame, bg=C["bg_header"])
        hdr.pack(fill="x", side="top")

        tk.Label(
            hdr,
            text="●",
            fg=C["text_hint"],
            bg=C["bg_header"],
            font=("Segoe UI", 6),
        ).pack(side="left", padx=(8, 4), pady=5)

        tk.Label(
            hdr,
            text="StickyWords",
            fg=C["text_muted"],
            font=("Segoe UI", 9),
            bg=C["bg_header"],
        ).pack(side="left", pady=5)

        self._app_label = tk.Label(
            hdr,
            text="",
            fg=C["text_hint"],
            bg=C["bg_header"],
            font=("Segoe UI", 8),
        )
        self._app_label.pack(side="left", padx=(4, 0), pady=5)

        self._close_btn = tk.Label(
            hdr,
            text="×",
            fg=C["text_hint"],
            bg=C["bg_header"],
            font=("Segoe UI", 13),
            cursor="hand2",
        )
        self._close_btn.pack(side="right", padx=8, pady=1)
        self._close_btn.bind("<Button-1>", lambda _: self.hide_ui())

        tk.Frame(self.frame, bg=C["border"], height=1).pack(fill="x")

        self._echo_label = tk.Label(
            self.frame,
            text="",
            fg=C["echo_fg"],
            bg=C["bg"],
            font=("Segoe UI", 8),
            anchor="w",
            padx=12,
            pady=2,
        )

        tk.Frame(self.frame, bg=C["border"], height=1).pack(fill="x")

        self.chips_container = tk.Frame(self.frame, bg=C["bg"], padx=0, pady=2)
        self.chips_container.pack(fill="both", expand=True)

        self.suggestion_rows = []
        self.suggestion_labels = []
        self.hotkey_labels = []

        for i in range(MAX_SUGGESTIONS):
            row_frame = tk.Frame(self.chips_container, bg=C["bg"], padx=6, pady=0, cursor="hand2")
            row_frame.pack(fill="x", side="top")
            row_frame.bind("<Button-1>", lambda e, idx=i: self._on_row_click(idx))
            row_frame.bind("<Enter>", lambda e, r=row_frame: r.config(bg=C["bg_hover"]))
            row_frame.bind(
                "<Leave>",
                lambda e, r=row_frame, idx=i: r.config(
                    bg=C["bg_top_row"] if idx == self.selected_index else C["bg"]
                ),
            )

            hk_lbl = tk.Label(
                row_frame,
                text=str(i + 1),
                fg=C["num_fg"],
                bg=C["num_bg"],
                font=("Segoe UI", 7, "bold"),
                width=2,
                bd=0,
            )
            hk_lbl.pack(side="left", padx=(4, 8), pady=4)
            hk_lbl.bind("<Button-1>", lambda e, idx=i: self._on_row_click(idx))

            lbl = tk.Label(
                row_frame,
                text="",
                fg=C["text_main"],
                bg=C["bg"],
                font=("Segoe UI", 10),
                anchor="w",
                bd=0,
            )
            lbl.pack(side="left", fill="x", expand=True, pady=4)
            lbl.bind("<Button-1>", lambda e, idx=i: self._on_row_click(idx))

            self.suggestion_rows.append(row_frame)
            self.hotkey_labels.append(hk_lbl)
            self.suggestion_labels.append(lbl)

        tk.Frame(self.frame, bg=C["border"], height=1).pack(fill="x")
        footer = tk.Frame(self.frame, bg=C["bg_header"], padx=8, pady=4)
        footer.pack(fill="x", side="bottom")
        tk.Label(
            footer,
            text="Tab accept   ↑↓ cycle   Esc dismiss",
            fg=C["text_hint"],
            bg=C["bg_header"],
            font=("Segoe UI", 8),
        ).pack(side="left")

        self.root.withdraw()


#
    def get_system_caret_position(self):
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None

        self.last_foreground_hwnd = hwnd
        self.last_foreground_title = self.get_window_title(hwnd)

        remote_thread_id = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
        current_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        ctypes.windll.user32.AttachThreadInput(current_thread_id, remote_thread_id, True)
        pos = None
        try:
            gui = GUITHREADINFO()
            gui.cbSize = sizeof(GUITHREADINFO)
            ctypes.windll.user32.GetGUIThreadInfo(remote_thread_id, byref(gui))

            if gui.hwndFocus and gui.rcCaret.left != 0:
                point = wintypes.POINT(gui.rcCaret.left, gui.rcCaret.bottom)
                ctypes.windll.user32.ClientToScreen(gui.hwndFocus, byref(point))
                pos = (point.x, point.y + 14)
        finally:
            ctypes.windll.user32.AttachThreadInput(current_thread_id, remote_thread_id, False)

        return pos

    def get_window_title(self, hwnd):
        try:
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
            return buf.value or ""
        except:
            return ""

    def get_mouse_fallback_position(self):
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(byref(pt))
        return (pt.x - 50, pt.y + 24)

    def _check_window_changed(self):
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return

            try:
                my_hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                if hwnd in (my_hwnd, self.root.winfo_id()):
                    return
            except Exception:
                pass

            if _foreground_is_browser():
                self.word_buffer = ""
                self.hide_ui()
            elif self._last_hwnd_for_buffer and hwnd != self._last_hwnd_for_buffer:
                self.word_buffer = ""
                self.hide_ui()

            if hwnd != self._last_hwnd_for_buffer:
                self._last_hwnd_for_buffer = hwnd
                app = get_active_app_name()
                self._active_app = app
                self.root.after(0, lambda a=app: self._app_label.config(
                    text=f"· {a}" if a != "Unknown" else ""
                ))
            else:
                self._last_hwnd_for_buffer = hwnd

        except Exception:
            pass
#keyboard eventts
    def on_key_press(self, key):
        if self.is_injecting:
            return

        try:
            if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                self.modifiers.add('ctrl'); return
            if key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
                self.modifiers.add('alt'); return
            if key in (keyboard.Key.shift, keyboard.Key.shift_r, keyboard.Key.shift_l):
                self.modifiers.add('shift'); return

            if key == keyboard.Key.esc:
                self.hide_ui(); return

            self._check_window_changed()
            key_char = getattr(key, 'char', None)

            visible = self.root.winfo_viewable()

            if key == keyboard.Key.tab and visible and self.current_suggestions:
                self.root.after(1, lambda: self.select_suggestion(self.selected_index, extra_erase=0))
                return

            if key == keyboard.Key.down and visible and self.current_suggestions:
                self.selected_index = (self.selected_index + 1) % len(self.current_suggestions)
                self.root.after(0, lambda: self._highlight_row(self.selected_index))
                return

            if key == keyboard.Key.up and visible and self.current_suggestions:
                self.selected_index = (self.selected_index - 1) % len(self.current_suggestions)
                self.root.after(0, lambda: self._highlight_row(self.selected_index))
                return

            if self.current_suggestions and visible and key_char in ['1', '2', '3', '4']:
                idx = int(key_char) - 1
                if idx < len(self.current_suggestions):
                    self.root.after(1, lambda: self.select_suggestion(idx, extra_erase=1))
                    return

            if key_char is not None:
                if re.match(r"[a-zA-Z\-']", key_char):
                    self.word_buffer += key_char
                else:
                    if self.word_buffer.strip():
                        w = self.word_buffer.strip()
                        self.context_words.append(w)
                        if len(self.context_words) > 12:
                            self.context_words.pop(0)
                        record_user_word(w)
                    self.word_buffer = ""
                    self.hide_ui()
                    return
                self.trigger_backend_check()

            elif key in [keyboard.Key.space, keyboard.Key.enter]:
                if self.word_buffer.strip():
                    w = self.word_buffer.strip()
                    self.context_words.append(w)
                    if len(self.context_words) > 12:
                        self.context_words.pop(0)
                    record_user_word(w)
                self.word_buffer = ""
                self.hide_ui()

            elif key == keyboard.Key.backspace:
                if self.word_buffer:
                    self.word_buffer = self.word_buffer[:-1]
                    self.trigger_backend_check()
                else:
                    self.hide_ui()
            else:
                self.word_buffer = ""
                self.hide_ui()

        except Exception as e:
            print("[KEY PRESS ERROR]", e)

    def on_key_release(self, key):
        try:
            if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                self.modifiers.discard('ctrl')
            elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r):
                self.modifiers.discard('alt')
            elif key in (keyboard.Key.shift, keyboard.Key.shift_r, keyboard.Key.shift_l):
                self.modifiers.discard('shift')
        except:
            pass
#prediction "engine"
    def trigger_backend_check(self):
        if len(self.word_buffer.strip()) < 2:
            self.hide_ui()
            return

        current_time = time.time()
        self.last_request_time = current_time

        def delayed():
            time.sleep(DEBOUNCE_DELAY)
            if current_time != self.last_request_time:
                return
            self.root.after(0, lambda: self.fetch_predictions(self.word_buffer.strip()))

        threading.Thread(target=delayed, daemon=True).start()

    def fetch_predictions(self, active_word):
        if len(active_word) < 2:
            self.hide_ui()
            return

        is_title = active_word[0].isupper() if active_word else False
        is_upper = active_word.isupper()
        lowered = active_word.lower()

        if lowered in WORD_SET:
            if not any(w != lowered and w.startswith(lowered) for w in WORD_LIST):
                self.hide_ui()
                return

        if lowered in self.prediction_cache:
            raw_suggestions = self.prediction_cache[lowered]["suggestions"]
        else:
            filtered_pool = [w for w in WORD_LIST if w.startswith(lowered[0])]
            if not filtered_pool:
                filtered_pool = WORD_LIST

            close = difflib.get_close_matches(
                lowered, filtered_pool, n=MAX_SUGGESTIONS * 3, cutoff=0.52
            )

            raw_suggestions = rank_suggestions(close)[:MAX_SUGGESTIONS]

            if len(self.prediction_cache) >= PREDICTION_CACHE_MAX:
                oldest_key = next(iter(self.prediction_cache))
                del self.prediction_cache[oldest_key]

            self.prediction_cache[lowered] = {"suggestions": raw_suggestions}

        if not raw_suggestions:
            self.hide_ui()
            return

        processed = []
        for s in raw_suggestions:
            if is_upper:
                processed.append(s.upper())
            elif is_title:
                processed.append(s.capitalize())
            else:
                processed.append(s)

        self.current_suggestions = processed[:MAX_SUGGESTIONS]
        self.selected_index = 0
        self.update_ui(self.current_suggestions, active_word)

# word injection/paste
    def _on_row_click(self, idx):
        if idx < len(self.current_suggestions):
            threading.Thread(target=self.inject_word, args=(self.current_suggestions[idx], 0), daemon=True).start()

    def select_suggestion(self, idx, extra_erase=0):
        if idx >= len(self.current_suggestions):
            return
        threading.Thread(
            target=self.inject_word,
            args=(self.current_suggestions[idx], extra_erase),
            daemon=True
        ).start()

    def inject_word(self, target_word, extra_erase):
        self.is_injecting = True
        try:
            typed_word = self.word_buffer
            erase_count = len(typed_word) + extra_erase

            self.learn_choice(typed_word, target_word)

            if self.last_foreground_hwnd:
                ctypes.windll.user32.SetForegroundWindow(self.last_foreground_hwnd)
                time.sleep(0.01)

            self.root.after(0, self.hide_ui)

            for _ in range(erase_count):
                self.keyboard_controller.press(keyboard.Key.backspace)
                self.keyboard_controller.release(keyboard.Key.backspace)
                time.sleep(0.01)

            self.keyboard_controller.type(target_word + " ")

            self.context_words.append(target_word)
            if len(self.context_words) > 12:
                self.context_words.pop(0)

            record_user_word(target_word)

            self.word_buffer = ""
        except Exception as e:
            print("[INJECTION ERROR]", e)
        finally:
            self.is_injecting = False

#UI render
    def _highlight_row(self, idx):
        for i, row in enumerate(self.suggestion_rows):
            sel = i == idx
            row.config(bg=C["bg_top_row"] if sel else C["bg"])
            self.hotkey_labels[i].config(
                bg=C["num_bg_top"] if sel else C["num_bg"],
                fg=C["num_fg_top"] if sel else C["num_fg"],
            )
            self.suggestion_labels[i].config(
                bg=C["bg_top_row"] if sel else C["bg"],
                fg=C["text_top"] if sel else C["text_main"],
                font=("Segoe UI", 10, "bold") if sel else ("Segoe UI", 10),
            )

    def update_ui(self, suggestions, echo=""):
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd or (self._last_hwnd_for_buffer and hwnd != self._last_hwnd_for_buffer):
                self.word_buffer = ""
                self.hide_ui()
                return

            num_suggs = len(suggestions)
            for i in range(len(self.suggestion_labels)):
                if i < num_suggs:
                    self.suggestion_rows[i].pack(fill="x", side="top")
                    self.suggestion_labels[i].config(text=suggestions[i])
                else:
                    self.suggestion_rows[i].pack_forget()

            self._highlight_row(self.selected_index)

            if echo:
                self._echo_label.config(text=echo)
                if not self._echo_label.winfo_manager():
                    self._echo_label.pack(fill="x", before=self.chips_container)
            else:
                if self._echo_label.winfo_manager():
                    self._echo_label.pack_forget()

            max_len = max((len(s) for s in suggestions), default=10)
            width = max(200, min(320, 140 + max_len * 7))

            pos = self.get_system_caret_position()
            if pos:
                x, y = pos
                self.last_ui_x, self.last_ui_y = x, y
            else:
                if self.root.winfo_viewable() and self.last_ui_x != 0:
                    x, y = self.last_ui_x, self.last_ui_y
                else:
                    x, y = self.get_mouse_fallback_position()
                    self.last_ui_x, self.last_ui_y = x, y

            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()

            if x + width > screen_w - 12:
                x = screen_w - width - 12

            self.root.geometry(f"{width}x1+{x}+{y}")
            self.root.update_idletasks()
            req_height = self.frame.winfo_reqheight()

            if y + req_height > screen_h - 12:
                y -= (req_height + 24)

            self.root.geometry(f"{width}x{req_height}+{x}+{y}")

            if not self.root.winfo_viewable():
                self.root.deiconify()
                self.root.attributes("-alpha", 1.0)

                if not self.overlay_watch_running:
                    self.overlay_watch_running = True

                    def _watcher():
                        try:
                            while self.overlay_watch_running and self.root.winfo_viewable():
                                time.sleep(0.1)
                                curr = ctypes.windll.user32.GetForegroundWindow()
                                curr_title = self.get_window_title(curr)
                                if not curr or curr != self.last_foreground_hwnd \
                                        or curr_title != self.last_foreground_title:
                                    self.word_buffer = ""
                                    self.root.after(0, self.hide_ui)
                                    break
                        except:
                            pass
                        finally:
                            self.overlay_watch_running = False

                    threading.Thread(target=_watcher, daemon=True).start()

        except Exception as e:
            print("[UI UPDATE RUNTIME ERROR]", e)

    def hide_ui(self):
        self.last_ui_x = 0
        self.last_ui_y = 0
        self.root.attributes("-alpha", 0.0)
        self.root.withdraw()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    assistant = DesktopAssistantUI()
    assistant.run()