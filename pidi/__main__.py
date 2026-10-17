"""python -m pidi            run the app
python -m pidi --demo     run with the floating camera HUD (for videos)
python -m pidi --preview  camera window with live pose labels (tuning, no actions)
python -m pidi --doctor   check camera, tracker, clipboard and dependencies
python -m pidi --cameras  list every working camera index/backend
"""
from __future__ import annotations

import argparse
import importlib
import queue
import sys


def doctor() -> int:
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            msg = fn() or "ok"
            print(f"  ✓ {name}: {msg}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"  ✗ {name}: {e}")

    print(f"PIDI doctor — Python {sys.version.split()[0]} on {sys.platform}")
    if sys.version_info >= (3, 13):
        print("  ! MediaPipe wheels can lag behind new Python versions; 3.11/3.12 is the safe choice.")
    for mod in ("cv2", "mediapipe", "mss", "PIL", "pynput", "pystray", "qrcode"):
        check(f"import {mod}", lambda m=mod: getattr(importlib.import_module(m), "__version__", "ok"))
    if sys.platform == "win32":
        check("import win32clipboard", lambda: importlib.import_module("win32clipboard") and "ok")

    def cam():
        from pidi.vision import open_camera
        cap, desc = open_camera()
        cap.release()
        return desc

    def tracker():
        from pidi.vision import HandTracker
        t = HandTracker()
        mode = t.mode
        t.close()
        return f"MediaPipe {mode} API"

    def screen():
        from pidi import capture
        from pidi.platform_utils import enable_dpi_awareness
        enable_dpi_awareness()
        mons = capture.monitors()
        return ", ".join(f"{m['width']}x{m['height']}@({m['left']},{m['top']})" for m in mons)

    def clip():
        from PIL import Image
        from pidi import clipboard
        clipboard.copy_image(Image.new("RGB", (8, 8), (62, 224, 143)))
        return "image copied (paste somewhere to confirm: a green square)"

    check("camera", cam)
    check("hand tracker", tracker)
    check("screens", screen)
    check("clipboard", clip)
    print("\nAll good — run: python -m pidi" if ok else "\nFix the ✗ items above, then run again.")
    return 0 if ok else 1


def preview() -> int:
    import time
    from pidi import config
    from pidi.gestures import EngineConfig, GestureEngine
    from pidi.vision import VisionLoop
    s = config.load()
    loop = VisionLoop(GestureEngine(EngineConfig.from_dict(s.get("engine"))), queue.Queue(), s,
                      dry_run=True)
    loop.start()
    print("Preview running — press Q or Esc in the camera window to exit.")
    try:
        while loop.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        loop.stop()
    return 0


def main():
    p = argparse.ArgumentParser(prog="pidi", description="Grab your screen with your hand.")
    p.add_argument("--preview", action="store_true", help="camera window with pose labels")
    p.add_argument("--doctor", action="store_true", help="check setup")
    p.add_argument("--demo", action="store_true", help="run with the floating camera HUD")
    p.add_argument("--cameras", action="store_true", help="list working cameras")
    a = p.parse_args()
    if a.doctor:
        sys.exit(doctor())
    if a.cameras:
        from pidi.vision import scan_cameras
        found = scan_cameras()
        for idx, be, size in found:
            print(f"  camera_index={idx}  camera_backend={be}  ({size})")
        if not found:
            print("No camera delivered frames. See the privacy / shutter tips in the README.")
        sys.exit(0 if found else 1)
    if a.preview:
        sys.exit(preview())
    from pidi.app import main as run
    run(demo=a.demo)


if __name__ == "__main__":
    main()
