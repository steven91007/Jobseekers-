"""Lever public postings API (api.lever.co/v0/postings)."""

from ..companies import Company
from ..models import Job
from ..normalize import classify_any, is_relevant_title, to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_json

LIST_URL = "https://api.lever.co/v0/postings/{slug}"


def _description(p: dict) -> str:
    parts = [p.get("descriptionPlain") or ""]
    for section in p.get("lists") or []:
        parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content', ''))}")
    parts.append(p.get("additionalPlain") or "")
    return "\n\n".join(x.strip() for x in parts if x and x.strip())


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="lever", label=company.slug)
    try:
        postings = http_get_json(LIST_URL.format(slug=company.slug), {"mode": "json"})
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    result.raw_count = len(postings)
    for p in postings:
        cats = p.get("categories") or {}
        locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        if p.get("country"):
            locs.append(f"{cats.get('location') or ''}, {p['country']}")
        region = classify_any([loc for loc in locs if loc])
        posted = to_iso(p.get("createdAt"))
        if not region or (cutoff and posted[:10] < cutoff):
            continue
        title = (p.get("text") or "").strip()
        if not is_relevant_title(title, company.tier):
            continue
        result.jobs.append(Job(
            source="lever",
            source_id=p["id"],
            title=title,
            company=company.name,
            location=" / ".join(dict.fromkeys(loc for loc in locs if loc)),
            region=region,
            url=p.get("hostedUrl", ""),
            posted_at=posted,
            work_type=p.get("workplaceType") or "",
            description=_description(p),
            company_tier=company.tier,
            ats_slug=company.slug,
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result


