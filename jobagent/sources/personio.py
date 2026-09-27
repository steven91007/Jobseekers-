"""Personio public XML feed ({slug}.jobs.personio.de/xml). Popular with German startups."""

import xml.etree.ElementTree as ET

from ..companies import Company
from ..models import Job
from ..normalize import to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_text, region_if_relevant


def _host(company: Company) -> str:
    return company.url.rstrip("/") if company.url else f"https://{company.slug}.jobs.personio.de"


def _description(pos: ET.Element) -> str:
    parts = []
    for jd in pos.findall("jobDescriptions/jobDescription"):
        name, value = jd.findtext("name") or "", jd.findtext("value") or ""
        parts.append(f"{name}\n{html_to_text(value)}".strip())
    return "\n\n".join(p for p in parts if p)


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="personio", label=company.slug)
    host = _host(company)
    try:
        body, _ = http_get_text(f"{host}/xml", {"language": "en"}, browser=False)
        root = ET.fromstring(body.encode("utf-8"))
    except ET.ParseError as e:
        result.outcome, result.detail = "PARSE_DRIFT", f"not a Personio XML feed: {e}"
        return result
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    positions = root.findall("position")
    result.raw_count = len(positions)
    for pos in positions:
        title = (pos.findtext("name") or "").strip()
        offices = [pos.findtext("office") or ""] + [o.text or "" for o in pos.findall("additionalOffices/office")]
        posted = to_iso(pos.findtext("createdAt"))
        region = region_if_relevant(company, title, offices, posted, cutoff)
        if not region:
            continue
        job_id = pos.findtext("id") or ""
        result.jobs.append(Job(
            source="personio", source_id=f"{company.slug}-{job_id}", title=title, company=company.name,
            location=" / ".join(o for o in offices if o), region=region,
            url=f"{host}/job/{job_id}", posted_at=posted,
            work_type=pos.findtext("schedule") or "", description=_description(pos),
            company_tier=company.tier, ats_slug=company.slug,
            extra={"seniority": pos.findtext("seniority") or ""},
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result
