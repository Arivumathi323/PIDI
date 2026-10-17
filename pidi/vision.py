"""Camera + MediaPipe hand tracking loop (runs in a background thread)."""
from __future__ import annotations

import os
import queue
import threading
import time
import urllib.request
from pathlib import Path

from .gestures import Event, GestureEngine, Pose, classify, pick_main_hand
from .platform_utils import IS_WIN

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")
MODEL_PATH = Path.home() / ".pidi" / "models" / "hand_landmarker.task"


def ensure_model() -> Path:
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_PATH.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        os.replace(tmp, MODEL_PATH)
    return MODEL_PATH


class HandTracker:
    """MediaPipe Tasks HandLandmarker, falling back to the legacy solutions API."""

    def __init__(self, max_hands=2):
        import mediapipe as mp
        self.mp = mp
        self._t0 = time.monotonic()
        self._last_ts = -1
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
            opts = mp_vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(ensure_model())),
                running_mode=mp_vision.RunningMode.VIDEO,
                num_hands=max_hands,
                min_hand_detection_confidence=0.6,
                min_hand_presence_confidence=0.6,
                min_tracking_confidence=0.5,
            )
            self._lm = mp_vision.HandLandmarker.create_from_options(opts)
            self.mode = "tasks"
        except Exception:
            self._hands = mp.solutions.hands.Hands(
                max_num_hands=max_hands, model_complexity=0,
                min_detection_confidence=0.6, min_tracking_confidence=0.5)
            self.mode = "solutions"

    def process(self, rgb) -> list[list[tuple]]:
        if self.mode == "tasks":
            ts = int((time.monotonic() - self._t0) * 1000)
            ts = max(ts, self._last_ts + 1)          # must strictly increase
            self._last_ts = ts
            img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
            res = self._lm.detect_for_video(img, ts)
            return [[(p.x, p.y, p.z) for p in hand] for hand in (res.hand_landmarks or [])]
        res = self._hands.process(rgb)
        return [[(p.x, p.y, p.z) for p in h.landmark] for h in (res.multi_hand_landmarks or [])]

    def close(self):
        try:
            (self._lm if self.mode == "tasks" else self._hands).close()
        except Exception:
            pass


BACKENDS_WIN = ("msmf", "dshow", "any")


def _backend_id(cv2, name):
    return {"msmf": cv2.CAP_MSMF, "dshow": cv2.CAP_DSHOW}.get(name, cv2.CAP_ANY)


def _try_open(cv2, idx, backend, w, h, warmup=15):
    cap = cv2.VideoCapture(idx, _backend_id(cv2, backend))
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    for _ in range(warmup):            # many webcams return empty frames at first
        ok, frame = cap.read()
        if ok and frame is not None and frame.size and frame.mean() > 1:
            return cap
        time.sleep(0.08)
    cap.release()
    return None


def open_camera(index=0, w=640, h=360, backend="auto"):
    """Open a webcam robustly: preferred index first, then other indices and backends.
    Returns (cap, description). Raises RuntimeError with a helpful message."""
    import cv2
    backends = (BACKENDS_WIN if IS_WIN else ("any",)) if backend == "auto" else (backend,)
    indices = [index] + [i for i in range(4) if i != index]
    for idx in indices:
        for be in backends:
            cap = _try_open(cv2, idx, be, w, h)
            if cap is not None:
                return cap, f"index {idx} via {be}"
    raise RuntimeError(
        "No working camera. Check: ThinkShutter slider open, camera hotkey (F8/F9) on, "
        "Settings > Privacy & security > Camera > 'Let desktop apps access your camera' ON, "
        "and close Zoom/Meet/Teams/Camera app.")


def scan_cameras(w=640, h=360):
    """List every index/backend combo that delivers real frames."""
    import cv2
    found = []
    for idx in range(4):
        for be in (BACKENDS_WIN if IS_WIN else ("any",)):
            cap = _try_open(cv2, idx, be, w, h, warmup=10)
            if cap is not None:
                ok, frame = cap.read()
                found.append((idx, be, f"{frame.shape[1]}x{frame.shape[0]}" if ok else "?"))
                cap.release()
    return found


_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
                (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
                (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


class VisionLoop(threading.Thread):
    """Reads the webcam, classifies the hand, feeds the engine, emits events."""

    def __init__(self, engine: GestureEngine, out: queue.Queue, settings: dict,
                 dry_run: bool = False):
        super().__init__(daemon=True, name="pidi-vision")
        self.engine = engine
        self.out = out
        self.s = settings
        self.dry_run = dry_run              # preview only, no events
        self.paused = threading.Event()
        self.preview = threading.Event()
        self.hud = threading.Event()        # demo HUD frames for the Tk overlay
        self.hud_frame = None               # (seq, rgb ndarray, pose, state, fps)
        self._hud_seq = 0
        if dry_run:
            self.preview.set()
        self._stop = threading.Event()
        self._preview_open = False
        self.fps = 0.0

    def stop(self):
        self._stop.set()

    def _open_camera(self):
        cap, desc = open_camera(int(self.s.get("camera_index", 0)),
                                int(self.s.get("camera_width", 640)),
                                int(self.s.get("camera_height", 360)),
                                self.s.get("camera_backend", "auto"))
        print(f"[pidi] camera: {desc}")
        return cap

    def run(self):
        import cv2
        try:
            tracker = HandTracker()
        except Exception as e:  # pragma: no cover - needs mediapipe
            self.out.put(Event("error", {"message": f"Hand tracker failed: {e}"}))
            return

        cap = None
        last_hand = 0.0
        frames, fps_t = 0, time.monotonic()
        try:
            while not self._stop.is_set():
                if self.paused.is_set():            # privacy: release camera, light off
                    if cap is not None:
                        cap.release()
                        cap = None
                        cv2.destroyAllWindows()
                    time.sleep(0.2)
                    continue
                if cap is None:
                    try:
                        cap = self._open_camera()
                    except Exception as e:
                        self.out.put(Event("error", {"message": str(e)}))
                        time.sleep(3)
                        continue

                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                hands = tracker.process(rgb)
                lm = pick_main_hand(hands)
                feat = classify(lm) if lm else None
                now = time.monotonic()
                if feat is not None:
                    last_hand = now

                if not self.dry_run:
                    for ev in self.engine.update(feat, now):
                        self.out.put(ev)

                if self.hud.is_set():
                    self._hud_seq += 1
                    self.hud_frame = (self._hud_seq, self._render_hud(frame, lm, feat),
                                      feat.pose.value if feat else Pose.NONE.value,
                                      self.engine.state.value, self.fps)

                if self.preview.is_set():
                    self._draw_preview(frame, lm, feat)
                    self._preview_open = True
                elif self._preview_open:
                    cv2.destroyAllWindows()
                    cv2.waitKey(1)
                    self._preview_open = False

                frames += 1
                if now - fps_t >= 1.0:
                    self.fps = frames / (now - fps_t)
                    frames, fps_t = 0, now

                # adaptive frame rate: fast when a hand is around, slow when idle
                busy = (now - last_hand) < 3.0 or self.engine.state.value != "idle"
                target = 1.0 / (24 if busy else 8)
                spare = target - (time.monotonic() - t0)
                if spare > 0:
                    time.sleep(spare)
        finally:
            if cap is not None:
                cap.release()
            tracker.close()
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

    # skeleton colour per engine state (BGR)
    HUD_COLORS = {"idle": (255, 214, 92), "armed": (40, 190, 255), "region": (255, 110, 200),
                  "holding": (143, 224, 62), "cooldown": (190, 190, 190)}

    def _render_hud(self, frame, lm, feat, width=420):
        """Mirrored camera frame with a glowing hand skeleton, returned as RGB."""
        import cv2
        h, w = frame.shape[:2]
        H = int(width * h / w)
        view = cv2.resize(cv2.flip(frame, 1), (width, H), interpolation=cv2.INTER_AREA)
        view = cv2.convertScaleAbs(view, alpha=0.82, beta=0)          # dim so the skeleton pops
        col = self.HUD_COLORS.get(self.engine.state.value, (255, 214, 92))
        if lm:
            pts = [(int((1 - p[0]) * width), int(p[1] * H)) for p in lm]
            glow = view.copy()
            for a, b in _CONNECTIONS:
                cv2.line(glow, pts[a], pts[b], col, 9, cv2.LINE_AA)
            view = cv2.addWeighted(glow, 0.35, view, 0.65, 0)
            for a, b in _CONNECTIONS:
                cv2.line(view, pts[a], pts[b], col, 2, cv2.LINE_AA)
            for i, p in enumerate(pts):
                tip = i in (4, 8, 12, 16, 20)
                cv2.circle(view, p, 6 if tip else 3, col if tip else (255, 255, 255), -1, cv2.LINE_AA)
            if feat is not None and feat.pose == Pose.PINCH:
                cx = (pts[4][0] + pts[8][0]) // 2
                cy = (pts[4][1] + pts[8][1]) // 2
                cv2.circle(view, (cx, cy), 14, col, 2, cv2.LINE_AA)
        return cv2.cvtColor(view, cv2.COLOR_BGR2RGB)

    def _draw_preview(self, frame, lm, feat):
        import cv2
        h, w = frame.shape[:2]
        view = cv2.flip(frame, 1)
        if lm:
            pts = [(int((1 - p[0]) * w), int(p[1] * h)) for p in lm]
            for a, b in _CONNECTIONS:
                cv2.line(view, pts[a], pts[b], (120, 220, 255), 2)
            for p in pts:
                cv2.circle(view, p, 3, (255, 255, 255), -1)
        pose = feat.pose.value if feat else Pose.NONE.value
        label = f"{pose}  |  {self.engine.state.value}  |  {self.fps:.0f} fps"
        cv2.rectangle(view, (0, 0), (w, 30), (20, 20, 20), -1)
        cv2.putText(view, label, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 255, 160), 2)
        cv2.imshow("PIDI preview", view)
        if cv2.waitKey(1) & 0xFF in (27, ord("q")):
            if self.dry_run:
                self.stop()
            else:
                self.preview.clear()
