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
    """transient = network / timeout / 408 / 429 / 5xx after retries: the caller opens its circuit breaker."""

    def __init__(self, msg: str, transient: bool = False):
        super().__init__(msg)
        self.transient = transient


RETRY = (408, 429, 500, 502, 503, 504, 529)


def api_key() -> str | None:
    k = os.environ.get("TYPESAFE_API_KEY")
    if k:
        return k.strip()
    if KEY_FILE.exists():
        if KEY_FILE.stat().st_mode & 0o077:
            print(f"Jev: key file readable by others — run chmod 600 {KEY_FILE}", file=sys.stderr)
        return KEY_FILE.read_text(encoding="utf-8").strip() or None
    return None


class Client:
    def __init__(self, key: str, debug: bool = False, retries: int = 2, timeout: int = 15, sleep=time.sleep):
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
                if e.code in RETRY and attempt < self.retries:
                    try:
                        wait = float(e.headers.get("Retry-After") or 0)
                    except ValueError:
                        wait = 0
                    self.sleep(min(10, wait or 2 ** attempt))   # honour Retry-After, capped at 10 s
                    continue
                raise JevError(f"HTTP {e.code}: {text}", transient=e.code in RETRY) from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < self.retries:
                    self.sleep(min(10, 2 ** attempt))
                    continue
                raise JevError(f"network: {e}", transient=True) from None
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
    """Recorded / fake responses for tests: STUB, not a result. `responder(payload)` → answers, or raises JevError."""

    def __init__(self, responder):
        super().__init__("stub")
        self.responder = responder
        self.payloads = []

    def system_one(self, payload: dict) -> dict:
        self.calls += 1
        self.payloads.append(payload)
        out = {"model": "STUB", "answers": self.responder(payload), "usage": {"input_tokens": 100, "output_tokens": 0},
               "_latency_s": 0.0}
        self.input_tokens += 100
        if not out["answers"]:
            raise JevError("stub: no recorded answer")
        return out
