"""
Customizable Auto Clicker for Windows
---------------------------------------
Features:
  - CPS (clicks per second) control
  - CDC (click duty cycle) control - the % of each click cycle the
    mouse button is held "down" vs "up"
  - Fully custom hotkeys - click "Set Hotkey" and press any key or
    key combo (e.g. Shift+R, Ctrl+Alt+F, etc.) to bind it
  - Customizable UI: pick from preset color themes, or choose your
    own two colors for a custom gradient background

Requirements:
  - Windows (uses the Win32 SendInput API directly via ctypes, no
    third-party packages required)
  - Python 3.8+ with tkinter (this ships with the standard python.org
    installer on Windows by default)

Run:
  python autoclicker.py

Turn into a standalone .exe (optional, run this ON Windows):
  pip install pyinstaller
  pyinstaller --onefile --noconsole --name AutoClicker autoclicker.py
  (the .exe will show up in the generated "dist" folder)

Note: some games/anti-cheat systems and some antivirus tools flag
autoclickers. Use responsibly and check the rules of anything you use
this with.
"""

import ctypes
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, colorchooser

if sys.platform != "win32":
    print("This autoclicker uses the Win32 API and only runs on Windows.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Low level mouse click simulation (Win32 SendInput) - no external packages
# ---------------------------------------------------------------------------

PUL = ctypes.POINTER(ctypes.c_ulong)


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
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

user32 = ctypes.windll.user32


def _send(flag):
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.mi = MouseInput(0, 0, 0, flag, 0, ctypes.pointer(extra))
    inp = Input(INPUT_MOUSE, ii)
    user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))


def mouse_down(right=False):
    _send(MOUSEEVENTF_RIGHTDOWN if right else MOUSEEVENTF_LEFTDOWN)


def mouse_up(right=False):
    _send(MOUSEEVENTF_RIGHTUP if right else MOUSEEVENTF_LEFTUP)


def is_key_pressed(vk_code):
    return (user32.GetAsyncKeyState(vk_code) & 0x8000) != 0


# ---------------------------------------------------------------------------
# Virtual key code map (for custom hotkey capture + display)
# ---------------------------------------------------------------------------

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_ESCAPE = 0x1B

MODIFIER_VKS = (VK_SHIFT, VK_CONTROL, VK_MENU)

VK_NAMES = {}
# Letters
for i in range(26):
    VK_NAMES[0x41 + i] = chr(ord('A') + i)
# Digits
for i in range(10):
    VK_NAMES[0x30 + i] = str(i)
# Function keys
for i in range(1, 13):
    VK_NAMES[0x6F + i] = f"F{i}"

VK_NAMES.update({
    VK_SHIFT: "Shift",
    VK_CONTROL: "Ctrl",
    VK_MENU: "Alt",
    0x20: "Space",
    0x09: "Tab",
    0x0D: "Enter",
    0x08: "Backspace",
    0x14: "CapsLock",
    VK_ESCAPE: "Esc",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End",
    0x21: "PageUp", 0x22: "PageDown",
    0xC0: "`",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
})

# Candidate keys scanned while capturing a new hotkey (non-modifier "main" keys)
MAIN_KEY_CANDIDATES = [vk for vk in VK_NAMES.keys() if vk not in MODIFIER_VKS]


def vk_name(vk):
    return VK_NAMES.get(vk, f"VK{vk:#x}")


def combo_to_string(combo):
    if not combo:
        return "None"
    return " + ".join(vk_name(vk) for vk in combo)


def is_combo_pressed(combo):
    if not combo:
        return False
    return all(is_key_pressed(vk) for vk in combo)


# ---------------------------------------------------------------------------
# Color themes
# ---------------------------------------------------------------------------

THEMES = {
    "Midnight":  {"g1": "#0f0c29", "g2": "#302b63", "accent": "#7b6ff6", "fg": "#f0f0ff"},
    "Ocean":     {"g1": "#005c97", "g2": "#363795", "accent": "#00c6ff", "fg": "#ffffff"},
    "Sunset":    {"g1": "#ff512f", "g2": "#dd2476", "accent": "#ffdf6b", "fg": "#ffffff"},
    "Neon":      {"g1": "#0f2027", "g2": "#2c5364", "accent": "#39ff14", "fg": "#e8ffe8"},
    "Cotton Candy": {"g1": "#ff9a9e", "g2": "#a18cd1", "accent": "#ffffff", "fg": "#33254a"},
    "Slate":     {"g1": "#232526", "g2": "#414345", "accent": "#00d4ff", "fg": "#ffffff"},
}


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % rgb


def interpolate_color(c1, c2, t):
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    return rgb_to_hex((
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    ))


# ---------------------------------------------------------------------------
# Clicker worker thread
# ---------------------------------------------------------------------------

class ClickerEngine:
    def __init__(self):
        self.running = False
        self.enabled = False
        self.cps = 10.0
        self.cdc = 50.0  # percent of the cycle spent "held down"
        self.right_click = False
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
                mouse_down(self.right_click)
                time.sleep(down_time)
                mouse_up(self.right_click)
                time.sleep(up_time)
            else:
                time.sleep(0.02)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class AutoClickerApp:
    def __init__(self, root):
        self.root = root
        self.engine = ClickerEngine()
        self.engine.start()

        self.theme_name = tk.StringVar(value="Midnight")
        self.custom_g1 = "#0f0c29"
        self.custom_g2 = "#302b63"
        self.use_custom = False

        self.cps_var = tk.DoubleVar(value=10.0)
        self.cdc_var = tk.DoubleVar(value=50.0)
        self.button_var = tk.StringVar(value="Left")

        # Hotkey state: default = F6 alone. Can be reset to any combo.
        self.hotkey_combo = [0x75]  # F6
        self.capturing_hotkey = False
        self.capture_start_time = 0.0
        self._hotkey_prev_state = False

        root.title("Auto Clicker")
        root.geometry("460x600")
        root.resizable(False, False)

        self.canvas = tk.Canvas(root, width=460, height=600, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.content = tk.Frame(self.canvas, bg="#1c1c1c")
        self.canvas.create_window(230, 300, window=self.content, width=420, height=560)

        self._build_ui()
        self._apply_theme()
        self._poll_hotkey()

    # ----- UI construction -----
    def _build_ui(self):
        self.title_label = tk.Label(self.content, text="AUTO CLICKER", font=("Segoe UI", 20, "bold"))
        self.title_label.pack(pady=(18, 4))

        self.status_label = tk.Label(self.content, text="STOPPED", font=("Segoe UI", 12, "bold"))
        self.status_label.pack(pady=(0, 16))

        # CPS
        cps_frame = tk.Frame(self.content)
        cps_frame.pack(fill="x", padx=24, pady=6)
        self.cps_title = tk.Label(cps_frame, text="Clicks Per Second (CPS)", font=("Segoe UI", 10, "bold"))
        self.cps_title.pack(anchor="w")
        cps_row = tk.Frame(cps_frame)
        cps_row.pack(fill="x")
        self.cps_scale = tk.Scale(cps_row, from_=1, to=50, orient="horizontal",
                                   variable=self.cps_var, resolution=0.5,
                                   showvalue=False, command=lambda v: self._sync_cps())
        self.cps_scale.pack(side="left", fill="x", expand=True)
        self.cps_entry = tk.Entry(cps_row, width=6, justify="center")
        self.cps_entry.pack(side="left", padx=(8, 0))
        self.cps_entry.insert(0, "10.0")
        self.cps_entry.bind("<Return>", lambda e: self._set_cps_from_entry())
        self.cps_entry.bind("<FocusOut>", lambda e: self._set_cps_from_entry())

        # CDC
        cdc_frame = tk.Frame(self.content)
        cdc_frame.pack(fill="x", padx=24, pady=6)
        self.cdc_title = tk.Label(cdc_frame, text="Click Duty Cycle (CDC) - % held down per click",
                                   font=("Segoe UI", 10, "bold"))
        self.cdc_title.pack(anchor="w")
        cdc_row = tk.Frame(cdc_frame)
        cdc_row.pack(fill="x")
        self.cdc_scale = tk.Scale(cdc_row, from_=1, to=95, orient="horizontal",
                                   variable=self.cdc_var, resolution=1,
                                   showvalue=False, command=lambda v: self._sync_cdc())
        self.cdc_scale.pack(side="left", fill="x", expand=True)
        self.cdc_entry = tk.Entry(cdc_row, width=6, justify="center")
        self.cdc_entry.pack(side="left", padx=(8, 0))
        self.cdc_entry.insert(0, "50")
        self.cdc_entry.bind("<Return>", lambda e: self._set_cdc_from_entry())
        self.cdc_entry.bind("<FocusOut>", lambda e: self._set_cdc_from_entry())

        # Mouse button
        opts_frame = tk.Frame(self.content)
        opts_frame.pack(fill="x", padx=24, pady=(10, 6))
        self.button_title = tk.Label(opts_frame, text="Mouse Button", font=("Segoe UI", 10, "bold"))
        self.button_title.pack(anchor="w")
        self.button_menu = ttk.Combobox(opts_frame, textvariable=self.button_var,
                                         values=["Left", "Right"], state="readonly", width=10)
        self.button_menu.pack(anchor="w", pady=(2, 0))
        self.button_menu.bind("<<ComboboxSelected>>", lambda e: self._sync_button())

        # Hotkey (custom capture)
        hotkey_frame = tk.Frame(self.content)
        hotkey_frame.pack(fill="x", padx=24, pady=(10, 6))
        self.hotkey_title = tk.Label(hotkey_frame, text="Toggle Hotkey", font=("Segoe UI", 10, "bold"))
        self.hotkey_title.pack(anchor="w")
        hotkey_row = tk.Frame(hotkey_frame)
        hotkey_row.pack(fill="x", pady=(2, 0))
        self.hotkey_display = tk.Label(hotkey_row, text=combo_to_string(self.hotkey_combo),
                                        font=("Segoe UI", 11, "bold"), relief="groove", padx=10, pady=6)
        self.hotkey_display.pack(side="left", fill="x", expand=True)
        self.set_hotkey_btn = tk.Button(hotkey_row, text="Set Hotkey", command=self._begin_capture)
        self.set_hotkey_btn.pack(side="left", padx=(8, 0))

        # Start/Stop button
        self.toggle_btn = tk.Button(self.content, text="START (or press hotkey)",
                                     font=("Segoe UI", 12, "bold"), relief="flat",
                                     command=self._toggle, height=2)
        self.toggle_btn.pack(fill="x", padx=24, pady=18)

        # Theme picker
        theme_frame = tk.Frame(self.content)
        theme_frame.pack(fill="x", padx=24, pady=(4, 4))
        self.theme_title = tk.Label(theme_frame, text="Theme", font=("Segoe UI", 10, "bold"))
        self.theme_title.pack(anchor="w")
        self.theme_menu = ttk.Combobox(theme_frame, textvariable=self.theme_name,
                                        values=list(THEMES.keys()) + ["Custom..."],
                                        state="readonly")
        self.theme_menu.pack(fill="x", pady=(2, 6))
        self.theme_menu.bind("<<ComboboxSelected>>", lambda e: self._on_theme_change())

        custom_row = tk.Frame(theme_frame)
        custom_row.pack(fill="x")
        self.pick_g1_btn = tk.Button(custom_row, text="Gradient Color 1", command=self._pick_color1)
        self.pick_g1_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.pick_g2_btn = tk.Button(custom_row, text="Gradient Color 2", command=self._pick_color2)
        self.pick_g2_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        self.hint_label = tk.Label(self.content,
                                    text="Hotkey works globally, even if this window\nisn't focused.",
                                    font=("Segoe UI", 9), justify="center")
        self.hint_label.pack(pady=(14, 0))

    # ----- value syncing -----
    def _sync_cps(self):
        v = round(self.cps_var.get(), 1)
        self.engine.cps = v
        self.cps_entry.delete(0, "end")
        self.cps_entry.insert(0, str(v))

    def _set_cps_from_entry(self):
        try:
            v = float(self.cps_entry.get())
            v = max(0.1, min(v, 200))
        except ValueError:
            v = self.cps_var.get()
        self.cps_var.set(v)
        self.engine.cps = v

    def _sync_cdc(self):
        v = round(self.cdc_var.get())
        self.engine.cdc = v
        self.cdc_entry.delete(0, "end")
        self.cdc_entry.insert(0, str(v))

    def _set_cdc_from_entry(self):
        try:
            v = float(self.cdc_entry.get())
            v = max(1, min(v, 99))
        except ValueError:
            v = self.cdc_var.get()
        self.cdc_var.set(v)
        self.engine.cdc = v

    def _sync_button(self):
        self.engine.right_click = (self.button_var.get() == "Right")

    def _toggle(self):
        self.engine.enabled = not self.engine.enabled
        self._refresh_status()

    def _refresh_status(self):
        if self.engine.enabled:
            self.status_label.config(text="RUNNING", fg=self._accent)
            self.toggle_btn.config(text="STOP (or press hotkey)")
        else:
            self.status_label.config(text="STOPPED", fg="#ff5555")
            self.toggle_btn.config(text="START (or press hotkey)")

    # ----- hotkey capture -----
    def _begin_capture(self):
        self.capturing_hotkey = True
        self.capture_start_time = time.time()
        self.set_hotkey_btn.config(text="...", state="disabled")
        self.hotkey_display.config(text="Press keys... (Esc to cancel)")

    def _finish_capture(self, combo):
        self.capturing_hotkey = False
        if combo:
            self.hotkey_combo = combo
        self.hotkey_display.config(text=combo_to_string(self.hotkey_combo))
        self.set_hotkey_btn.config(text="Set Hotkey", state="normal")

    def _poll_hotkey(self):
        if self.capturing_hotkey:
            # small grace period so the click that opened capture mode
            # isn't itself picked up
            if time.time() - self.capture_start_time > 0.25:
                if is_key_pressed(VK_ESCAPE):
                    self._finish_capture(None)  # cancel, keep old hotkey
                else:
                    pressed_main = [vk for vk in MAIN_KEY_CANDIDATES if is_key_pressed(vk)]
                    if pressed_main:
                        combo = [m for m in MODIFIER_VKS if is_key_pressed(m)]
                        combo.append(pressed_main[0])
                        self._finish_capture(combo)
        else:
            pressed = is_combo_pressed(self.hotkey_combo)
            if pressed and not self._hotkey_prev_state:
                self._toggle()
            self._hotkey_prev_state = pressed

        self.root.after(30, self._poll_hotkey)

    # ----- theming -----
    def _pick_color1(self):
        c = colorchooser.askcolor(color=self.custom_g1)[1]
        if c:
            self.custom_g1 = c
            self.use_custom = True
            self.theme_name.set("Custom...")
            self._apply_theme()

    def _pick_color2(self):
        c = colorchooser.askcolor(color=self.custom_g2)[1]
        if c:
            self.custom_g2 = c
            self.use_custom = True
            self.theme_name.set("Custom...")
            self._apply_theme()

    def _on_theme_change(self):
        self.use_custom = (self.theme_name.get() == "Custom...")
        self._apply_theme()

    def _apply_theme(self):
        if self.use_custom or self.theme_name.get() == "Custom...":
            g1, g2 = self.custom_g1, self.custom_g2
            accent, fg = "#00c6ff", "#ffffff"
        else:
            t = THEMES[self.theme_name.get()]
            g1, g2, accent, fg = t["g1"], t["g2"], t["accent"], t["fg"]

        self._accent = accent
        self._draw_gradient(g1, g2)

        panel_bg = interpolate_color(g1, g2, 0.5)
        r, g, b = hex_to_rgb(panel_bg)
        panel_bg = rgb_to_hex((int(r * 0.55), int(g * 0.55), int(b * 0.55)))

        self.content.configure(bg=panel_bg)
        for widget in [self.title_label, self.hint_label]:
            widget.configure(bg=panel_bg, fg=fg)
        self.title_label.configure(fg=accent)

        for w in self.content.winfo_children():
            self._style_recursive(w, panel_bg, fg, accent)

        self._refresh_status()
        self.toggle_btn.configure(bg=accent, fg="#111111", activebackground=fg)

    def _style_recursive(self, widget, bg, fg, accent):
        cls = widget.winfo_class()
        try:
            if cls in ("Frame",):
                widget.configure(bg=bg)
            elif cls == "Label":
                if widget is self.hotkey_display:
                    widget.configure(bg="#ffffff", fg="#111111")
                else:
                    widget.configure(bg=bg, fg=fg)
            elif cls == "Scale":
                widget.configure(bg=bg, fg=fg, troughcolor=accent,
                                  highlightbackground=bg, activebackground=accent)
            elif cls == "Entry":
                widget.configure(bg="#ffffff", fg="#111111", insertbackground="#111111")
            elif cls == "Button" and widget is not self.toggle_btn:
                widget.configure(bg=accent, fg="#111111", activebackground=fg)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._style_recursive(child, bg, fg, accent)

    def _draw_gradient(self, c1, c2):
        self.canvas.delete("gradient")
        w, h = 460, 600
        steps = 120
        for i in range(steps):
            t = i / steps
            color = interpolate_color(c1, c2, t)
            y0 = int(h * t)
            y1 = int(h * (t + 1.0 / steps)) + 1
            self.canvas.create_rectangle(0, y0, w, y1, outline="", fill=color, tags="gradient")
        self.canvas.tag_lower("gradient")


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
