"""AI actions on a grab: any OpenAI-compatible vision endpoint (Groq by default)."""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request

from PIL import Image

TASKS = {
    "Extract text": "Extract all text in this screenshot exactly as it appears, keeping line "
                    "breaks and structure. Output only the text, nothing else.",
    "Explain": "Explain what is shown in this screenshot clearly and briefly. If it is an "
               "error message or code, explain the cause and how to fix it.",
    "Summarize": "Summarize the content of this screenshot in 3-6 short bullet points.",
    "Translate → Tamil": "Read all text in this screenshot and translate it into natural Tamil. "
                         "Output only the translation.",
    "Translate → English": "Read all text in this screenshot and translate it into clear English. "
                           "Output only the translation.",
    "Table → CSV": "If this screenshot contains a table, convert it to CSV. Output only CSV.",
}


class AIError(RuntimeError):
    pass


class AIClient:
    def __init__(self, cfg: dict):
        self.base_url = cfg.get("base_url", "https://api.groq.com/openai/v1").rstrip("/")
        self.model = cfg.get("model", "meta-llama/llama-4-scout-17b-16e-instruct")
        self.key_env = cfg.get("api_key_env", "GROQ_API_KEY")
        self.timeout = float(cfg.get("timeout", 60))

    @property
    def ready(self) -> bool:
        return bool(os.environ.get(self.key_env))

    @staticmethod
    def _encode(img: Image.Image, max_side=1600) -> str:
        im = img.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode()

    def ask(self, img: Image.Image, task: str, extra: str = "") -> str:
        key = os.environ.get(self.key_env)
        if not key:
            raise AIError(f"Set the {self.key_env} environment variable to use AI actions.")
        prompt = TASKS.get(task, task) + (f"\n\n{extra}" if extra else "")
        body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + self._encode(img)}},
            ]}],
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise AIError(f"API error {e.code}: {e.read().decode(errors='ignore')[:300]}")
        except urllib.error.URLError as e:
            raise AIError(f"Network error: {e.reason}")
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError):
            raise AIError(f"Unexpected response: {str(data)[:300]}")
