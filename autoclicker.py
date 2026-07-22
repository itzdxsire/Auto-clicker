"""
Simple AutoClicker for Windows
---------------------------------------
A modern, card-based autoclicker with:
  - CPS (clicks per second) control
  - CDC (click duty cycle) control
  - Left / Right / Middle click support
  - Fully custom hotkey capture (press any key or combo)
  - Sidebar navigation (Main / Settings / About)
  - Selectable accent color themes + custom color picker

Requirements:
  - Windows (uses the Win32 SendInput API directly via ctypes, no
    third-party packages required)
  - Python 3.8+ with tkinter (ships with the standard python.org
    installer on Windows)

Run:
  python autoclicker.py

Build a standalone .exe (run this ON Windows):
  pip install pyinstaller
  pyinstaller --onefile --noconsole --name AutoClicker autoclicker.py
"""

import ctypes
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser

if sys.platform != "win32":
    print("This autoclicker uses the Win32 API and only runs on Windows.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Low level mouse click simulation (Win32 SendInput)
# ---------------------------------------------------------------------------

PUL = ctypes.POINTER(ctypes.c_ulong)


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", PUL),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", InputUnion)]


INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

user32 = ctypes.windll.user32

DOWN_FLAGS = {"Left": MOUSEEVENTF_LEFTDOWN, "Right": MOUSEEVENTF_RIGHTDOWN, "Middle": MOUSEEVENTF_MIDDLEDOWN}
UP_FLAGS = {"Left": MOUSEEVENTF_LEFTUP, "Right": MOUSEEVENTF_RIGHTUP, "Middle": MOUSEEVENTF_MIDDLEUP}


def _send(flag):
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.mi = MouseInput(0, 0, 0, flag, 0, ctypes.pointer(extra))
    inp = Input(INPUT_MOUSE, ii)
    user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))


def mouse_down(button="Left"):
    _send(DOWN_FLAGS[button])


def mouse_up(button="Left"):
    _send(UP_FLAGS[button])


def is_key_pressed(vk_code):
    return (user32.GetAsyncKeyState(vk_code) & 0x8000) != 0


# ---------------------------------------------------------------------------
# Virtual key map (for custom hotkey capture + display)
# ---------------------------------------------------------------------------

VK_SHIFT, VK_CONTROL, VK_MENU, VK_ESCAPE = 0x10, 0x11, 0x12, 0x1B
VK_XBUTTON1, VK_XBUTTON2, VK_MBUTTON = 0x05, 0x06, 0x04
MODIFIER_VKS = (VK_SHIFT, VK_CONTROL, VK_MENU)

VK_NAMES = {}
for i in range(26):
    VK_NAMES[0x41 + i] = chr(ord('A') + i)
for i in range(10):
    VK_NAMES[0x30 + i] = str(i)
for i in range(1, 13):
    VK_NAMES[0x6F + i] = f"F{i}"
VK_NAMES.update({
    VK_SHIFT: "Shift", VK_CONTROL: "Ctrl", VK_MENU: "Alt",
    0x20: "Space", 0x09: "Tab", 0x0D: "Enter", 0x08: "Backspace",
    0x14: "CapsLock", VK_ESCAPE: "Esc",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End",
    0x21: "PageUp", 0x22: "PageDown", 0xC0: "`",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    VK_MBUTTON: "Mouse 3 (Middle)",
    VK_XBUTTON1: "Mouse 4 (Back)",
    VK_XBUTTON2: "Mouse 5 (Forward)",
})
MAIN_KEY_CANDIDATES = [vk for vk in VK_NAMES if vk not in MODIFIER_VKS]


def vk_name(vk):
    return VK_NAMES.get(vk, f"VK{vk:#x}")


def combo_to_string(combo):
    return " + ".join(vk_name(vk) for vk in combo) if combo else "None"


def is_combo_pressed(combo):
    return bool(combo) and all(is_key_pressed(vk) for vk in combo)


# ---------------------------------------------------------------------------
# Colors / style constants
# ---------------------------------------------------------------------------

BG = "#0b0b0f"
SIDEBAR_BG = "#111116"
CARD_BG = "#15151c"
CARD_BORDER = "#24242e"
TEXT = "#e9e9ee"
SUBTEXT = "#9a9aa5"
STOPPED_RED = "#ef4444"

ACCENT_PRESETS = ["#7c3aed", "#3b82f6", "#22c55e", "#ef4444", "#f97316", "#ec4899", "#6b7280"]


# ---------------------------------------------------------------------------
# Clicker worker thread
# ---------------------------------------------------------------------------

class ClickerEngine:
    def __init__(self):
        self.running = False
        self.enabled = False
        self.cps = 20.0
        self.cdc = 50.0
        self.button = "Left"
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            if self.enabled:
                period = 1.0 / max(self.cps, 0.1)
                down_time = max(period * (self.cdc / 100.0), 0.001)
                up_time = max(period - down_time, 0.001)
                mouse_down(self.button)
                time.sleep(down_time)
                mouse_up(self.button)
                time.sleep(up_time)
            else:
                time.sleep(0.02)


# ---------------------------------------------------------------------------
# Small reusable UI helpers
# ---------------------------------------------------------------------------

def card(parent, **kw):
    f = tk.Frame(parent, bg=CARD_BG, highlightbackground=CARD_BORDER,
                 highlightthickness=1, bd=0)
    f.configure(**kw)
    return f


def card_title(parent, text, accent):
    lbl = tk.Label(parent, text=text, bg=CARD_BG, fg=accent,
                    font=("Segoe UI", 11, "bold"))
    lbl.pack(anchor="w", padx=16, pady=(14, 8))
    return lbl


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class AutoClickerApp:
    def __init__(self, root):
        self.root = root
        self.engine = ClickerEngine()
        self.engine.start()

        self.accent = ACCENT_PRESETS[0]
        self.cps_var = tk.StringVar(value="20")
        self.cdc_var = tk.StringVar(value="50")
        self.button_var = tk.StringVar(value="Left")

        self.hotkey_combo = [0x75]  # F6
        self.capturing_hotkey = False
        self.capture_start_time = 0.0
        self._hotkey_prev_state = False
        self.trigger_mode = "Toggle"  # or "Hold"

        self.accent_swatch_canvases = []

        root.title("Simple AutoClicker")
        root.configure(bg=BG)
        root.geometry("940x800")
        root.minsize(900, 700)
        root.resizable(True, True)

        self._build_layout()
        self._show_page("main")
        self._style_mode_buttons()
        self._apply_accent()
        self._poll_hotkey()

    # ----- top level layout -----
    def _build_layout(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)

        # Sidebar
        self.sidebar = tk.Frame(outer, bg=SIDEBAR_BG, width=190)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        title_row = tk.Frame(self.sidebar, bg=SIDEBAR_BG)
        title_row.pack(fill="x", pady=(20, 24), padx=16)
        self.logo_label = tk.Label(title_row, text="\u2716", bg=SIDEBAR_BG,
                                    font=("Segoe UI", 16, "bold"))
        self.logo_label.pack(side="left")
        tk.Label(title_row, text=" AutoClicker", bg=SIDEBAR_BG, fg=TEXT,
                 font=("Segoe UI", 13, "bold")).pack(side="left")

        self.nav_buttons = {}
        for key, label, icon in [("main", "Main", "\u2302"),
                                  ("settings", "Settings", "\u2699"),
                                  ("about", "About", "\u2139")]:
            b = tk.Label(self.sidebar, text=f"  {icon}   {label}", bg=SIDEBAR_BG, fg=SUBTEXT,
                         font=("Segoe UI", 11), anchor="w", padx=8, pady=10, cursor="hand2")
            b.pack(fill="x", padx=10, pady=2)
            b.bind("<Button-1>", lambda e, k=key: self._show_page(k))
            self.nav_buttons[key] = b

        tk.Label(self.sidebar, text="v1.0.0", bg=SIDEBAR_BG, fg=SUBTEXT,
                 font=("Segoe UI", 8)).pack(side="bottom", pady=14, padx=16, anchor="w")

        # Content area (pages stacked / swapped)
        self.content = tk.Frame(outer, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)

        self.pages = {
            "main": self._build_main_page(self.content),
            "settings": self._build_settings_page(self.content),
            "about": self._build_about_page(self.content),
        }

    def _show_page(self, key):
        for k, frame in self.pages.items():
            frame.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.configure(bg="#1d1d27", fg=TEXT)
            else:
                btn.configure(bg=SIDEBAR_BG, fg=SUBTEXT)

    # ----- MAIN PAGE -----
    def _build_main_page(self, parent):
        page = tk.Frame(parent, bg=BG)
        pad = 20

        # --- top row: status card + start/stop ---
        top_row = tk.Frame(page, bg=BG)
        top_row.pack(fill="x", padx=pad, pady=(pad, 12))

        self.status_card = card(top_row)
        self.status_card.pack(side="left", fill="both", expand=True, padx=(0, 12), ipady=10)

        status_inner = tk.Frame(self.status_card, bg=CARD_BG)
        status_inner.pack(fill="both", expand=True, padx=16, pady=16)

        self.status_canvas = tk.Canvas(status_inner, width=70, height=70, bg=CARD_BG, highlightthickness=0)
        self.status_canvas.pack(side="left", padx=(0, 16))

        text_col = tk.Frame(status_inner, bg=CARD_BG)
        text_col.pack(side="left", fill="both", expand=True)
        status_row = tk.Frame(text_col, bg=CARD_BG)
        status_row.pack(anchor="w")
        tk.Label(status_row, text="Status: ", bg=CARD_BG, fg=TEXT,
                 font=("Segoe UI", 13)).pack(side="left")
        self.status_value_label = tk.Label(status_row, text="Stopped", bg=CARD_BG, fg=STOPPED_RED,
                                            font=("Segoe UI", 13, "bold"))
        self.status_value_label.pack(side="left")
        self.status_sub_label = tk.Label(text_col, text="Press your hotkey to start clicking",
                                          bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 9))
        self.status_sub_label.pack(anchor="w", pady=(4, 0))

        btn_col = tk.Frame(top_row, bg=BG)
        btn_col.pack(side="left", fill="y")
        self.start_btn = tk.Button(btn_col, text="\u25B6  Start (F6)", font=("Segoe UI", 11, "bold"),
                                    relief="flat", bd=0, width=18, height=2, command=self._start_clicking)
        self.start_btn.pack(pady=(0, 8))
        self.stop_btn = tk.Button(btn_col, text="\u25A0  Stop (F6)", font=("Segoe UI", 11, "bold"),
                                   relief="flat", bd=0, width=18, height=2, command=self._stop_clicking)
        self.stop_btn.pack()

        # --- middle row: left column (cps/cdc) + right column (button/hotkey) ---
        mid_row = tk.Frame(page, bg=BG)
        mid_row.pack(fill="both", expand=True, padx=pad, pady=6)

        left_col = tk.Frame(mid_row, bg=BG)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right_col = tk.Frame(mid_row, bg=BG, width=270)
        right_col.pack(side="left", fill="y")
        right_col.pack_propagate(False)

        # CPS card
        cps_card = card(left_col)
        cps_card.pack(fill="x", pady=(0, 12))
        self.cps_title = card_title(cps_card, "\u26A1 CPS (Clicks Per Second)", self.accent)
        cps_row = tk.Frame(cps_card, bg=CARD_BG)
        cps_row.pack(fill="x", padx=16, pady=(0, 16))
        self.cps_spin = tk.Spinbox(cps_row, from_=1, to=200, textvariable=self.cps_var,
                                    font=("Segoe UI", 13), width=10, relief="flat",
                                    bg="#1c1c25", fg=TEXT, insertbackground=TEXT,
                                    buttonbackground="#1c1c25", justify="left")
        self.cps_spin.pack(fill="x", ipady=6)
        self.cps_var.trace_add("write", lambda *a: self._sync_cps())

        # CDC card
        cdc_card = card(left_col)
        cdc_card.pack(fill="x")
        self.cdc_title = card_title(cdc_card, "\u25D4 CDC (Click Duty Cycle)", self.accent)
        cdc_row = tk.Frame(cdc_card, bg=CARD_BG)
        cdc_row.pack(fill="x", padx=16)
        self.cdc_spin = tk.Spinbox(cdc_row, from_=1, to=99, textvariable=self.cdc_var,
                                    font=("Segoe UI", 13), width=10, relief="flat",
                                    bg="#1c1c25", fg=TEXT, insertbackground=TEXT,
                                    buttonbackground="#1c1c25", justify="left")
        self.cdc_spin.pack(side="left", fill="x", expand=True, ipady=6)
        tk.Label(cdc_row, text="%", bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 12)).pack(side="left", padx=8)
        self.cdc_var.trace_add("write", lambda *a: self._sync_cdc())

        note = tk.Frame(cdc_card, bg=CARD_BG)
        note.pack(fill="x", padx=16, pady=(10, 16))
        tk.Label(note, text="\u2139  Duty Cycle determines the ratio of click\n     time to the total click cycle.",
                 bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 9), justify="left").pack(anchor="w")

        # Mouse button card
        mb_card = card(right_col)
        mb_card.pack(fill="x", pady=(0, 12))
        self.mb_title = card_title(mb_card, "\U0001F5B1 Mouse Button", self.accent)
        self.radio_buttons = []
        for opt in ["Left", "Right", "Middle"]:
            rb = tk.Radiobutton(mb_card, text=f"{opt} Click", variable=self.button_var, value=opt,
                                 bg=CARD_BG, fg=TEXT, selectcolor=CARD_BG, activebackground=CARD_BG,
                                 activeforeground=TEXT, font=("Segoe UI", 11),
                                 command=self._sync_button, anchor="w")
            rb.pack(fill="x", padx=16, pady=4)
            self.radio_buttons.append(rb)
        tk.Frame(mb_card, bg=CARD_BG, height=10).pack()

        # Hotkey card
        hk_card = card(right_col)
        hk_card.pack(fill="x")
        self.hk_title = card_title(hk_card, "\u2328 Hotkey", self.accent)
        hk_row = tk.Frame(hk_card, bg=CARD_BG)
        hk_row.pack(fill="x", padx=16, pady=(0, 16))
        self.hotkey_display = tk.Label(hk_row, text=combo_to_string(self.hotkey_combo), bg="#1c1c25",
                                        fg=TEXT, font=("Segoe UI", 11, "bold"), pady=8)
        self.hotkey_display.pack(fill="x", pady=(0, 8))
        self.set_hotkey_btn = tk.Button(hk_row, text="Set Hotkey", relief="flat", bd=0,
                                         bg="#24242e", fg=TEXT, font=("Segoe UI", 10),
                                         command=self._begin_capture)
        self.set_hotkey_btn.pack(fill="x", ipady=6)
        tk.Label(hk_row, text="Works with keyboard keys or side mouse buttons\n(Mouse 4 / Mouse 5).",
                 bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 8), justify="left").pack(anchor="w", pady=(6, 0))

        mode_label = tk.Label(hk_row, text="Trigger Mode", bg=CARD_BG, fg=SUBTEXT,
                               font=("Segoe UI", 9, "bold"))
        mode_label.pack(anchor="w", pady=(14, 4))
        mode_row = tk.Frame(hk_row, bg=CARD_BG)
        mode_row.pack(fill="x")
        self.toggle_mode_btn = tk.Button(mode_row, text="Toggle", relief="flat", bd=0,
                                          font=("Segoe UI", 10, "bold"),
                                          command=lambda: self._set_trigger_mode("Toggle"))
        self.toggle_mode_btn.pack(side="left", fill="x", expand=True, padx=(0, 4), ipady=6)
        self.hold_mode_btn = tk.Button(mode_row, text="Hold", relief="flat", bd=0,
                                        font=("Segoe UI", 10, "bold"),
                                        command=lambda: self._set_trigger_mode("Hold"))
        self.hold_mode_btn.pack(side="left", fill="x", expand=True, padx=(4, 0), ipady=6)
        tk.Label(hk_row, text="Toggle: press once to start, again to stop.\nHold: clicks only while the key is held down.",
                 bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 8), justify="left").pack(anchor="w", pady=(8, 0))

        # --- theme card ---
        theme_card = card(page)
        theme_card.pack(fill="x", padx=pad, pady=(12, pad))
        self.theme_title = card_title(theme_card, "\U0001F3A8 Theme / Color", self.accent)
        swatch_row = tk.Frame(theme_card, bg=CARD_BG)
        swatch_row.pack(fill="x", padx=16, pady=(0, 18))

        for color in ACCENT_PRESETS:
            self._make_swatch(swatch_row, color)
        self._make_custom_swatch(swatch_row)

        return page

    def _make_swatch(self, parent, color):
        c = tk.Canvas(parent, width=40, height=40, bg=CARD_BG, highlightthickness=0, cursor="hand2")
        c.pack(side="left", padx=6)
        c.create_oval(4, 4, 36, 36, fill=color, outline="")
        c.color = color
        c.bind("<Button-1>", lambda e, col=color: self._select_accent(col))
        self.accent_swatch_canvases.append(c)

    def _make_custom_swatch(self, parent):
        c = tk.Canvas(parent, width=40, height=40, bg=CARD_BG, highlightthickness=0, cursor="hand2")
        c.pack(side="left", padx=6)
        c.create_oval(4, 4, 36, 36, fill=CARD_BG, outline=SUBTEXT, dash=(2, 2))
        c.create_text(20, 20, text="+", fill=SUBTEXT, font=("Segoe UI", 14, "bold"))
        c.color = None
        c.bind("<Button-1>", lambda e: self._pick_custom_accent())

    # ----- SETTINGS PAGE -----
    def _build_settings_page(self, parent):
        page = tk.Frame(parent, bg=BG)
        wrap = card(page)
        wrap.pack(fill="x", padx=20, pady=20)
        card_title(wrap, "\u2699 Settings", self.accent)
        tk.Label(wrap, text="This build keeps things simple - all options live on the Main page.\n"
                             "(CPS, CDC, mouse button, hotkey, and theme.)",
                 bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 10), justify="left").pack(
            anchor="w", padx=16, pady=(0, 18))
        return page

    # ----- ABOUT PAGE -----
    def _build_about_page(self, parent):
        page = tk.Frame(parent, bg=BG)
        wrap = card(page)
        wrap.pack(fill="x", padx=20, pady=20)
        card_title(wrap, "\u2139 About", self.accent)
        tk.Label(wrap, text="Simple AutoClicker  \u2022  v1.0.0\n\n"
                             "Built with Python + tkinter. Uses the Win32 SendInput API "
                             "to simulate mouse clicks, and GetAsyncKeyState to listen for "
                             "your custom hotkey globally.\n\n"
                             "Some games and antivirus tools flag autoclickers - use responsibly.",
                 bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 10), justify="left", wraplength=600).pack(
            anchor="w", padx=16, pady=(0, 18))
        return page

    # ----- value syncing -----
    def _sync_cps(self):
        try:
            v = float(self.cps_var.get())
            if v > 0:
                self.engine.cps = v
        except ValueError:
            pass

    def _sync_cdc(self):
        try:
            v = float(self.cdc_var.get())
            if 0 < v < 100:
                self.engine.cdc = v
        except ValueError:
            pass

    def _sync_button(self):
        self.engine.button = self.button_var.get()

    # ----- start/stop -----
    def _start_clicking(self):
        self.engine.enabled = True
        self._refresh_status()

    def _stop_clicking(self):
        self.engine.enabled = False
        self._refresh_status()

    def _toggle(self):
        if self.engine.enabled:
            self._stop_clicking()
        else:
            self._start_clicking()

    def _set_trigger_mode(self, mode):
        self.trigger_mode = mode
        self.engine.enabled = False
        self._style_mode_buttons()
        self._refresh_status()

    def _style_mode_buttons(self):
        if self.trigger_mode == "Toggle":
            self.toggle_mode_btn.config(bg=self.accent, fg="#ffffff")
            self.hold_mode_btn.config(bg="#24242e", fg=SUBTEXT)
        else:
            self.hold_mode_btn.config(bg=self.accent, fg="#ffffff")
            self.toggle_mode_btn.config(bg="#24242e", fg=SUBTEXT)

    def _refresh_status(self):
        hk = combo_to_string(self.hotkey_combo)
        self.start_btn.config(text=f"\u25B6  Start ({hk})")
        self.stop_btn.config(text=f"\u25A0  Stop ({hk})")
        hold_mode = (self.trigger_mode == "Hold")
        if self.engine.enabled:
            self.status_value_label.config(text="Running", fg=self.accent)
            sub = f"Holding {hk} to keep clicking" if hold_mode else "Press your hotkey to stop clicking"
            self.status_sub_label.config(text=sub)
            self.start_btn.config(bg="#24242e", fg=SUBTEXT, state="normal")
            self.stop_btn.config(bg=self.accent, fg="#ffffff")
        else:
            self.status_value_label.config(text="Stopped", fg=STOPPED_RED)
            sub = f"Hold {hk} to click" if hold_mode else "Press your hotkey to start clicking"
            self.status_sub_label.config(text=sub)
            self.start_btn.config(bg=self.accent, fg="#ffffff")
            self.stop_btn.config(bg="#24242e", fg=SUBTEXT)
        state = "disabled" if hold_mode else "normal"
        self.start_btn.config(state=state)
        self.stop_btn.config(state=state)
        self._draw_status_ring()

    def _draw_status_ring(self):
        c = self.status_canvas
        c.delete("all")
        color = self.accent if self.engine.enabled else "#3a3a45"
        c.create_oval(6, 6, 64, 64, outline=color, width=3)
        # simple cursor arrow
        c.create_polygon(27, 22, 27, 46, 33, 40, 37, 48, 41, 46, 37, 38, 45, 38,
                          fill=TEXT, outline="")
        if self.engine.enabled:
            for dx, dy in [(10, 8), (60, 10), (8, 58)]:
                c.create_line(dx, dy, dx + 6, dy + 4, fill=color, width=2)

    # ----- hotkey capture -----
    def _begin_capture(self):
        self.capturing_hotkey = True
        self.capture_start_time = time.time()
        self.set_hotkey_btn.config(text="Press keys... (Esc cancels)", state="disabled")
        self.hotkey_display.config(text="...")

    def _finish_capture(self, combo):
        self.capturing_hotkey = False
        if combo:
            self.hotkey_combo = combo
        self.hotkey_display.config(text=combo_to_string(self.hotkey_combo))
        self.set_hotkey_btn.config(text="Set Hotkey", state="normal")
        self._refresh_status()

    def _poll_hotkey(self):
        if self.capturing_hotkey:
            if time.time() - self.capture_start_time > 0.25:
                if is_key_pressed(VK_ESCAPE):
                    self._finish_capture(None)
                else:
                    pressed_main = [vk for vk in MAIN_KEY_CANDIDATES if is_key_pressed(vk)]
                    if pressed_main:
                        combo = [m for m in MODIFIER_VKS if is_key_pressed(m)]
                        combo.append(pressed_main[0])
                        self._finish_capture(combo)
        else:
            pressed = is_combo_pressed(self.hotkey_combo)
            if self.trigger_mode == "Hold":
                if pressed != self.engine.enabled:
                    self.engine.enabled = pressed
                    self._refresh_status()
            else:  # Toggle
                if pressed and not self._hotkey_prev_state:
                    self._toggle()
            self._hotkey_prev_state = pressed
        self.root.after(30, self._poll_hotkey)

    # ----- theming -----
    def _select_accent(self, color):
        self.accent = color
        self._apply_accent()

    def _pick_custom_accent(self):
        c = colorchooser.askcolor(color=self.accent)[1]
        if c:
            self.accent = c
            self._apply_accent()

    def _apply_accent(self):
        self.logo_label.configure(fg=self.accent)
        for title in [self.cps_title, self.cdc_title, self.mb_title, self.hk_title, self.theme_title]:
            title.configure(fg=self.accent)
        for rb in self.radio_buttons:
            rb.configure(selectcolor="#1c1c25", fg=TEXT)
            rb.configure(highlightbackground=self.accent)
        for k, btn in self.nav_buttons.items():
            pass
        # redraw swatch selection rings
        for c in self.accent_swatch_canvases:
            c.delete("ring")
            if c.color == self.accent:
                c.create_oval(1, 1, 39, 39, outline=self.accent, width=2, tags="ring")
        self._refresh_status()


def main():
    root = tk.Tk()
    app = AutoClickerApp(root)

    def on_close():
        app.engine.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
