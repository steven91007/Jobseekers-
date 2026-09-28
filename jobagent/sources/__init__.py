"""Job sources. Each returns a SourceResult and never raises for network trouble."""

from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from ..models import Job

HTTP_TIMEOUT = (5, 20)
USER_AGENT = "jobagent/1.0 (personal job search)"


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
    last: Exception | None = None
    for _ in range(2):
        try:
            resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
            if resp.status_code in (429, 500, 502, 503, 504):
                last = requests.HTTPError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
    raise last or RuntimeError("request failed")


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
    from . import greenhouse, linkedin

    if job.source == "linkedin":
        job.description = linkedin.fetch_description(job)
    elif job.source == "greenhouse":
        job.description = greenhouse.fetch_description(job)
    return job.description


def collect_company(company, cutoff: str) -> SourceResult:
    """Dispatch a watchlist company to its ATS collector."""
    from . import ashby, greenhouse, lever

    collector = {"greenhouse": greenhouse.collect, "ashby": ashby.collect, "lever": lever.collect}
    return collector[company.ats](company, cutoff)
