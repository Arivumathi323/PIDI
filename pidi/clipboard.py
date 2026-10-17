"""Put images/text on the system clipboard and fire a paste keystroke."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time

from PIL import Image

from .platform_utils import IS_MAC, IS_WIN


class ClipboardError(RuntimeError):
    pass


def _win_open(retries=10):
    import win32clipboard
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return win32clipboard
        except Exception:
            time.sleep(0.05)          # another app is holding the clipboard
    raise ClipboardError("Clipboard is busy")


def copy_image(img: Image.Image):
    if IS_WIN:
        bmp = io.BytesIO()
        img.convert("RGB").save(bmp, "BMP")
        dib = bmp.getvalue()[14:]     # strip BITMAPFILEHEADER -> CF_DIB
        png = io.BytesIO()
        img.save(png, "PNG")
        wc = _win_open()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardData(wc.CF_DIB, dib)                         # Paint, Word, WhatsApp…
            wc.SetClipboardData(wc.RegisterClipboardFormat("PNG"), png.getvalue())  # browsers, Figma
        finally:
            wc.CloseClipboard()
        return

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path, "PNG")
    try:
        if IS_MAC:
            subprocess.run(["osascript", "-e",
                            f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'],
                           check=True)
        elif shutil.which("wl-copy") and os.environ.get("WAYLAND_DISPLAY"):
            with open(path, "rb") as f:
                subprocess.run(["wl-copy", "--type", "image/png"], stdin=f, check=True)
        elif shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png", "-i", path], check=True)
        else:
            raise ClipboardError("Install xclip or wl-clipboard")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def copy_text(text: str):
    if IS_WIN:
        wc = _win_open()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardData(wc.CF_UNICODETEXT, text)
        finally:
            wc.CloseClipboard()
        return
    cmd = (["pbcopy"] if IS_MAC else
           ["wl-copy"] if shutil.which("wl-copy") and os.environ.get("WAYLAND_DISPLAY") else
           ["xclip", "-selection", "clipboard"])
    subprocess.run(cmd, input=text.encode("utf-8"), check=True)


def paste():
    """Send Ctrl+V (Cmd+V on mac) to whatever app has focus."""
    from pynput.keyboard import Controller, Key
    kb = Controller()
    mod = Key.cmd if IS_MAC else Key.ctrl
    time.sleep(0.05)
    with kb.pressed(mod):
        kb.press("v")
        kb.release("v")
