"""Screen capture: whole monitor, active window, or a rectangle."""
from __future__ import annotations

import mss
from PIL import Image

from .platform_utils import foreground_window_rect


def monitors() -> list[dict]:
    with mss.mss() as s:
        return [dict(m) for m in s.monitors[1:]]


def monitor_at(x: int, y: int) -> dict:
    mons = monitors()
    for m in mons:
        if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
            return m
    return mons[0]


def grab_box(left: int, top: int, width: int, height: int) -> Image.Image:
    with mss.mss() as s:
        shot = s.grab({"left": int(left), "top": int(top),
                       "width": max(1, int(width)), "height": max(1, int(height))})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def grab_monitor(mon: dict) -> Image.Image:
    return grab_box(mon["left"], mon["top"], mon["width"], mon["height"])


def grab_window_or_monitor(mon: dict) -> Image.Image:
    r = foreground_window_rect()
    if r:
        return grab_box(*r)
    return grab_monitor(mon)


def grab_region(mon: dict, rect_norm: tuple) -> Image.Image:
    x0, y0, x1, y1 = rect_norm
    w, h = mon["width"], mon["height"]
    return grab_box(mon["left"] + x0 * w, mon["top"] + y0 * h, (x1 - x0) * w, (y1 - y0) * h)
