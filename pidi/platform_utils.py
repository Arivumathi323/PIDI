"""OS helpers: DPI awareness, cursor position, foreground window bounds."""
from __future__ import annotations

import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def enable_dpi_awareness():
    """Without this, Windows scaling (125%/150%) makes capture coords wrong."""
    if not IS_WIN:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def cursor_pos() -> tuple[int, int]:
    if IS_WIN:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    try:
        from pynput.mouse import Controller
        x, y = Controller().position
        return int(x), int(y)
    except Exception:
        return 0, 0


def foreground_window_rect():
    """(left, top, width, height) of the active window, or None."""
    if not IS_WIN:
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    try:  # real visible bounds, without the invisible resize border
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    except Exception:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w < 50 or h < 50:
        return None
    return rect.left, rect.top, w, h


def make_noactivate(tk_window):
    """Stop an overlay window from ever stealing keyboard focus (Windows)."""
    if not IS_WIN:
        return
    import ctypes
    try:
        tk_window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(tk_window.winfo_id())
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_TRANSPARENT = 0x00000020   # clicks pass through (window is layered via -alpha)
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT)
    except Exception:
        pass


def beep(kind: str):
    """Tiny non-blocking audio cue."""
    import threading

    def _play():
        try:
            if IS_WIN:
                import winsound
                tones = {"arm": [(880, 35)], "grab": [(660, 40), (990, 50)],
                         "drop": [(990, 40), (660, 50)], "throw": [(700, 30), (1100, 30), (1500, 40)],
                         "error": [(300, 120)]}
                for f, d in tones.get(kind, [(800, 40)]):
                    winsound.Beep(f, d)
        except Exception:
            pass

    threading.Thread(target=_play, daemon=True).start()
