"""Read-only Gmail access: search messages and read one as plain text.

The Gmail API service is injected, so parsing is tested offline with a fake.
"""

from __future__ import annotations

import base64
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import getaddresses

MAX_SEARCH = 200
DEFAULT_BODY_CHARS = 8000
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n\s*\n\s*\n+")


@dataclass
class Email:
    id: str
    thread_id: str
    received_at: str  # ISO 8601, UTC
    sender: str
    sender_email: str
    to: str
    subject: str
    snippet: str
    labels: list[str] = field(default_factory=list)
    body: str = ""  # empty in search results; filled by GmailClient.get

    @property
    def sender_domain(self) -> str:
        return self.sender_email.rpartition("@")[2].lower()

    def to_dict(self) -> dict:
        return {**asdict(self), "sender_domain": self.sender_domain}


class GmailError(RuntimeError):
    pass


def _headers(payload: dict) -> dict[str, str]:
    return {h["name"].lower(): h.get("value", "") for h in payload.get("headers", [])}


def _decode(data: str, charset: str) -> str:
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _charset(part: dict) -> str:
    m = re.search(r'charset="?([^";\s]+)', _headers(part).get("content-type", ""), re.I)
    return m.group(1) if m else "utf-8"


def _walk(part: dict):
    yield part
    for child in part.get("parts", []) or []:
        yield from _walk(child)


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    return soup.get_text("\n")


def _tidy(text: str) -> str:
    lines = (_WS.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n"))
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def body_text(payload: dict) -> str:
    """The message's text/plain part, else its text/html part converted to text."""
    parts = [p for p in _walk(payload) if (p.get("body") or {}).get("data")
             and not (p.get("filename") or "")]
    for mime in ("text/plain", "text/html"):
        chunks = [_decode(p["body"]["data"], _charset(p)) for p in parts if p.get("mimeType") == mime]
        if chunks:
            text = "\n\n".join(chunks)
            return _tidy(_html_to_text(text) if mime == "text/html" else text)
    return ""


def parse_message(msg: dict, *, with_body: bool = False, max_chars: int = DEFAULT_BODY_CHARS) -> Email:
    payload = msg.get("payload") or {}
    h = _headers(payload)
    pairs = getaddresses([h.get("from", "")])
    name, addr = pairs[0] if pairs else ("", "")
    ms = int(msg.get("internalDate") or 0)
    body = ""
    if with_body:
        body = body_text(payload)
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n[... truncated]"
    return Email(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        received_at=datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds"),
        sender=name or addr,
        sender_email=addr.lower(),
        to=h.get("to", ""),
        subject=h.get("subject", ""),
        snippet=msg.get("snippet", ""),
        labels=list(msg.get("labelIds", [])),
        body=body,
    )


class GmailClient:
    def __init__(self, service):
        self._svc = service.users()

    @classmethod
    def from_settings(cls, settings) -> "GmailClient":
        from .google_auth import build_service

        return cls(build_service(settings, "gmail", "v1"))

    def profile(self) -> dict:
        """The signed-in account: emailAddress, messagesTotal, ..."""
        return self._svc.getProfile(userId="me").execute()

    def search(self, query: str, max_results: int = 20) -> list[Email]:
        """Newest-first messages matching a Gmail search query, without bodies."""
        want = max(1, min(max_results, MAX_SEARCH))
        ids: list[str] = []
        token = None
        while len(ids) < want:
            resp = self._svc.messages().list(userId="me", q=query, maxResults=min(100, want - len(ids)),
                                             pageToken=token).execute()
            ids += [m["id"] for m in resp.get("messages", [])]
            token = resp.get("nextPageToken")
            if not token:
                break
        return [self._fetch(i, with_body=False) for i in ids[:want]]

    def get(self, message_id: str, max_chars: int = DEFAULT_BODY_CHARS) -> Email:
        return self._fetch(message_id, with_body=True, max_chars=max_chars)

    def _fetch(self, message_id: str, *, with_body: bool, max_chars: int = DEFAULT_BODY_CHARS) -> Email:
        if with_body:
            msg = self._svc.messages().get(userId="me", id=message_id, format="full").execute()
        else:
            msg = self._svc.messages().get(userId="me", id=message_id, format="metadata",
                                           metadataHeaders=["From", "To", "Subject", "Date"]).execute()
        return parse_message(msg, with_body=with_body, max_chars=max_chars)
