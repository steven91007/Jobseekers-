"""Greenhouse public job-board API (boards-api.greenhouse.io)."""

import html

from ..companies import Company
from ..models import Job
from ..normalize import classify_region, is_relevant_title, to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_json

LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="greenhouse", label=company.slug)
    try:
        data = http_get_json(LIST_URL.format(slug=company.slug))
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    postings = data.get("jobs", [])
    result.raw_count = len(postings)
    for p in postings:
        location = (p.get("location") or {}).get("name", "")
        region = classify_region(location)
        posted = to_iso(p.get("first_published") or p.get("updated_at"))
        if not region or (cutoff and posted[:10] < cutoff):
            continue
        title = (p.get("title") or "").strip()
        if not is_relevant_title(title, company.tier):
            continue
        result.jobs.append(Job(
            source="greenhouse",
            source_id=str(p["id"]),
            title=title,
            company=company.name,
            location=location,
            region=region,
            url=p.get("absolute_url", ""),
            posted_at=posted,
            company_tier=company.tier,
            ats_slug=company.slug,
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result


def fetch_description(job: Job) -> str:
    try:
        data = http_get_json(DETAIL_URL.format(slug=job.ats_slug, job_id=job.source_id))
    except Exception:
        return ""
    return html_to_text(html.unescape(data.get("content") or ""))
