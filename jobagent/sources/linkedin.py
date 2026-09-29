"""LinkedIn guest search, via the existing strict scraper in linkedin_scraper.py."""

import re

import linkedin_scraper
from linkedin_scraper import ScraperError

from ..companies import tier_for_company_name
from ..config import REGIONS, REMOTE_EU
from ..freshness import from_relative
from ..models import Job
from ..normalize import classify_region, is_relevant_title, to_iso
from . import SourceResult

_EU_WIDE = re.compile(r"\b(european union|europe|emea)\b", re.I)
_OTHER_IRISH = re.compile(r"\b(cork|galway|limerick|waterford|belfast)\b", re.I)


def _region_for(location: str, searched: str) -> str | None:
    region = classify_region(location)
    if region:
        return region
    if _EU_WIDE.search(location or ""):
        return REMOTE_EU
    if _OTHER_IRISH.search(location or ""):
        return None
    return searched  # trust LinkedIn's own geo filter for vague strings


def collect(
    region_code: str,
    query: str,
    *,
    since: str = "7d",
    max_results: int = 25,
    deadline: float = 90.0,
) -> SourceResult:
    region = REGIONS[region_code]
    result = SourceResult(source="linkedin", label=f"{region_code} / {query}")
    try:
        scrape = linkedin_scraper.search_jobs_strict(
            keyword=query,
            location=region.linkedin_location,
            max_results=max_results,
            posted_within=since,
            timeout=deadline,
        )
    except ScraperError as e:
        result.outcome, result.detail = e.outcome.value, str(e)
        return result

    result.outcome, result.detail = scrape.outcome.value, scrape.detail
    result.raw_count = len(scrape.jobs)
    if len(scrape.jobs) >= max_results:
        # LinkedIn had at least this many; the window may hold more than we listed.
        result.detail = (result.detail + "; " if result.detail else "") + f"hit cap of {max_results}"
    for raw in scrape.jobs:
        job_region = _region_for(raw.get("location", ""), region_code)
        if job_region is None:
            continue
        tier = tier_for_company_name(raw["company"])
        if not is_relevant_title(raw["title"], tier):
            continue
        result.jobs.append(Job(
            source="linkedin",
            source_id=raw["job_id"],
            title=raw["title"],
            company=raw["company"],
            location=raw.get("location", ""),
            region=job_region,
            url=raw["url"],
            posted_at=from_relative(raw.get("posted_text", "")) or to_iso(raw.get("posted_date")),
            work_type=raw.get("work_type", "") if raw.get("work_type") != "N/A" else "",
            company_tier=tier,
            extra={"query": query},
        ))
    return result


def fetch_description(job: Job) -> str:
    detail = linkedin_scraper.get_job_detail(job.source_id)
    if detail.get("error"):
        return ""
    criteria = detail.get("criteria") or {}
    if criteria:
        job.extra["criteria"] = criteria
    head = "\n".join(f"{k}: {v}" for k, v in criteria.items())
    return (head + "\n\n" + detail.get("description", "")).strip()
