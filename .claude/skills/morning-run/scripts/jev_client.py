"""Minimal TypeSafe System One client (stdlib urllib). POST https://api.typesafe.ai/v1/systemone.

Key: env TYPESAFE_API_KEY, else ~/.config/ai-job-tracker/typesafe_key (chmod 600). Never logged, never in the repo.
--debug logs the exact request payload to stderr (the key is never part of the payload; the header is shown redacted).
Errors are raised as JevError, never turned into an answer. 429 / 529 / 5xx are retried with exponential backoff.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.typesafe.ai/v1/systemone"
KEY_FILE = Path.home() / ".config" / "ai-job-tracker" / "typesafe_key"


class JevError(RuntimeError):
    pass


def api_key() -> str | None:
    k = os.environ.get("TYPESAFE_API_KEY")
    if k:
        return k.strip()
    if KEY_FILE.exists():
        if KEY_FILE.stat().st_mode & 0o077:
            print(f"Jev: {KEY_FILE} is readable by others — chmod 600 it", file=sys.stderr)
        return KEY_FILE.read_text(encoding="utf-8").strip() or None
    return None


class Client:
    def __init__(self, key: str, debug: bool = False, retries: int = 4, timeout: int = 60, sleep=time.sleep):
        self._key, self.debug, self.retries, self.timeout, self.sleep = key, debug, retries, timeout, sleep
        self.calls = self.input_tokens = self.output_tokens = 0
        self.seconds = 0.0

    def system_one(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.debug:
            print("Jev request (Authorization: Bearer ***):\n" + json.dumps(payload, ensure_ascii=False, indent=1), file=sys.stderr)
        hdrs = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        t0 = time.monotonic()
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(API, data=body, method="POST", headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    out = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                text = e.read()[:300].decode("utf-8", "replace")
                if e.code in (429, 500, 502, 503, 529) and attempt < self.retries:
                    self.sleep(min(30, 2 ** attempt))
                    continue
                raise JevError(f"HTTP {e.code}: {text}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < self.retries:
                    self.sleep(min(30, 2 ** attempt))
                    continue
                raise JevError(f"network: {e}") from None
        dt_ = time.monotonic() - t0
        self.calls += 1
        self.seconds += dt_
        u = out.get("usage") or {}
        self.input_tokens += int(u.get("input_tokens") or 0)
        self.output_tokens += int(u.get("output_tokens") or 0)
        out["_latency_s"] = round(dt_, 3)
        if self.debug:
            print("Jev response:\n" + json.dumps(out, ensure_ascii=False, indent=1), file=sys.stderr)
        if not out.get("answers"):
            raise JevError("response without answers")
        return out


class StubClient(Client):
    """Recorded / fake responses for tests: STUB, not a result. `responses`: {subject_or_text_prefix: answers}."""

    def __init__(self, responder):
        super().__init__("stub")
        self.responder = responder

    def system_one(self, payload: dict) -> dict:
        self.calls += 1
        out = {"model": "STUB", "answers": self.responder(payload), "usage": {"input_tokens": 0, "output_tokens": 0},
               "_latency_s": 0.0}
        if not out["answers"]:
            raise JevError("stub: no recorded answer")
        return out
