"""Workable public widget API (apply.workable.com/api/v1/widget/accounts/{slug}). No key needed."""

import re

from ..companies import Company
from ..models import Job
from ..normalize import to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_json, region_if_relevant

API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"
# Titles like "... - EMEA Remote" often carry a placeholder office (e.g. Paris) as the location.
_REMOTE_TITLE = re.compile(r"\b(emea|europe|eu)\b.*\bremote\b|\bremote\b.*\b(emea|europe|eu)\b", re.I)


def _locations(j: dict) -> list[str]:
    locs = [", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)]
    for loc in j.get("locations") or []:
        locs.append(", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x))
    if _REMOTE_TITLE.search(j.get("title") or ""):
        locs.append("Remote - Europe")
    elif j.get("telecommuting") and j.get("country"):
        locs.append(f"Remote, {j['country']}")
    return [loc for loc in dict.fromkeys(locs) if loc]


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="workable", label=company.slug)
    try:
        data = http_get_json(API.format(slug=company.slug), {"details": "true"})
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    jobs = data.get("jobs") or []
    result.raw_count = len(jobs)
    for j in jobs:
        title = (j.get("title") or "").strip()
        locs = _locations(j)
        posted = to_iso(j.get("published_on") or j.get("created_at"))
        region = region_if_relevant(company, title, locs, posted, cutoff)
        if not region:
            continue
        result.jobs.append(Job(
            source="workable", source_id=f"{company.slug}-{j.get('shortcode')}", title=title,
            company=company.name, location=" / ".join(locs), region=region,
            url=j.get("url") or j.get("shortlink") or "", posted_at=posted,
            work_type="remote" if j.get("telecommuting") else "",
            description=html_to_text(j.get("description") or ""),
            company_tier=company.tier, ats_slug=company.slug,
            extra={"seniority": j.get("experience") or ""},
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result
