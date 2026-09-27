"""Teamtailor career sites, via the public RSS feed at <careers site>/jobs.rss."""

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..companies import Company
from ..models import Job
from ..normalize import to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_text, region_if_relevant

TT = "{https://teamtailor.com/locations}"


def _posted(text: str) -> str:
    try:
        return to_iso(parsedate_to_datetime(text).isoformat())
    except (TypeError, ValueError):
        return ""


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="teamtailor", label=company.slug)
    base = (company.url or f"https://{company.slug}.teamtailor.com").rstrip("/")
    try:
        body, _ = http_get_text(f"{base}/jobs.rss", browser=False)
        root = ET.fromstring(body.encode("utf-8"))
    except ET.ParseError as e:
        result.outcome, result.detail = "PARSE_DRIFT", f"not a Teamtailor RSS feed: {e}"
        return result
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    items = root.findall("channel/item")
    result.raw_count = len(items)
    for item in items:
        title = (item.findtext("title") or "").strip()
        locs = []
        for loc in item.findall(f"{TT}locations/{TT}location"):
            city, country, name = (loc.findtext(f"{TT}{k}") or "" for k in ("city", "country", "name"))
            locs.append(", ".join(x for x in (city or name, country) if x))
        remote = (item.findtext("remoteStatus") or "").lower()
        posted = _posted(item.findtext("pubDate") or "")
        region = region_if_relevant(company, title, locs, posted, cutoff)
        if not region:
            continue
        result.jobs.append(Job(
            source="teamtailor", source_id=f"{company.slug}-{item.findtext('guid') or item.findtext('link')}",
            title=title, company=company.name, location=" / ".join(locs), region=region,
            url=item.findtext("link") or "", posted_at=posted,
            work_type=remote if remote not in ("", "none") else "",
            description=html_to_text(item.findtext("description") or ""),
            company_tier=company.tier, ats_slug=company.slug,
        ))
    result.outcome = "OK" if result.jobs else "EMPTY"
    return result
