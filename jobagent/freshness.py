"""How recent a posting is: age in hours, a 0-100 freshness score, and window checks.

posted_at comes in two precisions. ATS boards give full timestamps. LinkedIn cards give a
date plus relative text ("5 hours ago"); `from_relative` turns minutes/hours into a
timestamp, so most jobs from a 24h search are precise to the hour. Anything else stays
date-only, which is treated as posted at noon UTC for scoring and as "that whole day" for
window checks (a date-only posting is never wrongly dropped from the window).
"""

import re
from datetime import datetime, timedelta, timezone

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELATIVE = re.compile(r"(\d+)\s*(minute|min|hour|hr|day|week|month)s?\s+ago", re.I)
_JUST_NOW = re.compile(r"\b(just now|moments? ago|seconds? ago)\b", re.I)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse(posted_at: str) -> tuple[datetime, bool] | None:
    """(datetime in UTC, is_precise), or None when there is no usable date."""
    text = (posted_at or "").strip()
    if not text:
        return None
    if DATE_ONLY.match(text):
        try:
            day = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return day + timedelta(hours=12), False
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc), True


def age_hours(posted_at: str, now: datetime | None = None) -> float | None:
    parsed = parse(posted_at)
    if parsed is None:
        return None
    return max(0.0, ((now or now_utc()) - parsed[0]).total_seconds() / 3600)


def within_hours(posted_at: str, hours: float, now: datetime | None = None) -> bool:
    """Posted within the last `hours`? Unknown dates are never within a window."""
    parsed = parse(posted_at)
    if parsed is None:
        return False
    now = now or now_utc()
    dt, precise = parsed
    if precise:
        return (now - dt).total_seconds() <= hours * 3600
    return dt.date() >= (now - timedelta(hours=hours)).date()


def score(posted_at: str, half_life_hours: float = 24.0, now: datetime | None = None) -> int | None:
    """0-100, halving every `half_life_hours`: with 24h, 0h=100, 6h=84, 12h=71, 24h=50, 3d=12."""
    age = age_hours(posted_at, now)
    if age is None:
        return None
    return round(100 * 0.5 ** (age / max(half_life_hours, 0.1)))


def from_relative(text: str, now: datetime | None = None) -> str:
    """ISO timestamp from "5 hours ago" / "30 minutes ago"; '' for day-level or unknown text."""
    now = now or now_utc()
    if _JUST_NOW.search(text or ""):
        return now.isoformat(timespec="seconds")
    m = _RELATIVE.search(text or "")
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit in ("minute", "min"):
        delta = timedelta(minutes=n)
    elif unit in ("hour", "hr"):
        delta = timedelta(hours=n)
    else:
        return ""  # "1 day ago" is 24-47h; the date attribute is as good as it gets
    return (now - delta).isoformat(timespec="seconds")


def label(posted_at: str, now: datetime | None = None) -> str:
    """Short human label: '3h ago', '2d ago', or the bare date when only the day is known."""
    parsed = parse(posted_at)
    if parsed is None:
        return "?"
    dt, precise = parsed
    if not precise:
        return dt.date().isoformat()
    hours = max(0.0, ((now or now_utc()) - dt).total_seconds() / 3600)
    if hours < 1:
        return "<1h ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"
