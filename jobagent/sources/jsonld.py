"""Company careers pages that publish schema.org JobPosting data (JSON-LD).

Google for Jobs requires this markup, so many careers sites embed it on each
job page. We read the listing page; if it carries no JobPosting data itself we
follow links that look like relevant job pages and parse those. Pages rendered
only by JavaScript expose neither, and come back EMPTY with an explanation.
"""

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..companies import Company
from ..models import Job
from ..normalize import AI_TERMS, ENGINEER_TERMS, SOFTWARE_TERMS, to_iso
from . import SourceResult, error_outcome, html_to_text, http_get_text, region_if_relevant

MAX_JOB_PAGES = 40
_JOB_PATH = re.compile(r"/(jobs?|careers?|positions?|openings?|vacanc(y|ies)|vacatures?|stellen|"
                       r"stellenangebote|o|p|role|roles|opportunit(y|ies))/[^?#]+", re.I)
COUNTRY_CODES = {"DE": "Germany", "DEU": "Germany", "NL": "Netherlands", "NLD": "Netherlands",
                 "IE": "Ireland", "IRL": "Ireland"}


def _load(text: str):
    for candidate in (text, html.unescape(text)):
        try:
            return json.loads(candidate, strict=False)
        except ValueError:
            continue
    return None


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "JobPosting" in types:
            yield node
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                yield from _walk(node[key])


def extract_postings(page_html: str) -> list[dict]:
    soup = BeautifulSoup(page_html, "html.parser")
    out = []
    for tag in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        data = _load(tag.string or tag.get_text() or "")
        if data is not None:
            out.extend(_walk(data))
    return out


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("@value") or value.get("value") or "")
    return str(value or "")


def posting_locations(p: dict) -> list[str]:
    locs = []
    places = p.get("jobLocation") or []
    for place in places if isinstance(places, list) else [places]:
        addr = (place or {}).get("address") or {} if isinstance(place, dict) else {}
        if isinstance(addr, str):
            locs.append(addr)
            continue
        country = _text(addr.get("addressCountry"))
        country = COUNTRY_CODES.get(country.upper(), country)
        locs.append(", ".join(x for x in (_text(addr.get("addressLocality")), country) if x))
    if str(p.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        reqs = p.get("applicantLocationRequirements") or []
        for r in reqs if isinstance(reqs, list) else [reqs]:
            name = _text(r)
            locs.append(f"Remote, {COUNTRY_CODES.get(name.upper(), name)}")
    return [loc for loc in locs if loc]


def _salary(p: dict) -> str:
    base = p.get("baseSalary") or {}
    if not isinstance(base, dict):
        return ""
    val = base.get("value") or {}
    if isinstance(val, dict) and (val.get("minValue") or val.get("maxValue") or val.get("value")):
        lo, hi = val.get("minValue") or val.get("value"), val.get("maxValue") or ""
        return f"{lo}-{hi} {base.get('currency') or ''} {val.get('unitText') or ''}".strip(" -")
    return ""


def posting_to_job(p: dict, company: Company, page_url: str, cutoff: str) -> Job | None:
    title = html.unescape(_text(p.get("title"))).strip()
    locs = posting_locations(p)
    posted = to_iso(p.get("datePosted"))
    region = region_if_relevant(company, title, locs, posted, cutoff)
    if not region:
        return None
    url = _text(p.get("url")) or page_url
    ident = _text(p.get("identifier")) or hashlib.sha1(url.encode()).hexdigest()[:12]
    salary = _salary(p)
    return Job(
        source="jsonld", source_id=f"{company.slug}-{ident}", title=title, company=company.name,
        location=" / ".join(dict.fromkeys(locs)), region=region, url=url, posted_at=posted,
        work_type="remote" if str(p.get("jobLocationType", "")).upper() == "TELECOMMUTE" else "",
        description=html_to_text(html.unescape(_text(p.get("description")))),
        company_tier=company.tier, ats_slug=company.slug, extra={"salary": salary} if salary else {},
    )


def _job_links(page_html: str, base_url: str) -> list[str]:
    """Same-site links that look like job pages and whose text looks like a relevant title."""
    soup = BeautifulSoup(page_html, "html.parser")
    site = urlparse(base_url).netloc.split(":")[0].removeprefix("www.")
    links = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"]).split("#")[0]
        host = urlparse(url).netloc.removeprefix("www.")
        if not (host == site or host.endswith("." + site) or site.endswith("." + host)):
            continue
        text = " ".join(a.get_text(" ").split())
        if not _JOB_PATH.search(urlparse(url).path):
            continue
        if not (AI_TERMS.search(text) or SOFTWARE_TERMS.search(text) or ENGINEER_TERMS.search(text)):
            continue
        links.append(url)
    return list(dict.fromkeys(links))[:MAX_JOB_PAGES]


def collect(company: Company, cutoff: str) -> SourceResult:
    result = SourceResult(source="jsonld", label=company.slug)
    try:
        page, final_url = http_get_text(company.url)
    except Exception as e:
        result.outcome, result.detail = error_outcome(e), str(e)
        return result

    pairs = [(p, final_url) for p in extract_postings(page)]
    links: list[str] = []
    if not pairs:
        links = _job_links(page, final_url)

        def fetch(url):
            try:
                body, final = http_get_text(url)
                return [(p, final) for p in extract_postings(body)]
            except Exception:
                return []

        with ThreadPoolExecutor(4) as pool:
            for found in pool.map(fetch, links):
                pairs += found

    result.raw_count = len(pairs)
    seen = set()
    for p, url in pairs:
        job = posting_to_job(p, company, url, cutoff)
        if job and job.job_key not in seen:
            seen.add(job.job_key)
            result.jobs.append(job)
    if not pairs:
        result.outcome = "EMPTY"
        result.detail = ("no JobPosting data on the page or on "
                         f"{len(links)} linked job pages (the site may render jobs with JavaScript)")
    else:
        result.outcome = "OK" if result.jobs else "EMPTY"
    return result
