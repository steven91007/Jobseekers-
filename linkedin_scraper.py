"""LinkedIn guest-API job scraper.

Two entry points, deliberately:

* :func:`search_jobs_strict` — used by the Discord bot. Classifies *why* a scrape
  came back empty (blocked / rate-limited / selector drift / genuinely no jobs)
  and raises on hard failures, so an unattended bot can tell "no new jobs today"
  apart from "LinkedIn changed their HTML three weeks ago".
* :func:`search_jobs` — used by the CLI. A thin forgiving wrapper: prints and
  returns whatever it managed to collect. A human is watching, so it stays dumb.

Both share one copy of the HTTP and parsing logic.
"""

import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

WORK_TYPE_MAP = {
    "onsite": "1",
    "remote": "2",
    "hybrid": "3",
}

JOB_TYPE_MAP = {
    "fulltime": "F",
    "parttime": "P",
    "contract": "C",
    "temporary": "T",
    "internship": "I",
    "volunteer": "V",
}

JOB_TYPE_LABEL = {
    "fulltime": "Full-time",
    "parttime": "Part-time",
    "contract": "Contract",
    "temporary": "Temporary",
    "internship": "Internship",
    "volunteer": "Volunteer",
}

# LinkedIn's "Date posted" filter (f_TPR), expressed as seconds since posting.
POSTED_WITHIN_MAP = {
    "24h": "r86400",
    "7d": "r604800",
    "30d": "r2592000",
}

JOB_VIEW_RE = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:[/?#]|$)")

# --- Reliability tuning ---------------------------------------------------

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15
MAX_ATTEMPTS = 3
RETRY_BACKOFF = (2.0, 6.0, 15.0)
PAGE_PAUSE = 1.0

# Retrying a block is what turns a soft rate-limit into a multi-hour IP ban,
# so these statuses are fatal on the first sighting and never retried.
BLOCK_STATUSES = frozenset({403, 999})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Substrings that mean LinkedIn served an auth/challenge page instead of results.
AUTHWALL_MARKERS = ("authwall", "checkpoint/challenge", "/uas/login")

# Below this, a 200 body is too small to be a real (even empty) result page.
SUSPICIOUS_BODY_BYTES = 200


class Outcome(str, Enum):
    """Why a scrape produced the number of jobs it did."""

    OK = "OK"
    EMPTY_OK = "EMPTY_OK"                    # real page, filters excluded everything
    EMPTY_SUSPICIOUS = "EMPTY_SUSPICIOUS"    # 200 but body too thin to trust
    PARSE_DRIFT = "PARSE_DRIFT"              # real HTML, selectors matched nothing
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"


class ScraperError(Exception):
    """Base for hard failures. Carries the Outcome it maps to."""

    outcome = Outcome.TRANSPORT_ERROR


class TransportError(ScraperError):
    outcome = Outcome.TRANSPORT_ERROR


class RateLimited(ScraperError):
    outcome = Outcome.RATE_LIMITED


class Blocked(ScraperError):
    outcome = Outcome.BLOCKED


@dataclass
class ScrapeResult:
    jobs: list[dict] = field(default_factory=list)
    outcome: Outcome = Outcome.OK
    raw_card_count: int = 0     # <li> elements seen
    parsed_count: int = 0       # cards that yielded a usable job_id
    pages_fetched: int = 0
    http_status: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in (Outcome.OK, Outcome.EMPTY_OK)


def _is_english(text: str) -> bool:
    if not text or text == "N/A":
        return True
    non_latin = sum(1 for c in text if ord(c) > 591)
    return (non_latin / len(text)) < 0.2


def _build_job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def _extract_job_id_from_href(href: str) -> str:
    if not href:
        return ""

    parsed = urlparse(href)
    query_job_id = parse_qs(parsed.query).get("currentJobId", [""])[0]
    if query_job_id.isdigit():
        return query_job_id

    match = JOB_VIEW_RE.search(parsed.path)
    if match:
        return match.group(1)

    return ""


def _extract_job_id(card) -> str:
    job_id_tag = card.find("div", {"data-entity-urn": True})
    if job_id_tag:
        urn = job_id_tag.get("data-entity-urn", "")
        match = re.search(r":(\d+)$", urn)
        if match:
            return match.group(1)

    link_tag = card.find("a", class_="base-card__full-link")
    if link_tag:
        return _extract_job_id_from_href(link_tag.get("href", ""))

    return ""


def _looks_blocked(body: str) -> bool:
    lowered = body[:4000].lower()
    return any(marker in lowered for marker in AUTHWALL_MARKERS)


def _remaining(deadline: float | None) -> float:
    return float("inf") if deadline is None else deadline - time.monotonic()


def _pause(seconds: float, deadline: float | None) -> bool:
    """Sleep up to ``seconds``, never past ``deadline``. False if out of budget."""
    remaining = _remaining(deadline)
    if remaining <= 0:
        return False
    time.sleep(min(seconds, remaining))
    return True


def _fetch(session, url, params, deadline):
    """GET with bounded retries. Raises ScraperError subclasses on give-up."""
    last_exc: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        if _remaining(deadline) <= 0:
            raise TransportError("deadline exceeded before request")

        try:
            resp = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except requests.RequestException as e:
            last_exc = e
        else:
            if resp.status_code in BLOCK_STATUSES:
                raise Blocked(f"HTTP {resp.status_code} (authwall/blocked)")
            if resp.status_code not in RETRY_STATUSES:
                resp.raise_for_status()
                if _looks_blocked(resp.text):
                    raise Blocked("HTTP 200 but body is an auth/challenge page")
                return resp
            last_exc = requests.HTTPError(f"HTTP {resp.status_code}")
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    if not _pause(float(retry_after), deadline):
                        raise RateLimited("429, deadline exceeded during Retry-After")

        if attempt < MAX_ATTEMPTS - 1:
            backoff = RETRY_BACKOFF[attempt] * random.uniform(0.7, 1.3)
            if not _pause(backoff, deadline):
                break

    if isinstance(last_exc, requests.HTTPError) and "429" in str(last_exc):
        raise RateLimited(str(last_exc)) from last_exc
    raise TransportError(str(last_exc) or "request failed") from last_exc


def _classify_page(body: str, raw_cards: int, parsed: int) -> Outcome:
    """Decide what an individual page's shape means."""
    stripped = body.strip()
    if len(stripped) < SUSPICIOUS_BODY_BYTES or raw_cards == 0:
        return Outcome.EMPTY_SUSPICIOUS
    if parsed == 0:
        # Real HTML with real cards, but not one usable job id came out of it.
        # That is a selector rename, not an empty result set.
        return Outcome.PARSE_DRIFT
    return Outcome.OK


def _parse_locations(location: str) -> list[str]:
    """Split a comma-separated location string into individual search terms.

    LinkedIn's guest search API only accepts one location per request, so
    "Berlin, Hamburg, Munich" is run as three separate searches and merged.
    """
    if not location.strip():
        return [""]
    parts = [part.strip() for part in location.split(",")]
    parts = [part for part in parts if part]
    return parts or [""]


def _search_one_location(
    session: requests.Session,
    keyword: str,
    location: str,
    work_type: str,
    job_type: str,
    english_only: bool,
    max_results: int,
    deadline: float | None,
    result: ScrapeResult,
    seen_ids: set,
    page_outcomes: list[Outcome],
    posted_within: str = "",
) -> None:
    """Page through one location's results, appending into the shared ``result``."""
    start = 0
    fetch_limit = max_results * 3 if english_only else max_results

    while len(result.jobs) < max_results and start < fetch_limit:
        if _remaining(deadline) <= 0:
            result.detail = "deadline exceeded; returning partial results"
            return

        params: dict = {
            "keywords": keyword,
            "location": location,
            "start": start,
            # Most recent first, rather than LinkedIn's default relevance sort.
            "sortBy": "DD",
        }
        if work_type in WORK_TYPE_MAP:
            params["f_WT"] = WORK_TYPE_MAP[work_type]
        if job_type in JOB_TYPE_MAP:
            params["f_JT"] = JOB_TYPE_MAP[job_type]
        if posted_within in POSTED_WITHIN_MAP:
            params["f_TPR"] = POSTED_WITHIN_MAP[posted_within]

        resp = _fetch(session, SEARCH_URL, params, deadline)
        result.http_status = resp.status_code
        result.pages_fetched += 1

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("li")
        result.raw_card_count += len(cards)

        page_parsed = 0
        for card in cards:
            job_id = _extract_job_id(card)
            if not job_id.isdigit():
                continue
            page_parsed += 1

            if len(result.jobs) >= max_results or job_id in seen_ids:
                continue

            title_tag = card.find("h3", class_="base-search-card__title")
            company_tag = card.find("h4", class_="base-search-card__subtitle")
            location_tag = card.find("span", class_="job-search-card__location")
            date_tag = card.find("time")
            wtype_tag = card.find("span", class_="job-search-card__workplace-type")

            title = title_tag.get_text(strip=True) if title_tag else "N/A"
            company = company_tag.get_text(strip=True) if company_tag else "N/A"

            if english_only and not (_is_english(title) and _is_english(company)):
                continue

            seen_ids.add(job_id)
            result.jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location_tag.get_text(strip=True) if location_tag else "N/A",
                "work_type": wtype_tag.get_text(strip=True) if wtype_tag else "N/A",
                "posted_date": date_tag.get("datetime", "N/A") if date_tag else "N/A",
                "url": _build_job_url(job_id),
            })

        result.parsed_count += page_parsed
        page_outcomes.append(_classify_page(resp.text, len(cards), page_parsed))

        if not cards:
            return

        start += len(cards)
        if start >= 1000:
            return
        if not _pause(PAGE_PAUSE, deadline):
            result.detail = "deadline exceeded between pages"
            return


def search_jobs_strict(
    keyword: str,
    location: str = "",
    max_results: int = 10,
    work_type: str = "",
    job_type: str = "",
    english_only: bool = False,
    timeout: float | None = None,
    session: requests.Session | None = None,
    posted_within: str = "",
) -> ScrapeResult:
    """Search LinkedIn, reporting *why* the result set is the size it is.

    ``location`` may be a comma-separated list (e.g. "Berlin, Hamburg, Munich");
    each is searched separately and the results merged, newest first, deduped
    by job id, capped at ``max_results`` total.

    ``posted_within`` is one of ``POSTED_WITHIN_MAP`` ("24h", "7d", "30d"); empty
    means no date filter, which is what the CLI and the bot use.

    ``timeout`` is a whole-operation budget in seconds, enforced between pages
    and during backoff. It exists because ``asyncio.wait_for`` around a thread
    cannot actually cancel the thread — the deadline has to be honoured in here.

    Raises :class:`Blocked`, :class:`RateLimited` or :class:`TransportError`.
    Selector drift and thin bodies come back as an ``outcome``, not an exception.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    owns_session = session is None
    session = session or requests.Session()

    result = ScrapeResult()
    seen_ids: set = set()
    page_outcomes: list[Outcome] = []

    try:
        for loc in _parse_locations(location):
            if len(result.jobs) >= max_results:
                break
            if _remaining(deadline) <= 0:
                result.detail = "deadline exceeded; returning partial results"
                break

            _search_one_location(
                session=session,
                keyword=keyword,
                location=loc,
                work_type=work_type,
                job_type=job_type,
                english_only=english_only,
                max_results=max_results,
                deadline=deadline,
                result=result,
                seen_ids=seen_ids,
                page_outcomes=page_outcomes,
                posted_within=posted_within,
            )
    finally:
        if owns_session:
            session.close()

    result.jobs.sort(key=lambda job: job.get("posted_date") or "", reverse=True)
    result.outcome = _overall_outcome(result, page_outcomes)
    return result


def _overall_outcome(result: ScrapeResult, page_outcomes: list[Outcome]) -> Outcome:
    if result.jobs:
        return Outcome.OK
    if Outcome.PARSE_DRIFT in page_outcomes:
        return Outcome.PARSE_DRIFT
    if result.parsed_count > 0:
        # Cards parsed fine, the filters just excluded everything.
        return Outcome.EMPTY_OK
    if not page_outcomes:
        return Outcome.EMPTY_SUSPICIOUS
    return page_outcomes[0]


def search_jobs(
    keyword: str,
    location: str = "",
    max_results: int = 10,
    work_type: str = "",
    job_type: str = "",
    english_only: bool = False,
) -> list[dict]:
    """Forgiving wrapper kept for the CLI: prints on failure, returns what it got."""
    try:
        return search_jobs_strict(
            keyword=keyword,
            location=location,
            max_results=max_results,
            work_type=work_type,
            job_type=job_type,
            english_only=english_only,
        ).jobs
    except ScraperError as e:
        print(f"LinkedIn search failed: {e}")
        return []


def get_job_detail(job_id: str) -> dict:
    if not job_id.isdigit():
        return {"error": f"Invalid LinkedIn job id: {job_id}"}

    url = JOB_DETAIL_URL.format(job_id=job_id)
    session = requests.Session()
    try:
        resp = _fetch(session, url, None, time.monotonic() + 60)
    except ScraperError as e:
        return {"error": str(e)}
    finally:
        session.close()

    soup = BeautifulSoup(resp.text, "html.parser")

    description_tag = soup.find("div", class_="show-more-less-html__markup")
    if not description_tag:
        description_tag = soup.find("div", class_="description__text")

    criteria: dict = {}
    for item in soup.find_all("li", class_="description__job-criteria-item"):
        label = item.find("h3")
        value = item.find("span")
        if label and value:
            criteria[label.get_text(strip=True)] = value.get_text(strip=True)

    description_text = ""
    if description_tag:
        for tag in description_tag.find_all(["br", "p", "li"]):
            tag.replace_with("\n" + tag.get_text() + "\n")
        description_text = description_tag.get_text(separator="", strip=False).strip()
        description_text = re.sub(r"\n{3,}", "\n\n", description_text)

    return {
        "description": description_text or "No description found.",
        "criteria": criteria,
    }
