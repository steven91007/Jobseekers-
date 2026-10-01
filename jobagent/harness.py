"""Live harness: proves each keyless source really finds jobs right now.

`python -m jobagent sources check` runs every check against the live sites and exits
non-zero if any of them comes back empty or broken. Offline unit tests cover parsing;
this covers "the site still answers, our request is still accepted, and the jobs we
want are still there". Nothing here needs an API key or a paid service.
"""

import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .companies import WATCHLIST


@dataclass
class Check:
    name: str
    ok: bool
    summary: str
    samples: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _sample(jobs, n=3) -> list[str]:
    return [f"{j.posted_at[:10]}  {j.title}  ({j.company}, {j.location[:30]})" for j in jobs[:n]]


def check_arbeitsagentur() -> Check:
    from .sources import arbeitsagentur

    res = arbeitsagentur.collect("AI Engineer", since="7d")
    if not res.jobs:
        return Check("arbeitsagentur", False, f"{res.outcome}: no relevant jobs ({res.detail or res.raw_count})")
    desc = arbeitsagentur.fetch_description(res.jobs[0])
    ok = len(desc) > 100
    return Check("arbeitsagentur", ok, f"{len(res.jobs)} relevant of {res.raw_count} 'AI Engineer' postings "
                 f"in 7 days; description {'fetched' if ok else 'MISSING'} ({len(desc)} chars)", _sample(res.jobs))


def check_arbeitnow() -> Check:
    from .sources import arbeitnow

    cutoff = (date.today() - timedelta(days=2)).isoformat()
    res = arbeitnow.collect(cutoff_iso=cutoff, regions=["DE", "NL", "IE"])
    ok = bool(res.jobs) and res.ok
    return Check("arbeitnow", ok, f"{res.outcome}: {len(res.jobs)} relevant of {res.raw_count} postings scanned "
                 f"since {cutoff}" + (f" ({res.detail})" if res.detail else ""), _sample(res.jobs))


def check_workable() -> Check:
    from .sources import collect_company

    boards = [c for c in WATCHLIST if c.ats == "workable"]
    results = [(c, collect_company(c, "")) for c in boards]
    broken = [f"{c.slug} {r.outcome}" for c, r in results if not r.ok]
    jobs = [j for _, r in results for j in r.jobs]
    ok = bool(boards) and not broken and bool(jobs)
    return Check("workable", ok, f"{len(boards)} watchlist boards, {len(jobs)} relevant roles"
                 + (f"; broken: {', '.join(broken)}" if broken else ""), _sample(jobs))


def check_common_crawl() -> Check:
    from . import discover

    known = discover.watchlist_slugs("workable")
    slugs = discover.crawl_slugs("workable")
    seen = known & slugs
    found = discover.verify("workable", sorted(seen)[:3], workers=3)
    hits = [f for f in found if f.relevant]
    ok = len(slugs) > 500 and bool(seen) and bool(hits)
    return Check("common-crawl", ok, f"{len(slugs)} Workable boards in the index; watchlist boards seen: "
                 f"{', '.join(sorted(seen)) or 'none'}; verified with roles: {', '.join(f.company.slug for f in hits) or 'none'}",
                 [f"{f.company.slug}: {f.relevant} relevant roles" for f in hits])


def check_pipeline() -> Check:
    """A real run (no LinkedIn, no LLM) on a scratch DB; new sources must reach the report."""
    from rich.console import Console

    from . import config, pipeline

    with tempfile.TemporaryDirectory() as tmp:
        s = config.load()
        s = s.__class__(**{**s.__dict__, "db_path": Path(tmp) / "h.db", "reports_dir": Path(tmp) / "r",
                           "agent_runs_dir": Path(tmp) / "runs"})
        stats = pipeline.run(s, since="24h", use_llm=False, skip_linkedin=True, console=Console(quiet=True))
        report = Path(stats["markdown"]).read_text(encoding="utf-8")
    src = stats.get("sources", {})
    # Keyword sources always have fresh postings. Most Jobsuche postings are German, so with
    # the English-only filter it must collect jobs but need not reach the report. Workable
    # boards may have nothing under 7 days old, so for them the bar is "every board answered".
    collected = {n: src.get(n, {}).get("kept", 0) for n in ("arbeitsagentur", "arbeitnow")}
    in_report = [n for n in ("arbeitsagentur", "arbeitnow", "workable") if f"| {n} |" in report]
    wk = src.get("workable", {"calls": 0, "kept": 0, "failed": 0})
    dropped = sum(stats.get("non_english_dropped", {}).values()) + sum(stats.get("non_english_hidden", {}).values())
    english_ok = (not s.english_only) or (dropped > 0 and "English only:" in report)
    ok = (all(collected.values()) and "arbeitnow" in in_report and wk["calls"] > 0 and wk["failed"] == 0
          and english_ok)
    failed = stats.get("failed_sources", [])
    return Check("pipeline", ok, f"{stats.get('unique')} unique jobs; collected: "
                 + ", ".join(f"{n}={c}" for n, c in collected.items())
                 + f", workable={wk['kept']} from {wk['calls']} boards ({wk['failed']} failed)"
                 + f"; English only: {dropped} non-English postings dropped"
                 + f"; sources in report: {', '.join(in_report) or 'none'}",
                 [f"failed source (not fatal unless listed above): {f}" for f in failed])

CHECKS = {"arbeitsagentur": check_arbeitsagentur, "arbeitnow": check_arbeitnow, "workable": check_workable,
          "common-crawl": check_common_crawl, "pipeline": check_pipeline}


def run(names: list[str] | None = None, progress=print) -> list[Check]:
    out = []
    for name in names or list(CHECKS):
        progress(f"checking {name} ...")
        t = time.time()
        try:
            c = CHECKS[name]()
        except Exception as e:  # a crash is a failed check, not a crashed harness
            c = Check(name, False, f"{type(e).__name__}: {e}")
        c.seconds = time.time() - t
        out.append(c)
    return out
