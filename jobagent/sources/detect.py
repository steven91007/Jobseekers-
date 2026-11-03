"""Work out which job board a company uses, from its careers page URL.

Looks for embedded or linked job-board URLs (Greenhouse, Ashby, Lever,
Personio, Recruitee, SmartRecruiters, Workday, Teamtailor) in the page and its
final URL, then for schema.org JobPosting data. Every candidate is verified by
running its collector, so a suggestion is only returned if the board answers.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..companies import Company
from . import SourceResult, collect_company, http_get_text
from .jsonld import _job_links, extract_postings

PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_-]+)", re.I)),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)", re.I)),
    ("recruitee", re.compile(r"([a-z0-9-]+)\.recruitee\.com", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workday", re.compile(r"(https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)", re.I)),
]
IGNORED_SLUGS = {"embed", "v1", "api", "www", "jobs", "careers", "static", "assets", "cdn", "app", "widget",
                 "js", "css", "favicon", "images", "img", "fonts", "media"}
TEAMTAILOR_HINT = re.compile(r"teamtailor", re.I)


@dataclass
class Detection:
    company: Company
    result: SourceResult

    @property
    def verified(self) -> bool:
        return self.result.ok and self.result.raw_count > 0


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "company"


def candidates(page: str, final_url: str, name: str, tier: str) -> list[Company]:
    haystack = final_url + "\n" + page
    found: list[Company] = []
    for ats, pattern in PATTERNS:
        for match in pattern.finditer(haystack):
            ident = match.group(1)
            if ats == "workday":
                label = urlparse(ident).netloc.split(".")[0]
                found.append(Company(name, "workday", label, tier, url=ident))
            elif ident.lower() not in IGNORED_SLUGS:
                if ats == "personio":
                    host = re.search(rf"{re.escape(ident)}\.jobs\.personio\.(de|com)", haystack, re.I)
                    tld = host.group(1).lower() if host else "de"
                    found.append(Company(name, ats, ident.lower(), tier, url=f"https://{ident.lower()}.jobs.personio.{tld}"))
                else:
                    found.append(Company(name, ats, ident if ats == "smartrecruiters" else ident.lower(), tier))
    if TEAMTAILOR_HINT.search(page):
        u = urlparse(final_url)
        found.append(Company(name, "teamtailor", _slugify(name), tier, url=f"{u.scheme}://{u.netloc}"))
    if extract_postings(page) or _job_links(page, final_url):
        found.append(Company(name, "jsonld", _slugify(name), tier, url=final_url))
    unique: dict[tuple, Company] = {}
    for c in found:
        unique.setdefault((c.ats, c.slug.lower(), c.url), c)
    return list(unique.values())[:8]


SLUG_BOARDS = ("greenhouse", "ashby", "lever", "personio", "recruitee", "smartrecruiters")


def guess_slugs(name: str, url: str) -> list[str]:
    """Likely board identifiers from the company name and domain: helsing, helsingai, helsing-ai."""
    label = urlparse(url).netloc.removeprefix("www.").removeprefix("careers.").removeprefix("jobs.").split(".")[0]
    words = re.findall(r"[a-z0-9]+", name.lower())
    options = [label, "".join(words), "-".join(words)]
    options += [o + "ai" for o in options[:2]] + [label + "-ai"]
    return [o for o in dict.fromkeys(options) if o and o not in IGNORED_SLUGS]


def _probe_slugs(name: str, url: str, tier: str) -> list[Company]:
    """Companies whose board answers for a guessed slug (used when the page shows nothing)."""
    from concurrent.futures import ThreadPoolExecutor

    from . import http_get_json

    endpoints = {
        "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
        "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
        "lever": "https://api.lever.co/v0/postings/{s}?mode=json&limit=1",
        "recruitee": "https://{s}.recruitee.com/api/offers/",
        "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1",
    }

    def probe(args):
        ats, slug = args
        try:
            if ats == "personio":
                body, _ = http_get_text(f"https://{slug}.jobs.personio.de/xml", browser=False)
                return Company(name, ats, slug, tier) if "<position>" in body else None
            data = http_get_json(endpoints[ats].format(s=slug))
            jobs = data.get("jobs") or data.get("offers") or data.get("content") if isinstance(data, dict) else data
            return Company(name, ats, slug, tier) if jobs else None
        except Exception:
            return None

    tasks = [(ats, slug) for slug in guess_slugs(name, url) for ats in SLUG_BOARDS]
    with ThreadPoolExecutor(12) as pool:
        return [c for c in pool.map(probe, tasks) if c]


def detect(url: str, name: str = "", tier: str = "ai_native") -> list[Detection]:
    """Candidate boards for a careers page, each verified by a live collection run."""
    name = name or urlparse(url).netloc.removeprefix("www.").split(".")[0].title()
    try:
        page, final_url = http_get_text(url)
        found = candidates(page, final_url, name, tier)
    except Exception:
        found = []
    # JavaScript-rendered pages hide their board; fall back to guessing the slug.
    if not any(c.ats != "jsonld" for c in found):
        found += _probe_slugs(name, url, tier)
    out = []
    for company in found:
        out.append(Detection(company, collect_company(company, cutoff="")))
    if not out:
        return [Detection(Company(name, "jsonld", _slugify(name), tier, url=url),
                          SourceResult("detect", url, outcome="EMPTY",
                                       detail="no job board link, JobPosting data, or guessable board slug"))]
    out.sort(key=lambda d: (not d.verified, d.company.ats == "jsonld", -len(d.result.jobs)))
    return out


def company_line(c: Company) -> str:
    url = f', url="{c.url}"' if c.url else ""
    return f'Company("{c.name}", "{c.ats}", "{c.slug}", "{c.tier}"{url}),'
