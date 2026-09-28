"""Agent tools. Each wraps a pipeline function, so the agent and the pipeline share code.

Tools that touch the network have per-run budgets, enforced here rather than
trusted to the prompt.
"""

import json
from dataclasses import dataclass, field

from .. import store
from ..companies import WATCHLIST, Company, by_name
from ..config import REGIONS, REMOTE_EU, Settings
from ..normalize import company_key
from ..sources import collect_company, fetch_description, linkedin

LINKEDIN_CALL_BUDGET = 6
BOARD_CHECK_BUDGET = 12
CANDIDATE_BUDGET = 10
DETAIL_CHARS = 6000


@dataclass
class ToolContext:
    settings: Settings
    conn: object
    run_id: int
    since: str
    regions: list[str] = field(default_factory=lambda: ["DE", "NL", "IE"])
    linkedin_calls: int = 0
    board_checks: int = 0
    candidates: int = 0
    found_keys: list[str] = field(default_factory=list)


def _strict(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


REGION_ENUM = ["DE", "NL", "IE"]
ATS_ENUM = ["greenhouse", "ashby", "lever", "personio", "recruitee", "smartrecruiters",
            "workday", "teamtailor", "jsonld"]

TOOL_DEFS = [
    _strict(
        "list_jobs",
        "List jobs collected in this run, best fit first (unscored jobs sort after scored ones).",
        {
            "region": {"type": "string", "enum": [*REGION_ENUM, REMOTE_EU, "ALL"]},
            "only_new": {"type": "boolean", "description": "Only jobs first seen in this run."},
            "min_fit_score": {"type": ["integer", "null"], "description": "Filter by fit score; null = no filter."},
            "limit": {"type": "integer", "description": "Max rows, 1-60."},
        },
    ),
    _strict(
        "get_job_detail",
        "Full description and fit assessment for one job_key returned by another tool.",
        {"job_key": {"type": "string"}},
    ),
    _strict(
        "search_linkedin",
        f"Search LinkedIn for AI jobs in one region. Budget: {LINKEDIN_CALL_BUDGET} calls per run. "
        "Results are saved and appear in the candidate's report.",
        {
            "query": {"type": "string", "description": "Keywords, e.g. 'RAG engineer'."},
            "region": {"type": "string", "enum": REGION_ENUM},
            "posted_within": {"type": "string", "enum": ["24h", "7d", "30d"]},
        },
    ),
    _strict(
        "detect_company_board",
        "Given a company's careers page URL, find which job board it uses (Greenhouse, Ashby, Lever, "
        "Personio, Recruitee, SmartRecruiters, Workday, Teamtailor, or schema.org JobPosting data) and "
        "verify it. Returns relevant AI/software roles in the run's regions. "
        f"Budget: {BOARD_CHECK_BUDGET} calls per run, shared with check_company_board.",
        {
            "careers_url": {"type": "string"},
            "company_name": {"type": "string"},
        },
    ),
    _strict(
        "check_company_board",
        "Check a known job board directly: HTTP status, total jobs, and relevant AI/software roles in "
        f"DE/NL/Dublin. Budget: {BOARD_CHECK_BUDGET} calls per run, shared with detect_company_board.",
        {
            "ats": {"type": "string", "enum": ATS_ENUM},
            "slug": {"type": "string", "description": "Board slug, e.g. 'openai' for jobs.ashbyhq.com/openai."},
            "url": {"type": ["string", "null"], "description": "Board URL for workday / teamtailor / jsonld, else null."},
            "company_name": {"type": "string"},
        },
    ),
    _strict(
        "add_company_candidate",
        "Record an AI company NOT on the watchlist that is hiring engineers in DE/NL/Dublin, "
        f"for the candidate to review. Budget: {CANDIDATE_BUDGET} per run.",
        {
            "name": {"type": "string"},
            "careers_url": {"type": "string"},
            "ats": {"type": "string", "enum": [*ATS_ENUM, "other", "unknown"]},
            "slug": {"type": ["string", "null"], "description": "Verified ATS slug, or null."},
            "regions": {"type": "array", "items": {"type": "string", "enum": REGION_ENUM}},
            "reason": {"type": "string", "description": "One sentence: what they build and which roles are open."},
        },
    ),
]


# --- implementations -----------------------------------------------------------------


def _row_summary(row) -> dict:
    return {
        "job_key": row["job_key"],
        "title": row["title"],
        "company": row["company"],
        "tier": row["company_tier"],
        "location": row["location"][:80],
        "region": row["region"],
        "posted": (row["posted_at"] or "")[:10],
        "new": bool(row["is_new"]),
        "fit_score": row["fit_score"],
        "priority": row["apply_priority"],
    }


def list_jobs(ctx: ToolContext, region: str, only_new: bool, min_fit_score: int | None, limit: int) -> dict:
    rows = store.open_jobs(ctx.conn, ctx.run_id)
    if region != "ALL":
        rows = [r for r in rows if r["region"] == region]
    if only_new:
        rows = [r for r in rows if r["is_new"]]
    if min_fit_score is not None:
        rows = [r for r in rows if (r["fit_score"] or -1) >= min_fit_score]
    rows.sort(key=lambda r: (r["fit_score"] if r["fit_score"] is not None else -1,
                             r["posted_at"] or ""), reverse=True)
    limit = max(1, min(limit, 60))
    return {"total_matching": len(rows), "jobs": [_row_summary(r) for r in rows[:limit]]}


def get_job_detail(ctx: ToolContext, job_key: str) -> dict:
    row = store.get_job(ctx.conn, job_key)
    if row is None:
        return {"error": f"unknown job_key {job_key}"}
    job = store.row_to_job(row)
    if not job.description:
        fetch_description(job)
        if job.description:
            store.save_description(ctx.conn, job_key, job.description, job.extra)
    a = store.get_assessment(ctx.conn, job_key)
    return {
        "job_key": job_key, "title": job.title, "company": job.company, "location": job.location,
        "posted": job.posted_at[:10], "url": job.url,
        "description": (job.description or "(unavailable)")[:DETAIL_CHARS],
        "assessment": json.loads(a["data"]) if a else None,
    }


def search_linkedin(ctx: ToolContext, query: str, region: str, posted_within: str) -> dict:
    if region not in ctx.regions:
        return {"error": f"region {region} is outside this run (regions: {', '.join(ctx.regions)})."}
    if ctx.linkedin_calls >= LINKEDIN_CALL_BUDGET:
        return {"error": "LinkedIn budget for this run is used up; do not call again."}
    ctx.linkedin_calls += 1
    res = linkedin.collect(region, query, since=posted_within,
                           max_results=15, deadline=ctx.settings.scrape_deadline)
    jobs = store.dedupe_batch(res.jobs)
    new_keys = store.upsert_jobs(ctx.conn, jobs, ctx.run_id)
    ctx.found_keys.extend(new_keys)
    return {
        "outcome": res.outcome, "detail": res.detail, "relevant": len(jobs),
        "new_to_report": len(new_keys),
        "jobs": [{"job_key": j.job_key, "title": j.title, "company": j.company,
                  "location": j.location, "posted": j.posted_at[:10],
                  "new": j.job_key in new_keys} for j in jobs[:15]],
        "calls_left": LINKEDIN_CALL_BUDGET - ctx.linkedin_calls,
    }


def _board_summary(res, on_watchlist: bool, regions: list[str]) -> dict:
    jobs = [j for j in res.jobs if j.region in regions or j.region == "REMOTE_EU"]
    return {
        "outcome": res.outcome, "detail": res.detail[:200], "total_jobs": res.raw_count,
        "relevant_in_region": len(jobs),
        "sample": [{"title": j.title, "location": j.location[:60], "posted": j.posted_at[:10]}
                   for j in jobs[:8]],
        "on_watchlist": on_watchlist,
    }


def check_company_board(ctx: ToolContext, ats: str, slug: str, url: str | None, company_name: str) -> dict:
    if ctx.board_checks >= BOARD_CHECK_BUDGET:
        return {"error": "board-check budget for this run is used up."}
    ctx.board_checks += 1
    slug = slug.strip() if ats == "smartrecruiters" else slug.strip().lower()
    res = collect_company(Company(company_name, ats, slug, "ai_native", url=url or ""), "")
    return _board_summary(res, by_name(company_name) is not None or by_name(slug) is not None, ctx.regions)


def detect_company_board(ctx: ToolContext, careers_url: str, company_name: str) -> dict:
    from ..sources.detect import detect

    if ctx.board_checks >= BOARD_CHECK_BUDGET:
        return {"error": "board-check budget for this run is used up."}
    ctx.board_checks += 1
    detections = detect(careers_url, name=company_name)
    best = next((d for d in detections if d.verified), None)
    if best is None:
        return {"found": False, "detail": detections[0].result.detail if detections else "nothing found"}
    c = best.company
    return {"found": True, "ats": c.ats, "slug": c.slug, "url": c.url or None,
            **_board_summary(best.result, by_name(company_name) is not None, ctx.regions)}


def add_company_candidate(ctx: ToolContext, name: str, careers_url: str, ats: str,
                          slug: str | None, regions: list[str], reason: str) -> dict:
    key = company_key(name)
    if any(company_key(c.name) == key for c in WATCHLIST):
        return {"status": "already_on_watchlist"}
    if ctx.candidates >= CANDIDATE_BUDGET:
        return {"error": "candidate budget for this run is used up."}
    added = store.add_candidate(
        ctx.conn, name=name, careers_url=careers_url, ats=ats, slug=slug or "",
        regions=",".join(regions), reason=reason, run_id=ctx.run_id,
    )
    if added:
        ctx.candidates += 1
    return {"status": "recorded" if added else "already_recorded"}


DISPATCH = {
    "list_jobs": list_jobs,
    "get_job_detail": get_job_detail,
    "search_linkedin": search_linkedin,
    "check_company_board": check_company_board,
    "detect_company_board": detect_company_board,
    "add_company_candidate": add_company_candidate,
}


def watchlist_names() -> str:
    return ", ".join(c.name for c in WATCHLIST)


def region_names(codes: list[str] | None = None) -> str:
    return ", ".join(f"{r.code}={r.label}" for r in REGIONS.values() if not codes or r.code in codes)
