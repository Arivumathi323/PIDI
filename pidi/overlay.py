"""On-screen feedback: status pill, capture flash, 'in hand' card, region box,
phone pairing window and AI panel. All Tk calls happen on the main thread."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .platform_utils import IS_WIN, make_noactivate

BG = "#111418"
CARD = "#181c22"
LINE = "#2a313b"
TXT = "#e9edf2"
MUT = "#8a94a3"
ACC = "#3ee08f"
WARN = "#ffb547"
KEY = "#010203"            # transparent key color for the region overlay
FONT = ("Segoe UI", 11) if IS_WIN else ("Helvetica", 12)
FONT_B = (FONT[0], FONT[1], "bold")


def _popup(root, alpha=0.95) -> tk.Toplevel:
    w = tk.Toplevel(root)
    w.withdraw()
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    try:
        w.attributes("-alpha", alpha)
    except tk.TclError:
        pass
    return w


class Overlay:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._status_win = None
        self._status_job = None
        self._card = None
        self._card_img = None
        self._region = None
        self._region_mon = None
        self._pair = None
        self._ai = None
        self._hud = None
        self._hud_hidden_until = 0.0
        self._banner_job = None
        self.post = lambda fn: self.root.after(0, fn)   # app replaces with a thread-safe queue

    # ----------------------------------------------------------- status pill
    def status(self, text: str, mon: dict | None, ms: int = 1800, color: str = ACC):
        if self._status_win is None:
            w = _popup(self.root, 0.94)
            w.configure(bg=BG)
            frame = tk.Frame(w, bg=BG, highlightbackground=LINE, highlightthickness=1)
            frame.pack()
            self._status_dot = tk.Label(frame, text="●", fg=color, bg=BG, font=FONT_B)
            self._status_dot.pack(side="left", padx=(14, 6), pady=9)
            self._status_lbl = tk.Label(frame, text=text, fg=TXT, bg=BG, font=FONT)
            self._status_lbl.pack(side="left", padx=(0, 16), pady=9)
            self._status_win = w
        self._status_dot.configure(fg=color)
        self._status_lbl.configure(text=text)
        w = self._status_win
        w.update_idletasks()
        mon = mon or {"left": 0, "top": 0, "width": w.winfo_screenwidth(),
                      "height": w.winfo_screenheight()}
        x = mon["left"] + (mon["width"] - w.winfo_reqwidth()) // 2
        y = mon["top"] + mon["height"] - w.winfo_reqheight() - 96
        w.geometry(f"+{x}+{y}")
        w.deiconify()
        make_noactivate(w)
        if self._status_job:
            self.root.after_cancel(self._status_job)
        self._status_job = self.root.after(ms, w.withdraw) if ms else None

    def hide_status(self):
        if self._status_win:
            self._status_win.withdraw()

    # ---------------------------------------------------------------- flash
    def flash(self, mon: dict):
        w = _popup(self.root, 0.55)
        w.configure(bg="white")
        w.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        w.deiconify()
        make_noactivate(w)

        def fade(a=0.55):
            if a <= 0.02:
                w.destroy()
                return
            try:
                w.attributes("-alpha", a)
            except tk.TclError:
                pass
            self.root.after(16, fade, a - 0.07)

        self.root.after(30, fade)

    # ------------------------------------------------------- "in hand" card
    def holding(self, img: Image.Image, mon: dict, title: str, hint: str):
        self.clear_holding()
        w = _popup(self.root, 0.97)
        w.configure(bg=LINE)
        inner = tk.Frame(w, bg=CARD)
        inner.pack(padx=1, pady=1)
        thumb = img.copy()
        thumb.thumbnail((260, 170))
        self._card_img = ImageTk.PhotoImage(thumb)
        tk.Label(inner, image=self._card_img, bg=CARD).pack(padx=10, pady=(10, 6))
        tk.Label(inner, text=title, fg=TXT, bg=CARD, font=FONT_B, anchor="w").pack(fill="x", padx=12)
        tk.Label(inner, text=hint, fg=MUT, bg=CARD, font=(FONT[0], FONT[1] - 2),
                 justify="left", anchor="w", wraplength=260).pack(fill="x", padx=12, pady=(2, 10))
        w.update_idletasks()
        x = mon["left"] + mon["width"] - w.winfo_reqwidth() - 24
        y = mon["top"] + mon["height"] - w.winfo_reqheight() - 72
        # slide in from the right
        start = mon["left"] + mon["width"]
        w.geometry(f"+{start}+{y}")
        w.deiconify()
        make_noactivate(w)
        self._card = w

        def slide(cx=start):
            if self._card is not w:
                return
            nx = cx - max(6, (cx - x) // 4)
            if nx <= x:
                w.geometry(f"+{x}+{y}")
                return
            w.geometry(f"+{nx}+{y}")
            self.root.after(12, slide, nx)

        slide()

    def clear_holding(self):
        if self._card is not None:
            self._card.destroy()
            self._card = None

    # --------------------------------------------------------- region select
    def region_begin(self, mon: dict):
        self.region_end()
        w = _popup(self.root, 1.0 if IS_WIN else 0.35)
        bg = KEY if IS_WIN else "black"
        w.configure(bg=bg)
        if IS_WIN:
            w.attributes("-transparentcolor", KEY)
        w.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        c = tk.Canvas(w, bg=bg, highlightthickness=0, width=mon["width"], height=mon["height"])
        c.pack(fill="both", expand=True)
        self._rect = c.create_rectangle(0, 0, 0, 0, outline=ACC, width=3, dash=(10, 6))
        self._dot = c.create_oval(0, 0, 0, 0, fill=ACC, outline="")
        self._size = c.create_text(0, 0, text="", fill=ACC, font=FONT_B, anchor="sw")
        c.create_text(mon["width"] // 2, 40, fill=TXT, font=FONT_B,
                      text="🤏 Hold the pinch and move · release to capture")
        w.deiconify()
        make_noactivate(w)
        self._region, self._canvas, self._region_mon = w, c, mon

    def region_update(self, a, b):
        if self._region is None:
            return
        m = self._region_mon
        x0, y0 = a[0] * m["width"], a[1] * m["height"]
        x1, y1 = b[0] * m["width"], b[1] * m["height"]
        c = self._canvas
        c.coords(self._rect, min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        c.coords(self._dot, x1 - 7, y1 - 7, x1 + 7, y1 + 7)
        c.coords(self._size, min(x0, x1), min(y0, y1) - 6)
        c.itemconfigure(self._size, text=f"{int(abs(x1 - x0))} × {int(abs(y1 - y0))}")

    def region_end(self):
        if self._region is not None:
            self._region.destroy()
            self._region = None

    # ----------------------------------------------------------- capture prep
    def hide_for_capture(self):
        self.hud_hide(0.45)
        self.hide_status()
        self.region_end()
        self.clear_holding()
        self.root.update_idletasks()

    # -------------------------------------------------------------- pairing
    def pair_window(self, url: str):
        import qrcode
        if self._pair is not None:
            self._pair.destroy()
        w = tk.Toplevel(self.root)
        w.title("PIDI · Pair your phone")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        w._img = ImageTk.PhotoImage(img)
        tk.Label(w, text="Scan with your phone camera", fg=TXT, bg=BG, font=FONT_B).pack(pady=(16, 8))
        tk.Label(w, image=w._img, bg=BG).pack(padx=24)
        tk.Label(w, text="Phone and PC must be on the same Wi-Fi", fg=MUT, bg=BG,
                 font=FONT).pack(pady=(10, 2))
        e = tk.Entry(w, fg=TXT, bg=CARD, insertbackground=TXT, relief="flat", width=46)
        e.insert(0, url)
        e.configure(state="readonly", readonlybackground=CARD)
        e.pack(padx=24, pady=(4, 18), ipady=6)
        self._pair = w

    # ------------------------------------------------------------- AI panel
    def ai_panel(self, img: Image.Image, client, copy_text):
        from .ai import TASKS
        if self._ai is not None:
            self._ai.destroy()
        w = tk.Toplevel(self.root)
        w.title("PIDI · AI")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.geometry("760x520")
        self._ai = w

        left = tk.Frame(w, bg=BG)
        left.pack(side="left", fill="y", padx=14, pady=14)
        thumb = img.copy()
        thumb.thumbnail((240, 180))
        w._img = ImageTk.PhotoImage(thumb)
        tk.Label(left, image=w._img, bg=BG).pack(pady=(0, 10))

        right = tk.Frame(w, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(0, 14), pady=14)
        out = tk.Text(right, bg=CARD, fg=TXT, insertbackground=TXT, relief="flat",
                      wrap="word", font=FONT, padx=10, pady=10)
        out.pack(fill="both", expand=True)
        status = tk.Label(right, text="" if client.ready else
                          f"⚠ Set {client.key_env} to enable AI", fg=WARN if not client.ready else MUT,
                          bg=BG, font=FONT, anchor="w")
        status.pack(fill="x", pady=(6, 0))
        bar = tk.Frame(right, bg=BG)
        bar.pack(fill="x", pady=(8, 0))
        style = ttk.Style(w)
        style.configure("P.TButton", padding=6)
        ttk.Button(bar, text="Copy result", style="P.TButton",
                   command=lambda: copy_text(out.get("1.0", "end").strip())).pack(side="left")
        ttk.Button(bar, text="Close", style="P.TButton", command=w.destroy).pack(side="right")

        def run(task):
            out.delete("1.0", "end")
            status.configure(text=f"Thinking… ({task})", fg=MUT)

            def work():
                try:
                    res, err = client.ask(img, task), None
                except Exception as e:  # noqa: BLE001
                    res, err = None, str(e)

                def done():
                    if not w.winfo_exists():
                        return
                    if err:
                        status.configure(text=err, fg=WARN)
                    else:
                        out.insert("1.0", res)
                        status.configure(text=f"✓ {task}", fg=ACC)
                self.post(done)          # back to the Tk thread

            threading.Thread(target=work, daemon=True).start()

        for name in TASKS:
            ttk.Button(left, text=name, style="P.TButton",
                       command=lambda n=name: run(n)).pack(fill="x", pady=3)
        w.focus_force()

    # ------------------------------------------------------ demo camera HUD
    STATE_TXT = {"idle": ("WATCHING", MUT), "armed": ("READY", "#ffbe28"),
                 "region": ("SELECTING", "#c86eff"), "holding": ("IN HAND", ACC),
                 "cooldown": ("DONE", MUT)}

    def _hud_build(self, corner: str):
        import time as _t
        w = _popup(self.root, 0.97)
        w.configure(bg=LINE)
        box = tk.Frame(w, bg=BG)
        box.pack(padx=1, pady=1)
        top = tk.Frame(box, bg=BG)
        top.pack(fill="x", padx=10, pady=(8, 6))
        tk.Label(top, text="\u25cf", fg="#ff4d4d", bg=BG, font=FONT_B).pack(side="left")
        tk.Label(top, text=" PIDI  \u00b7  LIVE HAND TRACKING", fg=TXT, bg=BG,
                 font=(FONT[0], FONT[1] - 1, "bold")).pack(side="left")
        self._hud_fps = tk.Label(top, text="", fg=MUT, bg=BG, font=(FONT[0], FONT[1] - 2))
        self._hud_fps.pack(side="right")
        stage = tk.Frame(box, bg=BG)
        stage.pack(padx=10)
        self._hud_img = tk.Label(stage, bg="black", bd=0)
        self._hud_img.pack()
        self._hud_banner = tk.Label(stage, text="", fg="#0b0d10", bg=ACC,
                                    font=(FONT[0], FONT[1] + 9, "bold"), padx=16, pady=4)
        bot = tk.Frame(box, bg=BG)
        bot.pack(fill="x", padx=10, pady=(6, 9))
        self._hud_pose = tk.Label(bot, text="", fg=TXT, bg=CARD, font=FONT_B, padx=10, pady=3)
        self._hud_pose.pack(side="left")
        self._hud_state = tk.Label(bot, text="", fg=ACC, bg=CARD, font=FONT_B, padx=10, pady=3)
        self._hud_state.pack(side="right")
        self._hud = w
        self._hud_corner = corner
        self._hud_placed = False
        self._hud_t = _t

    def _hud_place(self):
        w = self._hud
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
        m = 20
        x = m if "left" in self._hud_corner else sw - ww - m
        y = m if "top" in self._hud_corner else sh - wh - 70
        w.geometry(f"+{x}+{y}")
        self._hud_placed = True

    def hud_update(self, rgb, pose: str, state: str, fps: float, corner="top-right"):
        import time as _t
        if self._hud is None:
            self._hud_build(corner)
        if _t.monotonic() < self._hud_hidden_until:
            return
        img = ImageTk.PhotoImage(Image.fromarray(rgb))
        self._hud_img.configure(image=img)
        self._hud_img.image = img
        self._hud_pose.configure(text=pose.replace("_", " ").upper() if pose != "none" else "NO HAND")
        txt, col = self.STATE_TXT.get(state, (state.upper(), MUT))
        self._hud_state.configure(text=txt, fg=col)
        self._hud_fps.configure(text=f"{fps:.0f} FPS")
        if not self._hud_placed:
            self._hud_place()
        if self._hud.state() == "withdrawn":
            self._hud.deiconify()
            make_noactivate(self._hud)

    def hud_event(self, text: str, color: str = ACC, ms: int = 900):
        if self._hud is None:
            return
        self._hud_banner.configure(text=text, bg=color)
        self._hud_banner.place(relx=0.5, rely=0.5, anchor="center")
        if self._banner_job:
            self.root.after_cancel(self._banner_job)
        self._banner_job = self.root.after(ms, self._hud_banner.place_forget)

    def hud_hide(self, seconds: float | None = None):
        import time as _t
        if self._hud is None:
            return
        if seconds is None:              # turned off
            self._hud.destroy()
            self._hud = None
            return
        self._hud_hidden_until = _t.monotonic() + seconds
        self._hud.withdraw()
        self.root.update_idletasks()
