"""Arbeitnow free job board API (arbeitnow.com/api/job-board-api). No key, Germany-heavy.

It aggregates postings from Greenhouse, SmartRecruiters, JOIN, Recruitee and other
ATSs, so it reaches companies that are on no watchlist. Pages hold ~300 jobs each,
newest first apart from a few pinned ads; paging stops once a whole page is older
than the cutoff.
"""

import html
import time
from datetime import datetime, timezone

from ..models import Job
from ..normalize import classify_region, is_relevant_title
from . import RateLimitedError, SourceResult, error_outcome, html_to_text, http_get_json

API = "https://www.arbeitnow.com/api/job-board-api"
MAX_PAGES = 40
# The API answers 429 after ~10 quick requests; a short pause between pages avoids it.
PAGE_PAUSE = 2.0
RATE_LIMIT_WAIT = 20.0


def _page(page: int, pause: float) -> dict:
    try:
        return http_get_json(API, {"page": page})
    except RateLimitedError:
        time.sleep(RATE_LIMIT_WAIT if pause else 0)
        return http_get_json(API, {"page": page})


def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


def _text(raw: str) -> str:
    """Some postings arrive with their HTML escaped twice (&lt;p&gt;); unescape until it is real HTML."""
    for _ in range(2):
        if "&lt;" not in raw:
            break
        raw = html.unescape(raw)
    return html_to_text(raw)


def collect(*, cutoff_iso: str = "", regions: list[str] | None = None, max_pages: int = MAX_PAGES,
            pause: float = PAGE_PAUSE) -> SourceResult:
    """All relevant jobs created at or after `cutoff_iso` (an ISO date or timestamp)."""
    result = SourceResult(source="arbeitnow", label="arbeitnow")
    seen: set[str] = set()
    try:
        for page in range(1, max_pages + 1):
            if page > 1 and pause:
                time.sleep(pause)
            data = _page(page, pause)
            batch = data.get("data") or []
            result.raw_count += len(batch)
            fresh = 0
            for j in batch:
                posted = _iso(j.get("created_at"))
                if cutoff_iso and posted[:len(cutoff_iso)] < cutoff_iso:
                    continue
                fresh += 1
                slug, title = j.get("slug") or "", (j.get("title") or "").strip()
                if not slug or slug in seen or not is_relevant_title(title, "other"):
                    continue
                region = classify_region(j.get("location") or "")
                if not region or (regions and region not in regions and region != "REMOTE_EU"):
                    continue
                seen.add(slug)
                result.jobs.append(Job(
                    source="arbeitnow", source_id=slug, title=title, company=j.get("company_name") or "",
                    location=j.get("location") or "", region=region, url=j.get("url") or "",
                    posted_at=posted, work_type="remote" if j.get("remote") else "",
                    description=_text(j.get("description") or ""),
                    extra={"tags": j.get("tags") or [], "job_types": j.get("job_types") or []},
                ))
            if not batch or fresh == 0 or not (data.get("links") or {}).get("next"):
                break
        else:
            result.detail = f"stopped after {max_pages} pages"
    except Exception as e:
        if not result.jobs:
            result.outcome, result.detail = error_outcome(e), str(e)
            return result
        # Keep what the earlier pages gave; the window may be only partly covered.
        result.detail = f"partial: {error_outcome(e)} after {result.raw_count} postings"
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result
