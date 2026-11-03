"""One Google OAuth login for the pipeline: Drive (drive.file) + Sheets (spreadsheets).

    python3 google_auth.py --auth      # once: prints the consent URL (open it in Windows Chrome), caches the token
    python3 google_auth.py --check     # token present / refreshable?

Files (~/.config/ai-job-tracker/, override with AI_JOB_TRACKER_CONF_DIR):
  google_client_secret.json   Desktop OAuth client (the old name drive_client_secret.json is accepted)
  google_token.json           cached user token (refreshed automatically; chmod 600)

The OAuth consent screen must be **In production** (personal use; the "unverified app" warning is fine): in Testing
status Google expires refresh tokens after 7 days and the 04:00 run would stop working.
Packages: sudo apt install python3-google-auth-oauthlib  (pulls google-auth + requests).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONF_DIR = Path(os.environ.get("AI_JOB_TRACKER_CONF_DIR") or Path.home() / ".config" / "ai-job-tracker")
CLIENT_SECRETS = [CONF_DIR / "google_client_secret.json", CONF_DIR / "drive_client_secret.json"]
TOKEN = CONF_DIR / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/calendar.readonly"]


class AuthError(RuntimeError):
    """Missing / expired / revoked Google login. The message is user-facing (繁體中文)."""


def _libs():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request, AuthorizedSession
        from google.auth.exceptions import RefreshError
    except ImportError:
        raise AuthError("缺少 Google 套件:sudo apt install python3-google-auth-oauthlib")
    return Credentials, Request, AuthorizedSession, RefreshError


def _to_json(creds):
    """Authorized-user JSON. Ubuntu's python3-google-auth (1.5.x) has no Credentials.to_json()."""
    if hasattr(creds, "to_json"):
        return creds.to_json()
    expiry = getattr(creds, "expiry", None)
    return json.dumps({
        "token": creds.token, "refresh_token": creds.refresh_token, "token_uri": creds.token_uri,
        "client_id": creds.client_id, "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES), "expiry": expiry.isoformat() + "Z" if expiry else None,
    })


def credentials(interactive=False):
    Credentials, Request, _, RefreshError = _libs()
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.valid and set(SCOPES) <= set(creds.scopes or SCOPES):
        return creds
    if creds and creds.refresh_token and not interactive:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AuthError(f"Google 授權已過期或被撤銷({e}):請執行 google_auth.py --auth 重新登入;"
                            "若每 7 天就過期,把 OAuth consent screen 改成 In production")
    elif interactive:
        from google_auth_oauthlib.flow import InstalledAppFlow
        secret = next((p for p in CLIENT_SECRETS if p.exists()), None)
        if not secret:
            raise AuthError(f"找不到 OAuth client:{CLIENT_SECRETS[0]}(見 tools/pipeline/README.md)")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        # local-server flow: WSL forwards localhost, so the redirect from Windows Chrome reaches this process
        creds = flow.run_local_server(port=0, open_browser=False, access_type="offline", prompt="consent",
                                      authorization_prompt_message="請在 Windows 的瀏覽器開啟這個網址並允許:\n{url}\n")
    else:
        raise AuthError("Google 尚未授權:先手動執行 python3 .claude/skills/daily-job-tracker/scripts/google_auth.py --auth")
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(_to_json(creds), encoding="utf-8")
    os.chmod(TOKEN, 0o600)
    return creds


class Http:
    """JSON REST calls with a Bearer token; retries 429 / 5xx with exponential backoff.
    token=None → the cached OAuth credentials (refreshed on demand)."""

    def __init__(self, token=None, retries=6, sleep=time.sleep):
        self.static = token
        self.creds = None
        self.retries, self.sleep = retries, sleep

    def token(self):
        if self.static:
            return self.static
        if self.creds is None or not self.creds.valid:
            self.creds = credentials()
        return self.creds.token

    def request(self, method, url, body=None, headers=None, raw=False):
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
        hdrs = {"Authorization": f"Bearer {self.token()}", **({"Content-Type": "application/json"} if data is not None and not isinstance(body, bytes) else {}),
                **(headers or {})}
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    out = r.read()
                return out if raw else (json.loads(out) if out else {})
            except urllib.error.HTTPError as e:
                text = e.read()[:500]
                if e.code in (429, 500, 502, 503) and attempt < self.retries:
                    self.sleep(min(60, 2 ** attempt))
                    continue
                if e.code in (401, 403) and b"invalid_grant" in text.lower() + b"":
                    raise AuthError("Google 授權已失效:請執行 google_auth.py --auth")
                raise RuntimeError(f"Google API {method} {url.split('?')[0]} → {e.code}: {text!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auth", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    try:
        if a.auth:
            credentials(interactive=True)
            print(f"已授權(Drive + Sheets),token 存在 {TOKEN}")
        elif a.check:
            credentials()
            print("Google 授權有效")
        else:
            ap.print_help()
    except AuthError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
