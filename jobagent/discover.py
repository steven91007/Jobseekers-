"""Find AI companies' job boards in Common Crawl, then verify them against the live boards.

Common Crawl's URL index is sorted by SURT key ("com,workable,apply)/<slug>/..."), and
its cluster.idx maps key ranges to gzipped blocks of index lines. A binary search over
cluster.idx with HTTP range requests finds the few blocks covering one board host, so
listing every Workable, Ashby or Greenhouse board takes seconds and needs no key. (The
CDX query server times out on whole-host wildcards, so it is not used.)

Each run verifies a limited number of not-yet-checked slugs, AI-sounding names first,
and records every check in SQLite so later runs move on to the rest.
"""

import gzip
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta

import requests

from .companies import WATCHLIST, Company
from .sources import USER_AGENT, SourceResult, collect_company

DATA = "https://data.commoncrawl.org/cc-index/collections/{coll}/indexes/"
COLLINFO = "https://index.commoncrawl.org/collinfo.json"
TIMEOUT = (10, 60)

# SURT prefixes of each board host; the slug is the first path segment.
HOSTS: dict[str, list[str]] = {
    "workable": ["com,workable,apply)/"],
    "ashby": ["com,ashbyhq,jobs)/"],
    "greenhouse": ["io,greenhouse,job-boards)/", "io,greenhouse,boards)/"],
}
_SLUG = re.compile(r"^([a-z0-9][a-z0-9_.-]{1,60})(?:[/?\s]|$)")
_JUNK = re.compile(r"^(\d+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{24,}|embed|api|v1|j|static|assets)$")
AI_HINT = re.compile(r"ai|ml|llm|gpt|neural|deep|vision|intellig|cognit|agent|robot|data|labs?|learn|"
                     r"model|brain|mind|semantic|nlp|voice|speech|quantum|autonom", re.I)


class DiscoveryError(Exception):
    pass


@dataclass
class Found:
    company: Company
    result: SourceResult

    @property
    def relevant(self) -> int:
        return len(self.result.jobs)


def latest_collection(session: requests.Session) -> str:
    """Newest crawl id. The collection list lives on the (often overloaded) index server, so
    fall back to probing recent weekly ids on the static data server."""
    for attempt in range(2):
        try:
            return session.get(COLLINFO, timeout=TIMEOUT).json()[0]["id"]
        except (requests.RequestException, ValueError, KeyError, IndexError):
            time.sleep(2 * (attempt + 1))
    today = date.today()
    for weeks_back in range(0, 16):
        year, week, _ = (today - timedelta(weeks=weeks_back)).isocalendar()
        coll = f"CC-MAIN-{year}-{week:02d}"
        try:
            if session.head(DATA.format(coll=coll) + "cluster.idx", timeout=TIMEOUT).status_code == 200:
                return coll
        except requests.RequestException:
            continue
    raise DiscoveryError("no recent Common Crawl collection found")


def _range(session, url: str, start: int, end: int) -> bytes:
    """One byte range, retried: data.commoncrawl.org sometimes drops a connection mid-transfer."""
    last: Exception | None = None
    for attempt in range(4):
        if attempt:
            time.sleep(2 * attempt)
        try:
            resp = session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            continue
        if resp.status_code in (200, 206):
            return resp.content
        last = DiscoveryError(f"HTTP {resp.status_code} for {url}")
        if resp.status_code < 500 and resp.status_code != 429:
            break
    raise last or DiscoveryError(f"no response for {url}")


def _blocks(session, base: str, size: int, prefix: str) -> list[list[str]]:
    """cluster.idx rows (key, shard, offset, length) whose blocks may hold `prefix` keys."""
    idx = base + "cluster.idx"
    lo, hi = 0, size
    while hi - lo > 8192:
        mid = (lo + hi) // 2
        chunk = _range(session, idx, mid, mid + 8192)
        line = chunk.split(b"\n", 2)[1].decode(errors="replace")  # first complete line
        if line.split(" ", 1)[0] < prefix:
            lo = mid
        else:
            hi = mid
    lines = _range(session, idx, lo, lo + 400_000).decode(errors="replace").split("\n")[1:-1]
    rows = [line.split("\t") for line in lines if line.count("\t") >= 3]
    out: list[list[str]] = []
    for i, row in enumerate(rows):
        key = row[0].split(" ", 1)[0]
        if key < prefix:
            continue
        if not out and i:
            out.append(rows[i - 1])  # the block before the first match may start inside the range
        if not key.startswith(prefix):
            break
        out.append(row)
    return out


def crawl_slugs(ats: str, *, collection: str | None = None, session: requests.Session | None = None) -> set[str]:
    """Every board slug of one ATS that the latest Common Crawl saw."""
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    coll = collection or latest_collection(session)
    base = DATA.format(coll=coll)
    size = 0
    for attempt in range(4):
        try:
            head = session.head(base + "cluster.idx", timeout=TIMEOUT)
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(2 * (attempt + 1))
            continue
        if head.status_code == 200:
            size = int(head.headers["content-length"])
            break
        time.sleep(2 * (attempt + 1))
    if not size:
        raise DiscoveryError(f"cluster.idx for {coll} is unavailable")
    slugs: set[str] = set()
    for prefix in HOSTS[ats]:
        for row in _blocks(session, base, size, prefix):
            shard, offset, length = row[1], int(row[2]), int(row[3])
            text = gzip.decompress(_range(session, base + shard, offset, offset + length - 1)).decode(errors="replace")
            for line in text.splitlines():
                if not line.startswith(prefix):
                    continue
                m = _SLUG.match(line[len(prefix):])
                if m and not _JUNK.match(m.group(1)):
                    slugs.add(m.group(1).rstrip("."))
    return slugs


def prioritize(slugs: set[str], skip: set[str], limit: int) -> list[str]:
    """Unchecked slugs, AI-sounding names first, then shortest (real company names)."""
    todo = [s for s in slugs if s not in skip]
    todo.sort(key=lambda s: (not AI_HINT.search(s), len(s), s))
    return todo[:limit]


def verify(ats: str, slugs: list[str], *, workers: int = 8) -> list[Found]:
    """Run the board collector for each slug. Tier ai_heavy keeps only AI/ML titles."""
    def one(slug: str) -> Found:
        name = slug.replace("-", " ").replace("_", " ").title()
        return Found(Company(name, ats, slug, "ai_heavy"), collect_company(Company(name, ats, slug, "ai_heavy"), ""))

    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, slugs))


def watchlist_slugs(ats: str) -> set[str]:
    return {c.slug.lower() for c in WATCHLIST if c.ats == ats}


BOARD_URL = {"workable": "https://apply.workable.com/{slug}", "ashby": "https://jobs.ashbyhq.com/{slug}",
             "greenhouse": "https://job-boards.greenhouse.io/{slug}"}


def run(conn, ats_list: list[str], *, limit: int = 100, workers: int = 8,
        collection: str | None = None, progress=print) -> list[Found]:
    """Discover, verify and record. Boards with relevant DE/NL/IE roles become company candidates."""
    from . import store

    session = requests.Session()
    found: list[Found] = []
    for ats in ats_list:
        slugs = crawl_slugs(ats, collection=collection, session=session)
        todo = prioritize(slugs, watchlist_slugs(ats) | store.checked_slugs(conn, ats), limit)
        progress(f"{ats}: {len(slugs)} boards in Common Crawl, verifying {len(todo)}")
        for f in verify(ats, todo, workers=workers):
            store.record_check(conn, ats, f.company.slug, f.result.outcome, f.result.raw_count, f.relevant)
            if not f.relevant:
                continue
            found.append(f)
            regions = sorted({j.region for j in f.result.jobs})
            sample = "; ".join(j.title for j in f.result.jobs[:3])
            store.add_candidate(conn, name=f.company.name, careers_url=BOARD_URL[ats].format(slug=f.company.slug),
                                ats=ats, slug=f.company.slug, regions=",".join(regions),
                                reason=f"Common Crawl discovery: {f.relevant} AI roles, e.g. {sample}"[:500],
                                run_id=None)
    return found
