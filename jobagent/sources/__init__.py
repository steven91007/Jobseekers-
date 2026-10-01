"""Job sources. Each returns a SourceResult and never raises for network trouble."""

from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from ..models import Job

HTTP_TIMEOUT = (5, 20)
USER_AGENT = "jobagent/1.0 (personal job search)"
# Some careers sites serve bots an empty shell; a browser UA gets the real HTML.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
RETRY_STATUSES = (500, 502, 503, 504)


class RateLimitedError(Exception):
    pass


def _request(method: str, url: str, *, params=None, json_body=None, headers=None, browser=False,
             timeout=HTTP_TIMEOUT):
    """HTTP with one retry on 5xx/connection errors. 429 raises RateLimitedError (no retry)."""
    last: Exception | None = None
    hdrs = {"User-Agent": BROWSER_UA if browser else USER_AGENT, **(headers or {})}
    for _ in range(2):
        try:
            resp = requests.request(method, url, params=params, json=json_body, headers=hdrs,
                                    timeout=timeout, allow_redirects=True)
            if resp.status_code == 429:
                raise RateLimitedError(f"HTTP 429 from {url}")
            if resp.status_code in RETRY_STATUSES:
                last = requests.HTTPError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
    raise last or RuntimeError("request failed")


def http_get_text(url: str, params: dict | None = None, *, browser: bool = True) -> tuple[str, str]:
    """(body, final_url) of a page."""
    resp = _request("GET", url, params=params, browser=browser)
    resp.encoding = resp.encoding or "utf-8"
    return resp.text, resp.url


def http_post_json(url: str, body: dict):
    return _request("POST", url, json_body=body,
                    headers={"Accept": "application/json", "Content-Type": "application/json"}).json()


def error_outcome(e: Exception) -> str:
    return "RATE_LIMITED" if isinstance(e, RateLimitedError) else "ERROR"


@dataclass
class SourceResult:
    source: str
    label: str                      # e.g. "DE / AI Engineer" or "anthropic"
    jobs: list[Job] = field(default_factory=list)
    raw_count: int = 0              # postings seen before region/title filters
    outcome: str = "OK"             # OK | EMPTY | BLOCKED | RATE_LIMITED | ERROR | PARSE_DRIFT ...
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in ("OK", "EMPTY", "EMPTY_OK")


def http_get_json(url: str, params: dict | None = None):
    """GET JSON with one retry on transient failures. Raises requests exceptions."""
    return _request("GET", url, params=params, headers={"Accept": "application/json"}).json()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["br", "p", "li", "h1", "h2", "h3", "h4", "div"]):
        tag.insert_after("\n")
    text = soup.get_text()
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out, blank = [], 0
    for line in lines:
        blank = blank + 1 if not line else 0
        if blank <= 1:
            out.append(line)
    return "\n".join(out).strip()


def fetch_description(job: Job) -> str:
    """Fill job.description if empty. Returns the text ('' when unavailable)."""
    if job.description:
        return job.description
    from . import arbeitsagentur, greenhouse, linkedin, smartrecruiters, workday

    fetchers = {
        "arbeitsagentur": arbeitsagentur.fetch_description,
        "linkedin": linkedin.fetch_description,
        "greenhouse": greenhouse.fetch_description,
        "smartrecruiters": smartrecruiters.fetch_description,
        "workday": workday.fetch_description,
    }
    fetcher = fetchers.get(job.source)
    if fetcher:
        try:
            job.description = fetcher(job)
        except Exception:
            job.description = ""
    return job.description


COLLECTOR_MODULES = ("greenhouse", "ashby", "lever", "personio", "recruitee",
                     "smartrecruiters", "workday", "teamtailor", "workable", "jsonld")


def collect_company(company, cutoff: str) -> SourceResult:
    """Dispatch a watchlist company to its board collector."""
    import importlib

    if company.ats not in COLLECTOR_MODULES:
        return SourceResult(company.ats, company.slug, outcome="ERROR", detail=f"unknown ats {company.ats}")
    module = importlib.import_module(f"{__name__}.{company.ats}")
    return module.collect(company, cutoff)


def region_if_relevant(company, title: str, locations: list[str], posted_iso: str, cutoff: str) -> str | None:
    """Shared filter for board collectors: in-scope region, recent enough, relevant title."""
    from ..normalize import classify_any, is_relevant_title

    region = classify_any([loc for loc in locations if loc])
    if not region:
        return None
    if cutoff and posted_iso and posted_iso[:10] < cutoff:
        return None
    if not is_relevant_title(title, company.tier):
        return None
    return region
