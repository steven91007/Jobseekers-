"""Ashby public posting API (api.ashbyhq.com/posting-api)."""

from ..companies import Company
from ..models import Job
from ..normalize import classify_any, is_relevant_title, to_iso
from . import SourceResult, http_get_json

LIST_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _locations(p: dict) -> list[str]:
    locs = [p.get("location") or ""]
    locs += [s.get("location", "") for s in (p.get("secondaryLocations") or [])]
    addr = ((p.get("address") or {}).get("postalAddress") or {})
    if addr:
        locs.append(f"{addr.get('addressLocality', '')}, {addr.get('addressCountry', '')}")
    return [loc for loc in locs if loc and loc.strip(", ")]


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="ashby", label=company.slug)
    try:
        data = http_get_json(LIST_URL.format(slug=company.slug), {"includeCompensation": "true"})
    except Exception as e:
        result.outcome, result.detail = "ERROR", str(e)
        return result

    postings = [p for p in data.get("jobs", []) if p.get("isListed", True)]
    result.raw_count = len(postings)
    for p in postings:
        locs = _locations(p)
        region = classify_any(locs)
        posted = to_iso(p.get("publishedAt"))
        if not region or (cutoff and posted[:10] < cutoff):
            continue
        title = (p.get("title") or "").strip()
        if not is_relevant_title(title, company.tier):
            continue
        comp = (p.get("compensation") or {}).get("compensationTierSummary") or ""
        result.jobs.append(Job(
            source="ashby",
            source_id=p["id"],
            title=title,
            company=company.name,
            location=" / ".join(dict.fromkeys(locs)),
            region=region,
            url=p.get("jobUrl", ""),
            posted_at=posted,
            work_type=p.get("workplaceType") or "",
            description=p.get("descriptionPlain") or "",
            company_tier=company.tier,
            ats_slug=company.slug,
            extra={"salary": comp} if comp else {},
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result
