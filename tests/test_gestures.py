"""Gesture engine tests on synthetic hands (no camera needed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pidi.gestures import (EngineConfig, GestureEngine, Pose, State,
                           classify, pick_main_hand)

FPS = 30
DT = 1 / FPS

MCP = {5: (0.42, 0.62), 9: (0.50, 0.60), 13: (0.57, 0.61), 17: (0.63, 0.64)}


def make_hand(fingers=(True, True, True, True), thumb="out",
              pinch_at=None, dx=0.0, dy=0.0, scale=1.0):
    """Build 21 landmarks. fingers = (index, middle, ring, pinky) extended?"""
    lm = [None] * 21
    lm[0] = (0.50, 0.80)
    for i, base in enumerate((5, 9, 13, 17)):
        mx, my = MCP[base]
        lm[base] = (mx, my)
        if fingers[i]:
            lm[base + 1] = (mx, my - 0.07)
            lm[base + 2] = (mx, my - 0.12)
            lm[base + 3] = (mx, my - 0.16)
        else:
            lm[base + 1] = (mx, my - 0.05)
            lm[base + 2] = (mx, my - 0.02)
            lm[base + 3] = (mx, my + 0.03)
    if thumb == "out":
        lm[1], lm[2], lm[3], lm[4] = (0.42, 0.76), (0.36, 0.70), (0.31, 0.65), (0.27, 0.60)
    elif thumb == "in":
        lm[1], lm[2], lm[3], lm[4] = (0.44, 0.76), (0.42, 0.72), (0.44, 0.69), (0.45, 0.66)
    elif thumb == "up":
        lm[1], lm[2], lm[3], lm[4] = (0.43, 0.74), (0.41, 0.60), (0.40, 0.52), (0.40, 0.45)
    if pinch_at is not None:  # index bends to meet thumb
        px, py = pinch_at
        lm[6], lm[7], lm[8] = (0.40, 0.54), (0.38, 0.50), (px, py)
        lm[3], lm[4] = (px - 0.02, py + 0.04), (px + 0.01, py + 0.015)
    # translate / scale around wrist
    wx, wy = lm[0]
    return [((x - wx) * scale + wx + dx, (y - wy) * scale + wy + dy, 0.0) for x, y in lm]


PALM = make_hand()
FIST = make_hand((False, False, False, False), thumb="in")
THUMBS = make_hand((False, False, False, False), thumb="up")
PINCH = make_hand(pinch_at=(0.36, 0.48))
POINT = make_hand((True, False, False, False), thumb="in")


def feed(engine, hand, seconds, t, **kw):
    events = []
    n = max(1, round(seconds * FPS))
    for _ in range(n):
        lm = hand(**kw) if callable(hand) else hand
        f = classify(lm) if lm is not None else None
        events += engine.update(f, t)
        t += DT
    return events, t


def kinds(evs):
    return [e.kind for e in evs]


# ---------------------------------------------------------------- classifier
def test_classify_poses():
    assert classify(PALM).pose == Pose.OPEN_PALM
    assert classify(FIST).pose == Pose.FIST
    assert classify(THUMBS).pose == Pose.THUMBS_UP
    assert classify(PINCH).pose == Pose.PINCH
    assert classify(POINT).pose == Pose.POINT


def test_classifier_is_scale_and_position_invariant():
    for scale in (0.5, 1.0, 1.4):
        for dx in (-0.2, 0.0, 0.2):
            assert classify(make_hand(dx=dx, scale=scale)).pose == Pose.OPEN_PALM
            assert classify(make_hand((False,) * 4, thumb="in", dx=dx, scale=scale)).pose == Pose.FIST


def test_fist_is_not_pinch():
    # thumb tip touches curled index in a fist — must not read as pinch
    assert classify(FIST).pose != Pose.PINCH


def test_pointer_is_mirrored():
    f = classify(PALM)
    assert abs(f.pointer[0] - (1 - PALM[8][0])) < 1e-9


def test_pick_main_hand_prefers_closest():
    small = make_hand(scale=0.5)
    big = make_hand(scale=1.3)
    assert pick_main_hand([small, big]) is big
    assert pick_main_hand([]) is None


# ---------------------------------------------------------------- engine
def test_full_grab_and_drop_cycle():
    e, t = GestureEngine(), 0.0
    ev, t = feed(e, PALM, 0.6, t)
    assert "armed" in kinds(ev) and e.state == State.ARMED
    ev, t = feed(e, FIST, 0.3, t)
    assert kinds(ev) == ["grab"] and e.state == State.HOLDING
    ev, t = feed(e, FIST, 1.0, t)          # carry the grab
    ev, t = feed(e, PALM, 0.4, t)          # open palm -> drop
    assert kinds(ev) == ["drop"]


def test_fist_without_arming_does_nothing():
    e, t = GestureEngine(), 0.0
    ev, t = feed(e, FIST, 2.0, t)
    assert ev == [] and e.state == State.IDLE


def test_brief_palm_flash_does_not_arm():
    e, t = GestureEngine(), 0.0
    ev, t = feed(e, PALM, 0.2, t)          # waved past the camera
    ev2, t = feed(e, FIST, 0.5, t)
    assert "grab" not in kinds(ev + ev2)


def test_slow_fist_after_palm_disarms():
    e, t = GestureEngine(), 0.0
    _, t = feed(e, PALM, 0.6, t)
    ev, t = feed(e, None, 1.5, t)          # hand leaves
    assert "disarmed" in kinds(ev)
    ev, t = feed(e, FIST, 0.5, t)
    assert "grab" not in kinds(ev)


def test_relaxing_right_after_grab_does_not_paste():
    e, t = GestureEngine(), 0.0
    _, t = feed(e, PALM, 0.6, t)
    _, t = feed(e, FIST, 0.25, t)
    ev, t = feed(e, PALM, 0.5, t)          # immediately opens hand
    assert "drop" not in kinds(ev)
    assert e.state == State.HOLDING
    # user switches app with mouse (hand gone), then regrips and releases
    _, t = feed(e, None, 2.0, t)
    _, t = feed(e, FIST, 0.4, t)
    ev, t = feed(e, PALM, 0.4, t)
    assert kinds(ev) == ["drop"]


def test_no_rearm_while_palm_stays_open_after_drop():
    e, t = GestureEngine(), 0.0
    _, t = feed(e, PALM, 0.6, t)
    _, t = feed(e, FIST, 1.2, t)
    _, t = feed(e, PALM, 0.3, t)           # drop
    ev, t = feed(e, PALM, 3.0, t)          # palm still open
    assert "armed" not in kinds(ev)
    _, t = feed(e, None, 0.5, t)           # hand goes away
    ev, t = feed(e, PALM, 0.6, t)          # presented again
    assert "armed" in kinds(ev)


def test_region_select():
    e, t = GestureEngine(), 0.0
    _, t = feed(e, PALM, 0.6, t)
    ev, t = feed(e, make_hand(pinch_at=(0.36, 0.48)), 0.3, t)
    assert "region_start" in kinds(ev)
    # drag the whole hand to the left (mirrored -> right on screen) and down
    for i in range(15):
        f = make_hand(pinch_at=(0.36, 0.48), dx=-0.02 * i, dy=0.01 * i)
        ev, t = feed(e, f, DT, t)
    ev, t = feed(e, PALM, 0.3, t)          # release pinch
    grabs = [x for x in ev if x.kind == "region_grab"]
    assert grabs, kinds(ev)
    x0, y0, x1, y1 = grabs[0].data["rect"]
    assert x1 - x0 > 0.1 and y1 - y0 > 0.05
    assert e.state == State.HOLDING


def test_tiny_region_cancels():
    e, t = GestureEngine(), 0.0
    _, t = feed(e, PALM, 0.6, t)
    _, t = feed(e, PINCH, 0.3, t)
    ev, t = feed(e, PALM, 0.3, t)
    assert "region_cancel" in kinds(ev)


def test_throw_to_phone():
    e, t = GestureEngine(), 0.0
    _, t = feed(e, PALM, 0.6, t)
    _, t = feed(e, FIST, 0.3, t)
    out = []
    for i in range(8):                     # fast swipe, ~0.27 s
        f = make_hand((False,) * 4, thumb="in", dx=-0.05 * i)
        evs, t = feed(e, f, DT, t)
        out += evs
    assert "throw" in kinds(out)
    assert [x for x in out if x.kind == "throw"][0].data["direction"] == "right"


def test_slow_fist_drift_is_not_a_throw():
    e, t = GestureEngine(), 0.0
    _, t = feed(e, PALM, 0.6, t)
    _, t = feed(e, FIST, 0.3, t)
    out = []
    for i in range(90):                    # same distance over 3 s
        f = make_hand((False,) * 4, thumb="in", dx=-0.004 * i)
        evs, t = feed(e, f, DT, t)
        out += evs
    assert "throw" not in kinds(out)


def test_thumbs_up_opens_ai():
    e, t = GestureEngine(), 0.0
    _, t = feed(e, PALM, 0.6, t)
    _, t = feed(e, FIST, 0.3, t)
    ev, t = feed(e, THUMBS, 0.6, t)
    assert "ai" in kinds(ev)


def test_far_away_hand_ignored():
    e, t = GestureEngine(), 0.0
    far_palm = make_hand(scale=0.25)       # someone behind you
    ev, t = feed(e, far_palm, 2.0, t)
    assert ev == []


def test_hold_timeout():
    e = GestureEngine(EngineConfig(hold_timeout=2.0))
    t = 0.0
    _, t = feed(e, PALM, 0.6, t)
    _, t = feed(e, FIST, 0.3, t)
    ev, t = feed(e, None, 2.5, t)
    assert "hold_timeout" in kinds(ev)


def test_phone_injection_then_drop():
    e, t = GestureEngine(), 0.0
    e.inject_holding(t)
    _, t = feed(e, FIST, 1.0, t)
    ev, t = feed(e, PALM, 0.3, t)
    assert kinds(ev) == ["drop"]


def test_noisy_frames_are_smoothed():
    """A single misclassified frame inside a palm hold must not break arming."""
    e, t = GestureEngine(), 0.0
    seq = [PALM] * 6 + [POINT] + [PALM] * 10
    out = []
    for h in seq:
        out += e.update(classify(h), t)
        t += DT
    assert "armed" in kinds(out)


def test_config_from_dict():
    cfg = EngineConfig.from_dict({"arm_time": "0.5", "bogus": 1})
    assert cfg.arm_time == 0.5
