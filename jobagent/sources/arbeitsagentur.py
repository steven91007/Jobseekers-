"""Bundesagentur für Arbeit Jobsuche (Germany's federal job board). Keyword search, no signup.

The endpoint is the one the jobsuche web app uses. Its client id is public
("jobboerse-jobsuche") and it rejects non-browser user agents with 403.
Many German employers post here and nowhere else.
"""

import base64
import threading
import time

import requests

from ..models import Job
from ..normalize import is_relevant_title
from . import BROWSER_UA, RateLimitedError, SourceResult, error_outcome

SEARCH = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
DETAIL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/{ref}"
PAGE_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}"
HEADERS = {"X-API-Key": "jobboerse-jobsuche", "Accept": "application/json", "User-Agent": BROWSER_UA}
PAGE_SIZE = 50
# Large result pages take this API a while to build, and it drops parallel
# connections under load: callers should run queries one at a time.
# Its TLS handshake sometimes stalls for a while under load, hence the long connect timeout.
TIMEOUT = (30, 60)
BACKOFF = (5.0, 15.0, 30.0)  # waits before the 2nd, 3rd and 4th attempt
_local = threading.local()
DAYS = {"24h": 1, "7d": 7}


def _session() -> requests.Session:
    """One keep-alive session per thread; reconnecting per request is what times out."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers.update(HEADERS)
    return _local.session


def _get(url: str, params: dict | None = None) -> dict:
    last: Exception | None = None
    for attempt in range(len(BACKOFF) + 1):
        if attempt:
            time.sleep(BACKOFF[attempt - 1])
        try:
            resp = _session().get(url, params=params, timeout=TIMEOUT)
            if resp.status_code == 429:
                last = RateLimitedError(f"HTTP 429 from {url}")
                continue
            if resp.status_code >= 500:
                last = requests.HTTPError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            _local.__dict__.pop("session", None)  # a broken keep-alive connection: start fresh
    raise last or RuntimeError("request failed")


def _location(offer: dict) -> str:
    for loc in offer.get("stellenlokationen") or []:
        addr = loc.get("adresse") or {}
        if addr.get("ort"):
            return f"{addr['ort']}, Germany"
    return "Germany"


def collect(query: str, *, since: str = "7d", max_results: int = 200) -> SourceResult:
    """One keyword search over jobs published within `since`. Every hit is in Germany."""
    result = SourceResult(source="arbeitsagentur", label=f"DE / {query}")
    offers: list[dict] = []
    page = 1
    try:
        while len(offers) < max_results:
            data = _get(SEARCH, {"was": query, "veroeffentlichtseit": DAYS.get(since, 7),
                                 "angebotsart": 1, "size": PAGE_SIZE, "page": page})
            batch = data.get("ergebnisliste") or []
            offers += batch
            if len(batch) < PAGE_SIZE or len(offers) >= int(data.get("maxErgebnisse") or 0):
                break
            page += 1
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    result.raw_count = len(offers)
    for o in offers[:max_results]:
        title = (o.get("stellenangebotsTitel") or "").strip()
        ref = o.get("referenznummer") or ""
        if not ref or not is_relevant_title(title, "other"):
            continue
        result.jobs.append(Job(
            source="arbeitsagentur", source_id=ref, title=title, company=o.get("firma") or "",
            location=_location(o), region="DE", url=PAGE_URL.format(ref=ref),
            posted_at=o.get("datumErsteVeroeffentlichung") or "",
            work_type="hybrid" if o.get("homeofficemoeglich") else "",
            extra={"query": query, **({"external_url": o["externeURL"]} if o.get("externeURL") else {})},
        ))
    if len(offers) >= max_results:
        result.detail = f"hit cap of {max_results}"
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result


def fill_descriptions(jobs: list[Job]) -> int:
    """Fetch descriptions one by one (the search result has none). Returns how many failed.

    Needed before the English-only filter: a title like "AI Engineer (m/w/d)" says
    nothing about the language of the posting, and most postings here are German.
    """
    failed = 0
    for job in jobs:
        if job.description:
            continue
        try:
            job.description = fetch_description(job)
        except Exception:
            failed += 1
    return failed


def fetch_description(job: Job) -> str:
    ref = base64.b64encode(job.source_id.encode()).decode()
    data = _get(DETAIL.format(ref=ref))
    return (data.get("stellenangebotsBeschreibung") or "").strip()
