"""Settings stored in ~/.pidi/settings.json (merged over defaults)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .gestures import EngineConfig

SETTINGS_PATH = Path.home() / ".pidi" / "settings.json"

DEFAULTS = {
    "camera_index": 0,
    "camera_backend": "auto",         # "auto" | "msmf" | "dshow" | "any"
    "camera_width": 640,
    "camera_height": 360,
    "grab_target": "screen",          # "screen" | "window"
    "save_history": True,
    "history_dir": str(Path.home() / "Pictures" / "PIDI"),
    "history_keep": 300,              # max saved grabs
    "sounds": True,
    "hud": False,                     # floating camera HUD (great for demo videos)
    "hud_corner": "top-right",        # top-left | top-right | bottom-left | bottom-right
    "phone_enabled": True,
    "phone_port": 8765,
    "start_paused": False,
    "ai": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "api_key_env": "GROQ_API_KEY",
    },
    "engine": EngineConfig().to_dict(),
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    data = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    s = _merge(DEFAULTS, data)
    if not SETTINGS_PATH.exists():
        save(s)
    return s


def save(s: dict):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")
