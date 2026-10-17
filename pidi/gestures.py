"""PIDI gesture engine.

Pure logic: no camera, no UI. Feed it hand landmarks + timestamps and it
returns gesture events. Everything here is unit-testable on synthetic hands.

Gesture language
----------------
  ✋ open palm (hold ~0.35s)      -> ARMED   ("ready to grab")
  ✋ -> ✊ (within window)          -> GRAB    (screenshot full screen / window)
  ✋ -> 🤏 pinch, move, release     -> REGION  (screenshot a selected area)
  holding: ✊ then ✋               -> DROP    (paste into the focused app)
  holding: ✊ fast swipe sideways   -> THROW   (send to paired phone)
  holding: 👍 thumbs up             -> AI      (open the AI panel)

False-trigger protection
------------------------
  * pose must be stable across several frames (majority vote + hysteresis)
  * grab only counts if an open palm was shown first (deliberate arming)
  * small/far hands are ignored (people walking behind you)
  * after a drop, the open palm must go away before re-arming
  * relaxing your hand right after a grab does NOT paste (min carry time)
"""
from __future__ import annotations

import math
import threading
from collections import Counter, deque
from dataclasses import dataclass, field, asdict
from enum import Enum

# MediaPipe hand landmark indices
WRIST = 0
THUMB = (1, 2, 3, 4)
INDEX = (5, 6, 7, 8)
MIDDLE = (9, 10, 11, 12)
RING = (13, 14, 15, 16)
PINKY = (17, 18, 19, 20)


class Pose(str, Enum):
    NONE = "none"          # no (valid) hand in view
    OPEN_PALM = "open_palm"
    FIST = "fist"
    PINCH = "pinch"
    THUMBS_UP = "thumbs_up"
    POINT = "point"
    OTHER = "other"


class State(str, Enum):
    IDLE = "idle"
    ARMED = "armed"
    REGION = "region"
    HOLDING = "holding"
    COOLDOWN = "cooldown"


def _d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class HandFeatures:
    pose: Pose
    palm_size: float          # wrist -> middle MCP, normalized image units
    wrist: tuple              # (x, y) mirrored, normalized
    pointer: tuple            # (x, y) mirrored, normalized (pinch midpoint or index tip)
    extended: tuple = ()      # (thumb, index, middle, ring, pinky)


def classify(lm, mirror: bool = True) -> HandFeatures:
    """Classify one hand from 21 (x, y[, z]) landmarks.

    Uses distance ratios from the wrist, so it works with the hand rotated,
    tilted, near or far — not just "fingers pointing up".
    """
    palm = _d(lm[WRIST], lm[9]) or 1e-6

    def finger_extended(f) -> bool:
        _mcp, pip, _dip, tip = f
        return _d(lm[WRIST], lm[tip]) > _d(lm[WRIST], lm[pip]) * 1.10

    idx, mid, rng, pnk = (finger_extended(f) for f in (INDEX, MIDDLE, RING, PINKY))
    thumb = (_d(lm[4], lm[17]) > _d(lm[3], lm[17]) * 1.05
             and _d(lm[4], lm[5]) > 0.30 * palm)

    pinch = (_d(lm[4], lm[8]) < 0.28 * palm
             and _d(lm[WRIST], lm[8]) > _d(lm[WRIST], lm[5]) * 1.2)

    thumbs_up = (thumb and not (idx or mid or rng or pnk)
                 and (lm[WRIST][1] - lm[4][1]) > 0.5 * palm
                 and lm[4][1] < lm[5][1] - 0.3 * palm)

    if pinch:
        pose = Pose.PINCH
    elif thumbs_up:
        pose = Pose.THUMBS_UP
    elif idx and mid and rng and pnk:
        pose = Pose.OPEN_PALM
    elif not (idx or mid or rng or pnk):
        pose = Pose.FIST
    elif idx and not (mid or rng or pnk):
        pose = Pose.POINT
    else:
        pose = Pose.OTHER

    if pinch:
        px, py = (lm[4][0] + lm[8][0]) / 2, (lm[4][1] + lm[8][1]) / 2
    else:
        px, py = lm[8][0], lm[8][1]

    def m(x):
        return 1.0 - x if mirror else x

    return HandFeatures(
        pose=pose,
        palm_size=palm,
        wrist=(m(lm[WRIST][0]), lm[WRIST][1]),
        pointer=(m(px), py),
        extended=(thumb, idx, mid, rng, pnk),
    )


def pick_main_hand(hands):
    """Given several landmark lists, return the largest (closest) hand."""
    if not hands:
        return None
    return max(hands, key=lambda lm: _d(lm[WRIST], lm[9]))


@dataclass
class EngineConfig:
    arm_time: float = 0.35         # open palm hold before arming
    grab_window: float = 1.4       # max gap palm -> fist
    disarm_after: float = 0.9      # armed but palm gone this long -> idle
    smoothing_frames: int = 5      # majority-vote window
    stable_min: int = 3            # votes needed to change pose
    min_palm_size: float = 0.07    # ignore tiny/far hands
    min_carry: float = 0.8         # ignore relaxing right after grab
    drop_hold: float = 0.12        # open palm must last this long to drop
    hold_timeout: float = 25.0     # forget the "hand state" after this
    cooldown: float = 0.9
    throw_distance: float = 1.3    # in palm sizes
    throw_time: float = 0.35       # seconds the swipe must happen within
    thumbs_hold: float = 0.3
    region_lost: float = 1.0
    region_min: float = 0.04       # min region side, fraction of screen
    active_margin: float = 0.15    # camera border not mapped to screen
    cursor_alpha: float = 0.4      # pointer smoothing (EMA)

    @classmethod
    def from_dict(cls, d: dict | None) -> "EngineConfig":
        cfg = cls()
        for k, v in (d or {}).items():
            if hasattr(cfg, k):
                setattr(cfg, k, type(getattr(cfg, k))(v))
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Event:
    kind: str                       # armed, disarmed, grab, region_start, region_move,
                                    # region_grab, region_cancel, drop, throw, ai,
                                    # hold_timeout
    data: dict = field(default_factory=dict)


class GestureEngine:
    def __init__(self, cfg: EngineConfig | None = None):
        self.cfg = cfg or EngineConfig()
        self._lock = threading.Lock()
        self.reset()

    # ------------------------------------------------------------------ public
    def reset(self):
        self.state = State.IDLE
        self._votes: deque = deque(maxlen=self.cfg.smoothing_frames)
        self.pose = Pose.NONE
        self._pose_t = 0.0
        self._last_palm = 0.0
        self._cooldown_until = 0.0
        self._need_reset = False
        self._hold_start = 0.0
        self._grip_seen = False
        self._wrist_hist: deque = deque()
        self._cursor = None
        self._region_a = None
        self._region_b = None
        self._last_pinch = 0.0
        self._last_seen = 0.0

    def inject_holding(self, t: float):
        """Something arrived from outside (e.g. phone) — treat as 'in hand'."""
        with self._lock:
            self._enter_holding(t)
            self._grip_seen = False

    def update(self, feat: HandFeatures | None, t: float) -> list[Event]:
        with self._lock:
            return self._update(feat, t)

    # ---------------------------------------------------------------- internal
    def _smooth(self, raw: Pose, t: float):
        self._votes.append(raw)
        mode, count = Counter(self._votes).most_common(1)[0]
        if count >= self.cfg.stable_min and mode != self.pose:
            self.pose = mode
            self._pose_t = t

    def _map_cursor(self, p):
        m = self.cfg.active_margin
        x = min(1.0, max(0.0, (p[0] - m) / (1 - 2 * m)))
        y = min(1.0, max(0.0, (p[1] - m) / (1 - 2 * m)))
        if self._cursor is None:
            self._cursor = (x, y)
        else:
            a = self.cfg.cursor_alpha
            self._cursor = (self._cursor[0] + a * (x - self._cursor[0]),
                            self._cursor[1] + a * (y - self._cursor[1]))
        return self._cursor

    def _enter_holding(self, t):
        self.state = State.HOLDING
        self._hold_start = t
        self._grip_seen = False
        self._wrist_hist.clear()

    def _enter_cooldown(self, t, need_reset=False):
        self.state = State.COOLDOWN
        self._cooldown_until = t + self.cfg.cooldown
        self._need_reset = need_reset

    def _update(self, feat, t) -> list[Event]:
        cfg = self.cfg
        ev: list[Event] = []

        if feat is not None and feat.palm_size < cfg.min_palm_size:
            feat = None                       # too far away / background person
        raw = feat.pose if feat else Pose.NONE
        if feat:
            self._last_seen = t
        self._smooth(raw, t)
        pose = self.pose
        in_pose = t - self._pose_t

        if self._need_reset and pose != Pose.OPEN_PALM:
            self._need_reset = False

        st = self.state

        if st == State.COOLDOWN:
            if t >= self._cooldown_until:
                self.state = State.IDLE
            return ev

        if st == State.IDLE:
            if pose == Pose.OPEN_PALM and in_pose >= cfg.arm_time and not self._need_reset:
                self.state = State.ARMED
                self._last_palm = t
                ev.append(Event("armed", {"pointer": feat.pointer if feat else None}))
            return ev

        if st == State.ARMED:
            if pose == Pose.OPEN_PALM:
                self._last_palm = t
            elif pose == Pose.FIST and t - self._last_palm <= cfg.grab_window:
                self._enter_holding(t)
                ev.append(Event("grab"))
            elif pose == Pose.PINCH and feat is not None:
                self._cursor = None
                self._region_a = self._map_cursor(feat.pointer)
                self._region_b = self._region_a
                self._last_pinch = t
                self.state = State.REGION
                ev.append(Event("region_start", {"a": self._region_a}))
            elif t - self._last_palm > cfg.disarm_after:
                self.state = State.IDLE
                ev.append(Event("disarmed"))
            return ev

        if st == State.REGION:
            if pose == Pose.PINCH:
                if feat is not None and feat.pose == Pose.PINCH:
                    self._region_b = self._map_cursor(feat.pointer)
                self._last_pinch = t
                ev.append(Event("region_move", {"a": self._region_a, "b": self._region_b}))
            elif pose == Pose.NONE:
                if t - self._last_pinch > cfg.region_lost:
                    self.state = State.IDLE
                    ev.append(Event("region_cancel"))
            else:  # pinch released -> take the shot
                (ax, ay), (bx, by) = self._region_a, self._region_b
                rect = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
                if rect[2] - rect[0] >= cfg.region_min and rect[3] - rect[1] >= cfg.region_min:
                    self._enter_holding(t)
                    ev.append(Event("region_grab", {"rect": rect}))
                else:
                    self._enter_cooldown(t)
                    ev.append(Event("region_cancel"))
            return ev

        if st == State.HOLDING:
            carried = t - self._hold_start

            # throw: fast sideways swipe with a closed fist
            if feat is not None and pose == Pose.FIST:
                self._wrist_hist.append((t, feat.wrist[0], feat.palm_size))
                while self._wrist_hist and t - self._wrist_hist[0][0] > cfg.throw_time:
                    self._wrist_hist.popleft()
                if len(self._wrist_hist) >= 3:
                    dx = self._wrist_hist[-1][1] - self._wrist_hist[0][1]
                    if abs(dx) > cfg.throw_distance * self._wrist_hist[-1][2]:
                        self._enter_cooldown(t, need_reset=False)
                        ev.append(Event("throw", {"direction": "right" if dx > 0 else "left"}))
                        return ev
            else:
                self._wrist_hist.clear()

            if pose == Pose.FIST and carried >= cfg.min_carry:
                self._grip_seen = True

            if pose == Pose.OPEN_PALM and self._grip_seen and in_pose >= cfg.drop_hold:
                self._enter_cooldown(t, need_reset=True)
                ev.append(Event("drop"))
            elif pose == Pose.THUMBS_UP and in_pose >= cfg.thumbs_hold:
                self._enter_cooldown(t)
                ev.append(Event("ai"))
            elif carried > cfg.hold_timeout:
                self.state = State.IDLE
                ev.append(Event("hold_timeout"))
            return ev

        return ev
