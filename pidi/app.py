"""PIDI main app: wires vision -> gestures -> capture/clipboard/overlay/phone/AI."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from . import capture, clipboard, config
from .ai import AIClient
from .gestures import EngineConfig, Event, GestureEngine
from .overlay import ACC, WARN, Overlay
from .phone import PhoneServer
from .platform_utils import IS_MAC, IS_WIN, beep, cursor_pos, enable_dpi_awareness
from .vision import VisionLoop

HOLD_HINT = "✊ then ✋ to drop into any app  ·  ✊ swipe → phone  ·  👍 AI"


def _tray_icon(active: bool) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=16, fill=(62, 224, 143) if active else (110, 118, 130))
    # simple open-hand glyph
    for i, x in enumerate((18, 27, 36, 45)):
        top = 12 if i in (1, 2) else 16
        d.rounded_rectangle((x - 3, top, x + 4, 40), radius=4, fill=(10, 20, 15))
    d.rounded_rectangle((15, 30, 49, 52), radius=10, fill=(10, 20, 15))
    return img


HUD_EVENTS = {
    "armed": ("READY", "#ffbe28"), "grab": ("GRABBED", ACC), "region_start": ("SELECT", "#c86eff"),
    "region_grab": ("AREA GRABBED", ACC), "drop": ("DROPPED", "#4db5ff"),
    "throw": ("SENT TO PHONE", "#ff8a3d"), "ai": ("AI MODE", "#c86eff"),
}


class PidiApp:
    def __init__(self, demo: bool = False):
        enable_dpi_awareness()
        self.s = config.load()
        self.q: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("PIDI")
        self.ui = Overlay(self.root)
        self.ui.post = lambda fn: self.q.put(("call", fn))

        self.engine = GestureEngine(EngineConfig.from_dict(self.s.get("engine")))
        self.vision = VisionLoop(self.engine, self.q, self.s)
        self.ai = AIClient(self.s.get("ai", {}))
        self.phone = None
        if self.s.get("phone_enabled", True):
            self.phone = PhoneServer(int(self.s.get("phone_port", 8765)),
                                     on_upload=lambda img: self.q.put(("phone_upload", img)))

        self.grab: Image.Image | None = None
        self.mon = capture.monitor_at(*cursor_pos())
        self.history = Path(os.path.expanduser(self.s["history_dir"]))
        self.tray = None
        self._hud_seq = 0
        if demo or self.s.get("hud"):
            self.vision.hud.set()

    # ------------------------------------------------------------- lifecycle
    def run(self):
        if self.phone:
            try:
                self.phone.start()
            except OSError as e:
                print(f"[pidi] phone drop disabled: {e}")
                self.phone = None
        if self.s.get("start_paused"):
            self.vision.paused.set()
        self.vision.start()
        self._start_tray()
        self.root.after(15, self._pump)
        self.ui.status("PIDI is on — show an open palm ✋ to start", self.mon, 3200)
        try:
            self.root.mainloop()
        finally:
            self.vision.stop()
            if self.phone:
                self.phone.stop()

    def quit(self):
        if self.tray:
            self.tray.stop()
        self.root.quit()

    # ------------------------------------------------------------------ tray
    def _start_tray(self):
        try:
            import pystray
        except ImportError:
            print("[pidi] pystray not installed — running without tray icon (Ctrl+C to quit)")
            return
        cmd = lambda name: (lambda icon, item: self.q.put(("cmd", name)))  # noqa: E731
        M = pystray.MenuItem
        menu = pystray.Menu(
            M("Gestures on", cmd("toggle"), checked=lambda i: not self.vision.paused.is_set(),
              default=True),
            M("Grab mode", pystray.Menu(
                M("Whole screen", cmd("mode_screen"),
                  checked=lambda i: self.s["grab_target"] == "screen", radio=True),
                M("Active window", cmd("mode_window"),
                  checked=lambda i: self.s["grab_target"] == "window", radio=True))),
            M("Pair phone…", cmd("pair"), enabled=lambda i: self.phone is not None),
            M("AI on last grab…", cmd("ai"), enabled=lambda i: self.grab is not None),
            M("Camera HUD (for demos)", cmd("hud"),
              checked=lambda i: self.vision.hud.is_set()),
            M("Camera preview (tuning)", cmd("preview"),
              checked=lambda i: self.vision.preview.is_set()),
            M("Open grabs folder", cmd("folder")),
            M("Sounds", cmd("sounds"), checked=lambda i: bool(self.s.get("sounds"))),
            pystray.Menu.SEPARATOR,
            M("Quit PIDI", cmd("quit")),
        )
        self.tray = pystray.Icon("pidi", _tray_icon(True), "PIDI — gesture grab", menu)
        self.tray.run_detached()

    def _refresh_tray(self):
        if self.tray:
            self.tray.icon = _tray_icon(not self.vision.paused.is_set())
            self.tray.update_menu()

    # ------------------------------------------------------------ event pump
    def _pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                try:
                    if isinstance(item, Event):
                        self._on_event(item)
                    elif item[0] == "cmd":
                        self._on_cmd(item[1])
                    elif item[0] == "phone_upload":
                        self._on_phone_upload(item[1])
                    elif item[0] == "call":
                        item[1]()
                except Exception as e:  # keep the app alive no matter what
                    print(f"[pidi] error: {e!r}")
                    self.ui.status(f"Oops: {e}", self.mon, 3000, WARN)
        except queue.Empty:
            pass
        self._hud_tick()
        self.root.after(15, self._pump)

    def _hud_tick(self):
        if not self.vision.hud.is_set() or self.vision.paused.is_set():
            return
        f = self.vision.hud_frame
        if f is None or f[0] == self._hud_seq:
            return
        self._hud_seq = f[0]
        self.ui.hud_update(f[1], f[2], f[3], f[4], self.s.get("hud_corner", "top-right"))

    def _sound(self, kind):
        if self.s.get("sounds"):
            beep(kind)

    def _on_event(self, ev: Event):
        k = ev.kind
        if k in HUD_EVENTS and self.vision.hud.is_set():
            self.ui.hud_event(*HUD_EVENTS[k])
        if k == "armed":
            self.mon = capture.monitor_at(*cursor_pos())
            self._sound("arm")
            self.ui.status("Ready — close your hand ✊ to grab  ·  pinch 🤏 to select", self.mon, 1500)
        elif k == "disarmed":
            self.ui.hide_status()
        elif k == "grab":
            self.ui.hide_for_capture()
            self.root.after(70, self._do_grab)
        elif k == "region_start":
            self.ui.hide_status()
            self.ui.region_begin(self.mon)
            self.ui.region_update(ev.data["a"], ev.data["a"])
        elif k == "region_move":
            self.ui.region_update(ev.data["a"], ev.data["b"])
        elif k == "region_grab":
            self.ui.hide_for_capture()
            rect = ev.data["rect"]
            self.root.after(70, lambda: self._take(capture.grab_region(self.mon, rect), "Area grabbed"))
        elif k == "region_cancel":
            self.ui.region_end()
            self.ui.status("Selection cancelled", self.mon, 1000, WARN)
        elif k == "drop":
            self._drop()
        elif k == "throw":
            self._throw()
        elif k == "ai":
            self._open_ai()
        elif k == "hold_timeout":
            self.ui.clear_holding()
            self.ui.status("Still on your clipboard — Ctrl+V anytime", self.mon, 2200)
        elif k == "error":
            self.ui.status(ev.data.get("message", "Error"), self.mon, 4000, WARN)
            self._sound("error")

    # --------------------------------------------------------------- actions
    def _do_grab(self):
        if self.s.get("grab_target") == "window":
            img = capture.grab_window_or_monitor(self.mon)
        else:
            img = capture.grab_monitor(self.mon)
        self._take(img, "Screen grabbed")

    def _take(self, img: Image.Image, title: str):
        self.grab = img
        clipboard.copy_image(img)                 # Ctrl+V works even without gestures
        self._sound("grab")
        self.ui.flash(self.mon)
        self.ui.holding(img, self.mon, f"✓ {title}", HOLD_HINT)
        self._save_history(img)
        self._refresh_tray()

    def _drop(self):
        if self.grab is None:
            return
        self.ui.clear_holding()
        clipboard.copy_image(self.grab)           # in case something else was copied meanwhile
        clipboard.paste()
        self._sound("drop")
        self.ui.status("Dropped ✓", self.mon, 1100)

    def _throw(self):
        if self.grab is None:
            return
        if not self.phone:
            self.ui.status("Phone Drop is off in settings", self.mon, 2000, WARN)
            return
        self.phone.publish(self.grab)
        self.ui.clear_holding()
        self._sound("throw")
        if self.phone.phone_connected:
            self.ui.status("Sent to phone 📱", self.mon, 1500)
        else:
            self.ui.status("Ready on phone — pair from the tray menu to see it", self.mon, 2600, WARN)

    def _open_ai(self):
        if self.grab is None:
            return
        self.ui.clear_holding()
        self.ui.ai_panel(self.grab, self.ai, clipboard.copy_text)

    def _on_phone_upload(self, img: Image.Image):
        self.mon = capture.monitor_at(*cursor_pos())
        self.grab = img
        clipboard.copy_image(img)
        self.engine.inject_holding(time.monotonic())
        self._sound("grab")
        self.ui.holding(img, self.mon, "📱 From your phone", "✊ then ✋ to drop into any app")
        self._save_history(img, prefix="phone")

    def _save_history(self, img: Image.Image, prefix="grab"):
        if not self.s.get("save_history"):
            return
        self.history.mkdir(parents=True, exist_ok=True)
        name = f"{prefix}_{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
        img.save(self.history / name, "PNG")
        files = sorted(self.history.glob("*.png"), key=lambda p: p.stat().st_mtime)
        for old in files[: max(0, len(files) - int(self.s.get("history_keep", 300)))]:
            try:
                old.unlink()
            except OSError:
                pass

    # -------------------------------------------------------------- commands
    def _on_cmd(self, name: str):
        if name == "toggle":
            if self.vision.paused.is_set():
                self.vision.paused.clear()
                self.engine.reset()
                self.ui.status("Gestures on ✋", self.mon, 1500)
            else:
                self.vision.paused.set()
                self.ui.hud_hide(None)
                self.ui.status("Gestures paused — camera off", self.mon, 1500, WARN)
        elif name in ("mode_screen", "mode_window"):
            self.s["grab_target"] = "screen" if name == "mode_screen" else "window"
            config.save(self.s)
        elif name == "pair" and self.phone:
            self.ui.pair_window(self.phone.url)
        elif name == "ai":
            self._open_ai()
        elif name == "hud":
            if self.vision.hud.is_set():
                self.vision.hud.clear()
                self.ui.hud_hide(None)
            else:
                self.vision.hud.set()
            self.s["hud"] = self.vision.hud.is_set()
            config.save(self.s)
        elif name == "preview":
            (self.vision.preview.clear if self.vision.preview.is_set() else self.vision.preview.set)()
        elif name == "folder":
            self.history.mkdir(parents=True, exist_ok=True)
            if IS_WIN:
                os.startfile(self.history)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open" if IS_MAC else "xdg-open", str(self.history)])
        elif name == "sounds":
            self.s["sounds"] = not self.s.get("sounds")
            config.save(self.s)
        elif name == "quit":
            self.quit()
        self._refresh_tray()


def main(demo: bool = False):
    try:
        PidiApp(demo=demo).run()
    except KeyboardInterrupt:
        sys.exit(0)
