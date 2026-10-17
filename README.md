# PIDI (பிடி) — grab your screen with your hand

Huawei-style air-gesture screenshots for **any** Windows laptop with a webcam — plus things Huawei doesn't do:
region select by pinching, throwing grabs to your phone (no app install), phone → PC drops, and AI on any grab.

By **Aarvan Labs**.

## Gestures

| Gesture | What happens |
|---|---|
| ✋ Show open palm (~⅓ s) | **Armed** — "Ready" pill appears |
| ✋ → ✊ close your hand | **Grab** whole screen (or active window), copied to clipboard |
| ✋ → 🤏 pinch, move, release | **Region grab** — draw the box with your hand |
| while holding: ✊ then ✋ | **Drop** — pastes into whatever app is focused |
| while holding: ✊ fast sideways swipe | **Throw** to your paired phone |
| while holding: 👍 thumbs up | **AI panel** — extract text, explain, summarize, translate → Tamil, table → CSV |
| Phone: "Send a photo to PC" | Lands "in your hand" on the PC — ✊ then ✋ to drop it anywhere |

Every grab is also on the normal clipboard (Ctrl+V works) and saved in `Pictures\PIDI`.

**Flow for pasting into another app:** grab → put your hand down → switch app with mouse/Alt+Tab →
show a fist → open palm. Relaxing your hand right after grabbing does *not* paste (built-in "carry time").

## Setup (Windows, not WSL — WSL can't see the webcam)

1. Install **Python 3.12** (or 3.11) from python.org and tick "Add to PATH".
2. Double-click `setup.bat`. It creates a venv, installs everything and runs the doctor.
3. Double-click `run_pidi.bat`. PIDI lives in the system tray (green hand icon).

The first run downloads the MediaPipe hand model (~8 MB) to `~/.pidi/models`.
Allow the Windows Firewall prompt on **private networks** if you want Phone Drop.

Run these from the project folder with the venv's Python (`.\.venv\Scripts\python.exe -m pidi ...`):

```
python -m pidi            # run
python -m pidi --demo     # run with the floating camera HUD (great for recording demos)
python -m pidi --preview  # camera window with live pose + state labels (for tuning)
python -m pidi --doctor   # check camera, tracker, screens, clipboard
python -m pidi --cameras  # list every working camera index/backend
```

> PowerShell tip: scripts in the current folder need `.\` in front, e.g. `.\setup.bat`.

## Phone Drop

Tray → **Pair phone…** → scan the QR code. The phone just opens a web page on your Wi-Fi.
The URL carries a random token, so nobody else on the network can read your grabs.

## AI actions

Set a Groq key once (free tier works):

```
setx GROQ_API_KEY "gsk_..."
```

Any OpenAI-compatible vision endpoint works — change `ai.base_url` / `ai.model` in settings.

## Settings — `~/.pidi/settings.json`

Created on first run. Useful knobs:

| Key | Default | Meaning |
|---|---|---|
| `grab_target` | `screen` | `screen` or `window` (also in tray menu) |
| `camera_index` | `0` | use `1` for an external webcam |
| `engine.arm_time` | `0.35` | how long to show the palm before arming |
| `engine.grab_window` | `1.4` | max seconds between palm and fist |
| `engine.min_palm_size` | `0.07` | raise it if people behind you trigger it |
| `engine.min_carry` | `0.8` | relax time after a grab that never pastes |
| `engine.throw_distance` | `1.3` | swipe length (in palm widths) for a throw |
| `phone_port` | `8765` | Phone Drop port |

Use `--preview` while tuning. It shows exactly what pose and state PIDI sees.

## Privacy

- All hand tracking runs **locally**. No frames leave the PC.
- "Gestures on" toggle in the tray **releases the camera** (light goes off), not just ignores it.
- The frame rate drops to 8 fps when no hand is around, to save battery.
- AI actions only send the image when you press an AI button.

## Architecture

```
webcam ─► vision.py (MediaPipe, adaptive fps) ─► gestures.py (pose classifier + state machine)
                                                        │ events
                         ┌──────────────────────────────┴───────────────────────────┐
                    app.py (Tk main thread, event pump, tray)                        │
          ┌───────────┬─────────┴───┬──────────────┬───────────────┐                 │
     capture.py  clipboard.py   overlay.py      phone.py         ai.py               │
     (mss, DPI)  (DIB+PNG,      (pill, flash,   (local web app,  (OpenAI-compatible  │
                 Ctrl+V)        card, region,   QR pairing)      vision)             │
                                AI panel)          └─ uploads ──► engine.inject_holding
```

`gestures.py` is pure logic with no camera, so it's fully unit-tested on synthetic hands:

```
pip install pytest && python -m pytest -q
```

### Why it doesn't misfire
- Pose = majority vote over 5 frames (one bad frame can't flip it).
- A grab needs a deliberate *palm → fist* sequence within 1.4 s. A random fist does nothing.
- Hands that are small in frame (far away / someone behind you) are ignored.
- After a drop, the palm must leave the frame before PIDI re-arms.
- The classifier uses distance ratios from the wrist, so it works with tilted or rotated hands.

## Roadmap ideas
- Hand-cursor drop: open the palm *over* a window to paste there.
- Screen recording: hold ✊ for 2 s to start, ✋ to stop → GIF/MP4.
- Android companion app for background receive + system share sheet.
- Offline OCR fallback (Windows OCR API) when there's no API key.
- PyInstaller single `.exe` + auto-start with Windows.
- Integrate as a gesture "sense" module in AARVAN OS.
