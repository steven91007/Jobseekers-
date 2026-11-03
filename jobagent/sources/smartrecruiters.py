"""SmartRecruiters public postings API (api.smartrecruiters.com/v1/companies/{id}/postings)."""

from ..companies import Company
from ..models import Job
from ..normalize import to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_json, region_if_relevant

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"
PAGE_SIZE = 100
MAX_POSTINGS = 2000
COUNTRY_NAMES = {"de": "Germany", "nl": "Netherlands", "ie": "Ireland"}


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="smartrecruiters", label=company.slug)
    postings: list[dict] = []
    try:
        # Ask the API for the three countries only; it filters server-side.
        for country in ("de", "nl", "ie"):
            offset = 0
            while offset < MAX_POSTINGS:
                page = http_get_json(LIST_URL.format(slug=company.slug),
                                     {"limit": PAGE_SIZE, "offset": offset, "country": country})
                content = page.get("content", [])
                postings += content
                offset += len(content)
                if not content or offset >= page.get("totalFound", 0):
                    break
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    result.raw_count = len(postings)
    for p in postings:
        loc = p.get("location") or {}
        country = COUNTRY_NAMES.get((loc.get("country") or "").lower(), loc.get("country") or "")
        locs = [loc.get("fullLocation") or "", ", ".join(x for x in (loc.get("city"), country) if x)]
        if loc.get("remote") and country:
            locs.append(f"Remote, {country}")
        title = (p.get("name") or "").strip()
        posted = to_iso(p.get("releasedDate"))
        region = region_if_relevant(company, title, locs, posted, cutoff)
        if not region:
            continue
        result.jobs.append(Job(
            source="smartrecruiters", source_id=f"{company.slug}-{p['id']}", title=title, company=company.name,
            location=locs[1] or locs[0], region=region,
            url=f"https://jobs.smartrecruiters.com/{company.slug}/{p['id']}",
            posted_at=posted,
            work_type="remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else "",
            company_tier=company.tier, ats_slug=company.slug, extra={"posting_id": p["id"]},
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result


def fetch_description(job: Job) -> str:
    data = http_get_json(DETAIL_URL.format(slug=job.ats_slug, job_id=job.extra.get("posting_id")))
    if data.get("postingUrl"):
        job.url = data["postingUrl"]
    sections = (data.get("jobAd") or {}).get("sections") or {}
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
        sec = sections.get(key) or {}
        if sec.get("text"):
            parts.append(f"{sec.get('title') or key}\n{html_to_text(sec['text'])}")
    return "\n\n".join(parts)
