"""Recruitee public offers API ({slug}.recruitee.com/api/offers/). Common in the Netherlands."""

from ..companies import Company
from ..models import Job
from ..normalize import to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_json, region_if_relevant


def _locations(o: dict) -> list[str]:
    locs = [o.get("location") or ""]
    for loc in o.get("locations") or []:
        locs.append(", ".join(x for x in (loc.get("city"), loc.get("country")) if x))
    if o.get("remote") and o.get("country"):
        locs.append(f"Remote, {o['country']}")
    return [loc for loc in locs if loc]


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="recruitee", label=company.slug)
    try:
        data = http_get_json(f"https://{company.slug}.recruitee.com/api/offers/")
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    offers = [o for o in data.get("offers", []) if o.get("status", "published") == "published"]
    result.raw_count = len(offers)
    for o in offers:
        title = (o.get("title") or "").strip()
        locs = _locations(o)
        posted = to_iso((o.get("published_at") or o.get("created_at") or "").replace(" UTC", "+00:00").replace(" ", "T", 1))
        region = region_if_relevant(company, title, locs, posted, cutoff)
        if not region:
            continue
        salary = o.get("salary") or {}
        salary_text = ""
        if isinstance(salary, dict) and (salary.get("min") or salary.get("max")):
            salary_text = f"{salary.get('min') or ''}-{salary.get('max') or ''} {salary.get('currency') or ''} {salary.get('period') or ''}".strip()
        desc = "\n\n".join(x for x in (html_to_text(o.get("description") or ""),
                                       html_to_text(o.get("requirements") or "")) if x)
        work = "remote" if o.get("remote") else "hybrid" if o.get("hybrid") else "onsite" if o.get("on_site") else ""
        result.jobs.append(Job(
            source="recruitee", source_id=f"{company.slug}-{o.get('id')}", title=title, company=company.name,
            location=" / ".join(dict.fromkeys(locs)), region=region,
            url=o.get("careers_url") or "", posted_at=posted, work_type=work, description=desc,
            company_tier=company.tier, ats_slug=company.slug,
            extra={"salary": salary_text} if salary_text else {},
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result
