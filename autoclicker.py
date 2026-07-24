"""
Desyx - Auto Clicker for Windows
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
  pyinstaller --onefile --noconsole --name Desyx --icon icon.ico --add-data "logo.png;." autoclicker.py
"""

import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import colorchooser

if sys.platform != "win32":
    print("This autoclicker uses the Win32 API and only runs on Windows.")
    sys.exit(1)

from license import LicenseGate, get_hwid

# Run "Desyx.exe --hwid" (or "python autoclicker.py --hwid") to print your
# HWID code, then send that code to the developer to get access.
if "--hwid" in sys.argv:
    print(get_hwid())
    sys.exit(0)


def resource_path(relative_path):
    """
    Resolves a bundled asset's path correctly whether running as a plain
    .py script or as a frozen PyInstaller --onefile exe (which extracts
    bundled data files to a temp folder at runtime, referenced via
    sys._MEIPASS).
    """
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


APP_NAME = "Desyx"

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


class KeybdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeybdInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", InputUnion)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

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


def send_key(vk_code, key_down, extended=False):
    """Injects a synthetic keyboard event via SendInput. Used by SOCD to
    cancel the losing key's output / resume it later - never to send keys
    that weren't already physically involved."""
    flags = 0 if key_down else KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    extra = ctypes.c_ulong(0)
    ii = InputUnion()
    ii.ki = KeybdInput(vk_code, 0, flags, 0, ctypes.pointer(extra))
    inp = Input(INPUT_KEYBOARD, ii)
    user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))


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
for i in range(13, 25):
    # F13-F24 aren't on physical keyboards - Windows reserves them as
    # "extra" keys specifically for remapped macro buttons (gaming mice,
    # macro keypads, streaming decks, etc.)
    VK_NAMES[0x7C - 13 + i] = f"F{i}"
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
# SOCD (Simultaneous Opposing Cardinal Directions) resolution
# ---------------------------------------------------------------------------
#
# Mode: "last input wins" - like most fighting-game controllers / SOCD-
# cleaning keyboards. When two opposite keys (e.g. A/D) are held together,
# whichever one was pressed most recently is the one that's actually "down"
# from the game's perspective; the other is synthetically released until
# it's the only one still held, at which point it resumes.

VK_W, VK_A, VK_S, VK_D = 0x57, 0x41, 0x53, 0x44
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
EXTENDED_VKS = {VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN}


class SOCDEngine:
    """Tracks physical key state for opposing pairs and decides, on every
    key event, whether the real event should be suppressed and/or whether
    a synthetic event needs to be injected for the other key in the pair.
    Thread-safe enough for this use case: all calls come from the single
    hook thread, single-threaded by construction."""

    def __init__(self):
        self.enabled = False
        self.mode = "wasd"  # "wasd" or "every" (adds arrow keys)
        self.opposite = {}
        self.physical = {}
        self.active = {}  # pair key (min_vk, max_vk) -> currently "winning" vk, or None
        self._rebuild_pairs()

    def _rebuild_pairs(self):
        pairs = [(VK_A, VK_D), (VK_W, VK_S)]
        if self.mode == "every":
            pairs += [(VK_LEFT, VK_RIGHT), (VK_UP, VK_DOWN)]
        self.opposite = {}
        self.active = {}
        for a, b in pairs:
            self.opposite[a] = b
            self.opposite[b] = a
            self.active[(min(a, b), max(a, b))] = None
        self.physical = {vk: False for vk in self.opposite}

    def set_mode(self, mode):
        if mode not in ("wasd", "every"):
            return
        self.mode = mode
        self._rebuild_pairs()

    def _pair_key(self, vk):
        opp = self.opposite[vk]
        return (min(vk, opp), max(vk, opp))

    def handle_key_event(self, vk, is_down):
        """Call for every real (non-injected) key event. Returns True if
        the caller should swallow/suppress this real event."""
        if not self.enabled or vk not in self.opposite:
            return False

        opp = self.opposite[vk]
        pkey = self._pair_key(vk)

        if is_down:
            self.physical[vk] = True
            if self.active[pkey] == opp:
                # opposite key currently winning -> this new press overrides it
                send_key(opp, False, extended=opp in EXTENDED_VKS)
            self.active[pkey] = vk
            return False  # let this real keydown through as normal

        # key-up
        self.physical[vk] = False
        if self.active[pkey] == vk:
            if self.physical.get(opp):
                # opposite key is still physically held -> resume its output
                self.active[pkey] = opp
                send_key(opp, True, extended=opp in EXTENDED_VKS)
            else:
                self.active[pkey] = None
            return False  # let this real keyup through as normal
        else:
            # this key's output was never sent (it lost), so its release
            # should be swallowed too - there's nothing to release
            return True


WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExA.restype = ctypes.c_void_p
user32.SetWindowsHookExA.argtypes = [ctypes.c_int, LowLevelKeyboardProc, ctypes.c_void_p, ctypes.c_uint32]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p


class SOCDHook:
    """
    Installs a WH_KEYBOARD_LL hook on its own dedicated thread. A low-level
    hook only receives events on the thread that installed it, and that
    thread must pump a real Win32 message loop (GetMessage/DispatchMessage)
    for Windows to deliver them - so this runs completely independently of
    tkinter's mainloop rather than trying to piggyback on it.
    """

    def __init__(self, socd_engine):
        self.engine = socd_engine
        self._thread = None
        self._thread_id = None
        self._hook_handle = None
        self._proc = LowLevelKeyboardProc(self._hook_proc)  # must keep a reference alive

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)
        self._hook_handle = user32.SetWindowsHookExA(WH_KEYBOARD_LL, self._proc, hmod, 0)
        msg = wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
        if self._hook_handle:
            user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None

    def _hook_proc(self, nCode, wParam, lParam):
        if nCode == 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not (kb.flags & LLKHF_INJECTED) and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
                is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self.engine.handle_key_event(kb.vkCode, is_down):
                    return 1
        return user32.CallNextHookEx(None, nCode, wParam, lParam)


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
# Settings persistence (saved to %APPDATA%\Desyx\settings.json)
# ---------------------------------------------------------------------------

SETTINGS_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Desyx")
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")


def load_settings():
    try:
        with open(SETTINGS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass  # never crash the app over a failed settings save


# ---------------------------------------------------------------------------
# Clicker worker thread
# ---------------------------------------------------------------------------

class ClickerEngine:
    """
    Click engine.

    Honest design notes - read this if you're wondering why it's built
    this way, especially around the "no CPU spin, no catch-up guard"
    requirements:

      - HIGH-RES TIMER: timeBeginPeriod(1) asks Windows for ~1ms scheduler
        granularity (default is ~15.6ms) for as long as the engine runs.
        This alone is most of the fix for the original "bursty" behavior,
        since a plain time.sleep() for a few ms is far more accurate once
        the OS's own tick size shrinks to ~1ms.

      - THREAD PRIORITY: the click thread requests
        THREAD_PRIORITY_TIME_CRITICAL from Windows. This makes it much
        less likely to get pre-empted for long stretches by other
        processes, which further reduces scheduling jitter - again, with
        zero busy-waiting, purely by asking the OS to schedule this
        thread more eagerly.

      - NO CPU SPIN: every wait is a single time.sleep(remaining) call.
        There is no busy-loop anywhere. This means individual clicks can
        be off by roughly the OS's real sleep granularity (about 0-1ms
        with the high-res timer active) rather than being spin-accurate
        to the microsecond - a deliberate trade of a small amount of
        per-click precision for zero CPU usage.

      - WHY THERE'S NO "ANTI-BURST GUARD": a rigid absolute schedule
        (next click = session_start + N * period) combined with NO
        spin-wait and NO catch-up guard is a bad combination - if the
        thread is ever delayed past its scheduled slot (guard removed,
        so nothing catches that), it fires every "missed" click
        back-to-back the moment it wakes up, which is a real burst, not
        an imagined one. Rather than patch that with a guard, the
        scheduling model itself was changed: each click's timing is
        computed fresh from the moment the previous click actually
        finished, not from a fixed running schedule. There is no
        "behind schedule" state to catch up from, so there is nothing
        for a burst to be made of - it's eliminated by construction,
        not detected and suppressed after the fact.
        The honest tradeoff: because there's no absolute anchor, a
        delayed cycle is not "made up" - if the OS stalls the thread for
        50ms, you lose that time rather than getting a burst to
        compensate. In practice, combined with the high-res timer and
        time-critical thread priority, this loss is negligible and
        clicks stay effectively locked to your CPS setting.

      - MOUSE-UP SAFETY: once mouse_down() has been sent, the matching
        mouse_up() always fires after down_time, even if you disable the
        clicker in that exact instant. This is not interruptible, on
        purpose - it guarantees the mouse button can never end up
        logically stuck "down".
    """

    THREAD_PRIORITY_TIME_CRITICAL = 15

    def __init__(self, gate=None):
        self.running = False
        self.enabled = False
        self.cps = 20.0
        self.cdc = 50.0
        self.button = "Left"
        self._thread = None
        self._timer_boosted = False
        self.gate = gate  # LicenseGate instance; None = no gate (never ship like this)

    def start(self):
        self.running = True
        self._boost_timer_resolution()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        self._restore_timer_resolution()

    # ----- Windows high-resolution timer -----
    def _boost_timer_resolution(self):
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            self._timer_boosted = True
        except Exception:
            self._timer_boosted = False

    def _restore_timer_resolution(self):
        if self._timer_boosted:
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
            self._timer_boosted = False

    # ----- Windows thread priority (reduces scheduling delay, no CPU cost) -----
    def _boost_thread_priority(self):
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetCurrentThread()
            kernel32.SetThreadPriority(handle, self.THREAD_PRIORITY_TIME_CRITICAL)
        except Exception:
            pass

    # ----- plain sleep-based waiting, zero CPU spin -----
    def _sleep_until(self, target):
        remaining = target - time.perf_counter()
        if remaining > 0:
            time.sleep(remaining)

    # ----- main loop -----
    def _loop(self):
        self._boost_thread_priority()  # once, for the lifetime of this thread
        while self.running:
            if self.enabled:
                self._run_click_session()
            else:
                time.sleep(0.02)

    def _run_click_session(self):
        """
        One continuous clicking session. Each click's schedule is derived
        fresh from "now" every cycle - see the class docstring for why
        this (rather than a fixed running anchor) is what makes bursts
        impossible without needing a guard.
        """
        while self.running and self.enabled:
            multiplier = self.gate.click_multiplier() if self.gate else 1.0
            if multiplier <= 0:
                time.sleep(0.5)  # unlicensed / revoked - idle, don't click
                continue

            cps = max(self.cps * multiplier, 0.1)
            cdc = min(max(self.cdc, 1.0), 99.0)
            period = 1.0 / cps
            down_time = period * (cdc / 100.0)
            up_time = period - down_time

            # --- click (never interruptible once started - see mouse-up safety note) ---
            mouse_down(self.button)
            self._sleep_until(time.perf_counter() + down_time)
            mouse_up(self.button)

            # --- gap before next click (interruptible, checked in short slices
            #     so disabling the clicker takes effect promptly) ---
            up_target = time.perf_counter() + up_time
            while self.running and self.enabled:
                remaining = up_target - time.perf_counter()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.01))


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
    def __init__(self, root, gate=None):
        self.root = root
        self.gate = gate
        self.engine = ClickerEngine(gate=gate)
        self.engine.start()

        s = load_settings()

        self.accent = s.get("accent", ACCENT_PRESETS[0])
        self.cps_value = float(s.get("cps", 20))
        self.cdc_value = float(s.get("cdc", 50))
        self.button_var = tk.StringVar(value=s.get("button", "Left"))

        self.hotkey_combo = s.get("hotkey_combo", [0x75])  # default F6
        self.capturing_hotkey = False
        self.capture_start_time = 0.0
        self._hotkey_prev_state = False
        self.trigger_mode = s.get("trigger_mode", "Toggle")

        self.engine.cps = self.cps_value
        self.engine.cdc = self.cdc_value
        self.engine.button = self.button_var.get()

        self.socd_engine = SOCDEngine()
        self.socd_engine.set_mode(s.get("socd_mode", "wasd"))
        self.socd_engine.enabled = bool(s.get("socd_enabled", False))
        self.socd_hook = SOCDHook(self.socd_engine)
        self.socd_hook.start()

        self.accent_swatch_canvases = []

        root.title("Desyx")
        root.configure(bg=BG)
        WIN_W, WIN_H = 460, 700
        root.geometry(f"{WIN_W}x{WIN_H}")
        root.resizable(False, False)

        self.current_tab = "MAIN"
        self._build_ui()
        self._style_mode_buttons()
        self._style_button_buttons()
        self._apply_accent()
        self._poll_hotkey()

    # ----- UI -----
    def _build_ui(self):
        root_frame = tk.Frame(self.root, bg=BG)
        root_frame.pack(fill="both", expand=True)

        # --- header ---
        header = tk.Frame(root_frame, bg=BG)
        header.pack(fill="x", padx=20, pady=(20, 12))
        self.accent_bar = tk.Frame(header, bg=self.accent, width=4)
        self.accent_bar.pack(side="left", fill="y", padx=(0, 12))
        title_col = tk.Frame(header, bg=BG)
        title_col.pack(side="left")
        self.title_label = tk.Label(title_col, text="DESYX", bg=BG, fg=self.accent,
                                     font=("Segoe UI", 20, "bold"))
        self.title_label.pack(anchor="w")
        tk.Label(title_col, text="v1.0.0", bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w")

        tk.Frame(root_frame, bg=CARD_BORDER, height=1).pack(fill="x", padx=20, pady=(0, 16))

        # --- tab bar ---
        tab_bar = tk.Frame(root_frame, bg=BG)
        tab_bar.pack(fill="x", padx=20, pady=(0, 14))
        self.tab_buttons = {}
        for name in ["MAIN", "SOCD"]:
            b = tk.Button(tab_bar, text=name, relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
                          command=lambda n=name: self._switch_tab(n), padx=14, pady=8)
            b.pack(side="left", padx=(0, 6))
            self.tab_buttons[name] = b

        # --- page container (only one page is packed at a time) ---
        self.page_container = tk.Frame(root_frame, bg=BG)
        self.page_container.pack(fill="both", expand=True)

        self.main_page = tk.Frame(self.page_container, bg=BG)
        self.socd_page = tk.Frame(self.page_container, bg=BG)

        self._build_main_page(self.main_page)
        self._build_socd_page(self.socd_page)

        self._switch_tab(self.current_tab)

    def _switch_tab(self, name):
        self.current_tab = name
        self.main_page.pack_forget()
        self.socd_page.pack_forget()
        if name == "MAIN":
            self.main_page.pack(fill="both", expand=True)
        else:
            self.socd_page.pack(fill="both", expand=True)
        self._style_tab_buttons()

    def _style_tab_buttons(self):
        for name, btn in self.tab_buttons.items():
            if name == self.current_tab:
                btn.configure(bg=self.accent, fg="#ffffff")
            else:
                btn.configure(bg="#24242e", fg=SUBTEXT)

    # ----- MAIN page -----
    def _build_main_page(self, root_frame):
        # --- SPEED card ---
        speed_card = card(root_frame)
        speed_card.pack(fill="x", padx=20, pady=(0, 14))
        self.speed_title = card_title(speed_card, "\u2699 SPEED", self.accent)

        speed_row = tk.Frame(speed_card, bg=CARD_BG)
        speed_row.pack(fill="x", padx=16, pady=(0, 18))

        cps_col = tk.Frame(speed_row, bg=CARD_BG)
        cps_col.pack(side="left", fill="both", expand=True)
        tk.Label(cps_col, text="clicks per second", bg=CARD_BG, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w")
        cps_val_row = tk.Frame(cps_col, bg=CARD_BG)
        cps_val_row.pack(anchor="w", pady=(2, 0))
        self.cps_entry = self._make_big_number_entry(cps_val_row, self.cps_value, "CPS", self._on_cps_commit)
        cps_col.pack_propagate(True)

        tk.Frame(speed_row, bg=CARD_BORDER, width=1).pack(side="left", fill="y", padx=14)

        cdc_col = tk.Frame(speed_row, bg=CARD_BG)
        cdc_col.pack(side="left", fill="both", expand=True)
        tk.Label(cdc_col, text="click duty (cdc)", bg=CARD_BG, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w")
        cdc_val_row = tk.Frame(cdc_col, bg=CARD_BG)
        cdc_val_row.pack(anchor="w", pady=(2, 0))
        self.cdc_entry = self._make_big_number_entry(cdc_val_row, self.cdc_value, "%", self._on_cdc_commit)

        # --- CONTROLS card ---
        controls_card = card(root_frame)
        controls_card.pack(fill="x", padx=20, pady=(0, 14))
        self.controls_title = card_title(controls_card, "\u2328 CONTROLS", self.accent)

        hk_wrap = tk.Frame(controls_card, bg=CARD_BG)
        hk_wrap.pack(fill="x", padx=16)
        tk.Label(hk_wrap, text="hotkey", bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 9)).pack(anchor="w")

        self.hotkey_box = tk.Frame(hk_wrap, bg="#1a1420", highlightthickness=1,
                                    highlightbackground=self.accent, cursor="hand2")
        self.hotkey_box.pack(fill="x", pady=(4, 6))
        hk_inner = tk.Frame(self.hotkey_box, bg="#1a1420")
        hk_inner.pack(fill="x", padx=12, pady=10)
        self.hotkey_icon = tk.Label(hk_inner, text="\u2328", bg="#1a1420", fg=self.accent, font=("Segoe UI", 12))
        self.hotkey_icon.pack(side="left")
        self.hotkey_display = tk.Label(hk_inner, text=combo_to_string(self.hotkey_combo),
                                        bg="#1a1420", fg=self.accent, font=("Segoe UI", 14, "bold"))
        self.hotkey_display.pack(side="left", padx=(10, 0))
        self.hotkey_pencil = tk.Label(hk_inner, text="\u270E", bg="#1a1420", fg=SUBTEXT, font=("Segoe UI", 11))
        self.hotkey_pencil.pack(side="right")
        for w in (self.hotkey_box, hk_inner, self.hotkey_icon, self.hotkey_display, self.hotkey_pencil):
            w.bind("<Button-1>", lambda e: self._begin_capture())

        tk.Label(hk_wrap, text="u can use combos like shift+q or ctrl+f6, or mouse side\n"
                                "buttons and extra mouse buttons - whatever u want",
                 bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 8), justify="left").pack(anchor="w", pady=(0, 12))

        tk.Frame(controls_card, bg=CARD_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 12))

        opts_row = tk.Frame(controls_card, bg=CARD_BG)
        opts_row.pack(fill="x", padx=16, pady=(0, 18))

        mb_col = tk.Frame(opts_row, bg=CARD_BG)
        mb_col.pack(side="left", fill="both", expand=True)
        tk.Label(mb_col, text="mouse button", bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 9)).pack(anchor="w")
        mb_row = tk.Frame(mb_col, bg=CARD_BG)
        mb_row.pack(anchor="w", pady=(6, 0))
        self.button_buttons = {}
        for opt in ["Left", "Right", "Middle"]:
            b = tk.Button(mb_row, text=opt, relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
                          command=lambda o=opt: self._select_button(o), padx=10, pady=6)
            b.pack(side="left", padx=(0, 6))
            self.button_buttons[opt] = b

        tk.Frame(opts_row, bg=CARD_BORDER, width=1).pack(side="left", fill="y", padx=10)

        mode_col = tk.Frame(opts_row, bg=CARD_BG)
        mode_col.pack(side="left", fill="both", expand=True)
        tk.Label(mode_col, text="mode", bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 9)).pack(anchor="w")
        mode_row = tk.Frame(mode_col, bg=CARD_BG)
        mode_row.pack(anchor="w", pady=(6, 0))
        self.toggle_mode_btn = tk.Button(mode_row, text="Toggle", relief="flat", bd=0,
                                          font=("Segoe UI", 10, "bold"),
                                          command=lambda: self._set_trigger_mode("Toggle"),
                                          padx=10, pady=6)
        self.toggle_mode_btn.pack(side="left", padx=(0, 6))
        self.hold_mode_btn = tk.Button(mode_row, text="Hold", relief="flat", bd=0,
                                        font=("Segoe UI", 10, "bold"),
                                        command=lambda: self._set_trigger_mode("Hold"),
                                        padx=10, pady=6)
        self.hold_mode_btn.pack(side="left")

        # --- ACCENT card ---
        accent_card = card(root_frame)
        accent_card.pack(fill="x", padx=20, pady=(0, 14))
        self.accent_title = card_title(accent_card, "\U0001F3A8 ACCENT", self.accent)
        swatch_row = tk.Frame(accent_card, bg=CARD_BG)
        swatch_row.pack(fill="x", padx=16, pady=(0, 16))
        for color in ACCENT_PRESETS:
            self._make_swatch(swatch_row, color)
        self._make_custom_swatch(swatch_row)

        # --- start/stop toggle button ---
        self.toggle_btn = tk.Button(root_frame, text="", font=("Segoe UI", 13, "bold"),
                                     relief="flat", bd=0, command=self._toggle, height=2)
        self.toggle_btn.pack(fill="x", padx=20, pady=(4, 20))
        self._refresh_toggle_label()

    # ----- SOCD page -----
    def _build_socd_page(self, root_frame):
        socd_card = card(root_frame)
        socd_card.pack(fill="x", padx=20, pady=(0, 14))
        self.socd_title = card_title(socd_card, "\u2194 SOCD", self.accent)

        tk.Label(socd_card, text="Simultaneous Opposing Cardinal Directions.\n"
                                  "When two opposite keys are held together, the one\n"
                                  "pressed most recently overrides the other (last\n"
                                  "input wins) - the earlier key resumes once you\n"
                                  "let go of whichever one is currently winning.",
                 bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 9), justify="left").pack(
            anchor="w", padx=16, pady=(0, 14))

        self.socd_toggle_btn = tk.Button(socd_card, text="", font=("Segoe UI", 12, "bold"),
                                          relief="flat", bd=0, command=self._toggle_socd, height=2)
        self.socd_toggle_btn.pack(fill="x", padx=16, pady=(0, 16))

        tk.Frame(socd_card, bg=CARD_BORDER, height=1).pack(fill="x", padx=16, pady=(0, 14))

        scope_wrap = tk.Frame(socd_card, bg=CARD_BG)
        scope_wrap.pack(fill="x", padx=16, pady=(0, 18))
        tk.Label(scope_wrap, text="applies to", bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 9)).pack(anchor="w")
        scope_row = tk.Frame(scope_wrap, bg=CARD_BG)
        scope_row.pack(anchor="w", pady=(6, 0))
        self.socd_scope_buttons = {}
        for key, label in [("wasd", "WASD only"), ("every", "WASD + Arrow Keys")]:
            b = tk.Button(scope_row, text=label, relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
                          command=lambda k=key: self._set_socd_scope(k), padx=10, pady=6)
            b.pack(side="left", padx=(0, 6))
            self.socd_scope_buttons[key] = b

        self._style_socd_scope_buttons()
        self._refresh_socd_toggle_label()

    def _toggle_socd(self):
        self.socd_engine.enabled = not self.socd_engine.enabled
        self._refresh_socd_toggle_label()
        self._save()

    def _refresh_socd_toggle_label(self):
        if self.socd_engine.enabled:
            self.socd_toggle_btn.config(text="\u25A0  SOCD enabled  (click to disable)",
                                         bg="#24242e", fg=self.accent)
        else:
            self.socd_toggle_btn.config(text="\u25B6  SOCD disabled  (click to enable)",
                                         bg=self.accent, fg="#ffffff")

    def _set_socd_scope(self, mode):
        self.socd_engine.set_mode(mode)
        self._style_socd_scope_buttons()
        self._save()

    def _style_socd_scope_buttons(self):
        for key, btn in self.socd_scope_buttons.items():
            if key == self.socd_engine.mode:
                btn.configure(bg=self.accent, fg="#ffffff")
            else:
                btn.configure(bg="#24242e", fg=SUBTEXT)

    # ----- big fixed-format number entry (digits + single "." only, always N.NN) -----
    def _make_big_number_entry(self, parent, initial_value, suffix, commit_callback):
        var = tk.StringVar(value=f"{initial_value:.2f}")
        vcmd = (self.root.register(self._validate_number_chars), "%P")
        entry = tk.Entry(parent, textvariable=var, font=("Segoe UI", 26, "bold"),
                          bg=CARD_BG, fg=self.accent, insertbackground=self.accent,
                          relief="flat", bd=0, width=7, validate="key", validatecommand=vcmd)
        entry.pack(side="left")
        entry._var = var
        entry._commit_callback = commit_callback
        entry.bind("<FocusOut>", lambda e: self._commit_number_entry(entry, suffix_ignore=None))
        entry.bind("<Return>", lambda e: (self._commit_number_entry(entry, suffix_ignore=None), entry.master.focus_set()))
        tk.Label(parent, text=suffix, bg=CARD_BG, fg=SUBTEXT, font=("Segoe UI", 11)).pack(side="left", padx=(4, 0), pady=(10, 0))
        return entry

    def _validate_number_chars(self, proposed):
        if proposed == "":
            return True  # allow clearing while typing; refilled on commit
        if proposed.count(".") > 1:
            return False
        return all(ch.isdigit() or ch == "." for ch in proposed)

    def _commit_number_entry(self, entry, suffix_ignore):
        raw = entry._var.get()
        try:
            v = float(raw) if raw not in ("", ".") else 0.0
        except ValueError:
            v = 0.0
        v = entry._commit_callback(v)  # callback clamps + applies, returns final clamped value
        entry._var.set(f"{v:.2f}")

    def _on_cps_commit(self, v):
        v = max(0.1, min(v if v > 0 else 20.0, 500.0))
        self.cps_value = v
        self.engine.cps = v
        self._save()
        return v

    def _on_cdc_commit(self, v):
        v = max(1.0, min(v if v > 0 else 50.0, 99.0))
        self.cdc_value = v
        self.engine.cdc = v
        self._save()
        return v

    # ----- swatches -----
    def _make_swatch(self, parent, color):
        c = tk.Canvas(parent, width=34, height=34, bg=CARD_BG, highlightthickness=0, cursor="hand2")
        c.pack(side="left", padx=5)
        c.create_oval(3, 3, 31, 31, fill=color, outline="")
        c.color = color
        c.bind("<Button-1>", lambda e, col=color: self._select_accent(col))
        self.accent_swatch_canvases.append(c)

    def _make_custom_swatch(self, parent):
        c = tk.Canvas(parent, width=34, height=34, bg=CARD_BG, highlightthickness=0, cursor="hand2")
        c.pack(side="left", padx=5)
        c.create_oval(3, 3, 31, 31, fill=CARD_BG, outline=SUBTEXT, dash=(2, 2))
        c.create_text(17, 17, text="+", fill=SUBTEXT, font=("Segoe UI", 12, "bold"))
        c.color = None
        c.bind("<Button-1>", lambda e: self._pick_custom_accent())

    # ----- mouse button / trigger mode selection -----
    def _select_button(self, opt):
        self.button_var.set(opt)
        self.engine.button = opt
        self._style_button_buttons()
        self._save()

    def _style_button_buttons(self):
        for opt, btn in self.button_buttons.items():
            if opt == self.button_var.get():
                btn.configure(bg=self.accent, fg="#ffffff")
            else:
                btn.configure(bg="#24242e", fg=SUBTEXT)

    def _set_trigger_mode(self, mode):
        self.trigger_mode = mode
        self.engine.enabled = False
        self._style_mode_buttons()
        self._refresh_toggle_label()
        self._save()

    def _style_mode_buttons(self):
        if self.trigger_mode == "Toggle":
            self.toggle_mode_btn.config(bg=self.accent, fg="#ffffff")
            self.hold_mode_btn.config(bg="#24242e", fg=SUBTEXT)
        else:
            self.hold_mode_btn.config(bg=self.accent, fg="#ffffff")
            self.toggle_mode_btn.config(bg="#24242e", fg=SUBTEXT)

    # ----- start/stop -----
    def _toggle(self):
        self.engine.enabled = not self.engine.enabled
        self._refresh_toggle_label()

    def _refresh_toggle_label(self):
        hk = combo_to_string(self.hotkey_combo)
        if self.engine.enabled:
            self.toggle_btn.config(text=f"\u25A0  stop  [{hk}]", bg="#24242e", fg=self.accent)
        else:
            self.toggle_btn.config(text=f"\u25B6  start  [{hk}]", bg=self.accent, fg="#ffffff")

    # ----- hotkey capture -----
    def _begin_capture(self):
        if self.capturing_hotkey:
            return
        self.capturing_hotkey = True
        self.capture_start_time = time.time()
        self.hotkey_display.config(text="press keys... (esc cancels)")

    def _finish_capture(self, combo):
        self.capturing_hotkey = False
        if combo:
            self.hotkey_combo = combo
            self._save()
        self.hotkey_display.config(text=combo_to_string(self.hotkey_combo))
        self._refresh_toggle_label()

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
                    self._refresh_toggle_label()
            else:  # Toggle
                if pressed and not self._hotkey_prev_state:
                    self._toggle()
            self._hotkey_prev_state = pressed
        self.root.after(30, self._poll_hotkey)

    # ----- theming -----
    def _select_accent(self, color):
        self.accent = color
        self._apply_accent()
        self._save()

    def _pick_custom_accent(self):
        c = colorchooser.askcolor(color=self.accent)[1]
        if c:
            self.accent = c
            self._apply_accent()
            self._save()

    def _apply_accent(self):
        self.accent_bar.configure(bg=self.accent)
        self.title_label.configure(fg=self.accent)
        for title in [self.speed_title, self.controls_title, self.accent_title, self.socd_title]:
            title.configure(fg=self.accent)
        self.hotkey_box.configure(highlightbackground=self.accent)
        self.hotkey_icon.configure(fg=self.accent)
        self.hotkey_display.configure(fg=self.accent)
        self.cps_entry.configure(fg=self.accent, insertbackground=self.accent)
        self.cdc_entry.configure(fg=self.accent, insertbackground=self.accent)
        self._style_button_buttons()
        self._style_mode_buttons()
        for c in self.accent_swatch_canvases:
            c.delete("ring")
            if c.color == self.accent:
                c.create_oval(0, 0, 34, 34, outline=self.accent, width=2, tags="ring")
        self._refresh_toggle_label()
        self._style_tab_buttons()
        self._style_socd_scope_buttons()
        self._refresh_socd_toggle_label()

    # ----- settings persistence -----
    def _save(self):
        save_settings({
            "accent": self.accent,
            "cps": self.cps_value,
            "cdc": self.cdc_value,
            "button": self.button_var.get(),
            "hotkey_combo": self.hotkey_combo,
            "trigger_mode": self.trigger_mode,
            "socd_enabled": self.socd_engine.enabled,
            "socd_mode": self.socd_engine.mode,
        })


def main():
    gate = LicenseGate()
    gate.start()  # does an immediate check, then keeps re-checking in the background

    root = tk.Tk()
    root.withdraw()  # keep hidden until we know the license is valid

    valid, reason = gate.status()
    if not valid:
        import tkinter.messagebox as mb
        mb.showerror(
            "Desyx",
            f"Access denied.\n({reason})\n\n"
            f"Your code: {gate.hwid}\n"
            f"Send this code to the developer to get access."
        )
        root.destroy()
        sys.exit(1)

    root.deiconify()
    app = AutoClickerApp(root, gate=gate)

    def on_close():
        app._save()
        app.engine.stop()
        app.socd_hook.stop()
        gate.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
