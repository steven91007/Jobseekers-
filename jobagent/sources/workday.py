"""Workday career sites ({tenant}.wdN.myworkdayjobs.com), via the JSON API their pages use.

Workday has no "list everything" feed that stays small, so we search with a few
AI keywords, filter titles first, and only open detail pages when the list's
location text is too vague ("3 Locations") to place the job.
"""

import re
from datetime import date, timedelta
from urllib.parse import urlparse

from ..companies import Company
from ..models import Job
from ..normalize import classify_any, is_relevant_title, to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_json, http_post_json

AI_SEARCHES = ["machine learning", "artificial intelligence", "AI engineer", "LLM",
               "generative AI", "data scientist", "MLOps"]
SOFTWARE_SEARCHES = ["software engineer", "backend engineer"]
PAGE = 20
MAX_PER_SEARCH = 100
MAX_DETAIL_FETCHES = 40
_POSTED = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)
_MULTI = re.compile(r"^\d+\s+locations?$", re.I)


def parse_board(url: str) -> tuple[str, str, str]:
    """(host, tenant, site) from e.g. https://acme.wd3.myworkdayjobs.com/en-US/Careers."""
    u = urlparse(url)
    parts = [p for p in u.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
    if not u.netloc.endswith("myworkdayjobs.com") or not parts:
        raise ValueError(f"not a Workday board URL: {url}")
    return u.netloc, u.netloc.split(".")[0], parts[0]


def posted_from_text(text: str, today: date | None = None) -> str:
    """'Posted 3 Days Ago' -> ISO date. '30+ Days Ago' becomes 30 days ago (a lower bound)."""
    today = today or date.today()
    m = _POSTED.search(text or "")
    if not m:
        return ""
    word = m.group(1).lower()
    days = 0 if word == "today" else 1 if word == "yesterday" else int(m.group(2))
    return (today - timedelta(days=days)).isoformat()


def _api(host: str, tenant: str, site: str) -> str:
    return f"https://{host}/wday/cxs/{tenant}/{site}"


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="workday", label=company.slug)
    try:
        host, tenant, site = parse_board(company.url)
    except ValueError as e:
        result.outcome, result.detail = "ERROR", str(e)
        return result
    api = _api(host, tenant, site)
    searches = AI_SEARCHES + (SOFTWARE_SEARCHES if company.tier == "ai_native" else [])

    postings: dict[str, dict] = {}
    try:
        for term in searches:
            for offset in range(0, MAX_PER_SEARCH, PAGE):
                page = http_post_json(f"{api}/jobs", {"limit": PAGE, "offset": offset,
                                                      "searchText": term, "appliedFacets": {}})
                batch = page.get("jobPostings") or []
                for p in batch:
                    postings.setdefault(p.get("externalPath", ""), p)
                if len(batch) < PAGE or offset + PAGE >= (page.get("total") or 0):
                    break
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    result.raw_count = len(postings)
    details = 0
    for path, p in postings.items():
        title = (p.get("title") or "").strip()
        if not path or not is_relevant_title(title, company.tier):
            continue
        loc_text = p.get("locationsText") or ""
        posted = posted_from_text(p.get("postedOn") or "")
        description, url, locations = "", f"https://{host}/{site}{path}", [loc_text]
        region = classify_any(locations)
        if region is None and _MULTI.match(loc_text) and details < MAX_DETAIL_FETCHES:
            details += 1
            try:
                info = http_get_json(f"{api}{path}").get("jobPostingInfo") or {}
            except Exception:
                info = {}
            locations = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
            region = classify_any(locations)
            posted = to_iso(info.get("startDate")) or posted
            description = html_to_text(info.get("jobDescription") or "")
            url = info.get("externalUrl") or url
        if region is None or (cutoff and posted and posted < cutoff):
            continue
        result.jobs.append(Job(
            source="workday", source_id=f"{tenant}-{path.rsplit('/', 1)[-1]}", title=title,
            company=company.name, location=" / ".join(x for x in locations if x), region=region,
            url=url, posted_at=posted, work_type=p.get("remoteType") or "", description=description,
            company_tier=company.tier, ats_slug=company.slug,
            extra={"api": api, "path": path},
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result


def fetch_description(job: Job) -> str:
    info = http_get_json(f"{job.extra['api']}{job.extra['path']}").get("jobPostingInfo") or {}
    if info.get("startDate"):
        job.posted_at = to_iso(info["startDate"])
    return html_to_text(info.get("jobDescription") or "")
