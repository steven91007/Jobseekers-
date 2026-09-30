"""OAuth for Gmail and Sheets: one Desktop-app client, one cached user token.

``authorize()`` opens a browser once and saves the token (with its refresh token) to
``Settings.token_file``; ``credentials()`` loads and refreshes it without a browser,
so the CLI and the MCP server can run unattended afterwards.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import SCOPES, Settings


class AuthError(RuntimeError):
    pass


SETUP_HINT = (
    "Create a Desktop-app OAuth client in Google Cloud (Gmail API + Google Sheets API enabled), "
    "save its JSON as {client}, then run: python -m jobtracker auth"
)


def _save(creds, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(tmp, 0o600)  # holds a refresh token
    tmp.replace(path)


def authorize(settings: Settings, *, open_browser: bool = True):
    """Run the browser consent flow and save the token. Returns the credentials."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not settings.client_secret_file.exists():
        raise AuthError(f"no OAuth client file at {settings.client_secret_file}. "
                        + SETUP_HINT.format(client=settings.client_secret_file))
    flow = InstalledAppFlow.from_client_secrets_file(str(settings.client_secret_file), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=open_browser, access_type="offline",
                                  prompt="consent")
    _save(creds, settings.token_file)
    return creds


def credentials(settings: Settings):
    """Saved credentials, refreshed if expired. Raises AuthError when a consent is needed."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = settings.token_file
    if not path.exists():
        raise AuthError(f"not signed in to Google (no token at {path}); run: python -m jobtracker auth")
    granted = set(json.loads(path.read_text(encoding="utf-8")).get("scopes") or [])
    missing = set(SCOPES) - granted
    if missing:
        raise AuthError(f"the saved Google token lacks {sorted(missing)}; run: python -m jobtracker auth")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise AuthError("the saved Google token cannot be refreshed; run: python -m jobtracker auth")
    try:
        creds.refresh(Request())
    except RefreshError as e:
        # Tokens of apps left in "Testing" expire after 7 days; revoked ones fail here too.
        raise AuthError(f"Google refused the saved token ({e}); run: python -m jobtracker auth") from e
    _save(creds, path)
    return creds


def build_service(settings: Settings, api: str, version: str):
    from googleapiclient.discovery import build

    return build(api, version, credentials=credentials(settings), cache_discovery=False)
