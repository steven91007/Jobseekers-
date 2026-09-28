"""One end-to-end run: collect -> store -> score -> agent -> score agent finds -> report."""

import contextvars
import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from rich.console import Console

from . import observability as obs
from . import report, store
from .companies import WATCHLIST
from .config import REGION_LABELS, ROLE_QUERIES, SINCE_CHOICES, Settings
from .llm.prompts import PROMPT_VERSION
from .sources import SourceResult, collect_company, linkedin

log = logging.getLogger(__name__)

# Two blocked/rate-limited LinkedIn searches in a row: stop LinkedIn for this run.
LINKEDIN_CIRCUIT_BREAK = 2
AGENT_FOLLOWUP_SCORE_CAP = 15


def cutoff_for(since: str) -> str:
    return (date.today() - timedelta(days=SINCE_CHOICES[since])).isoformat()


def _record(sp, res: SourceResult) -> None:
    sp.update(
        output={"outcome": res.outcome, "raw": res.raw_count, "kept": len(res.jobs),
                "detail": res.detail or None},
        level=None if res.ok else "WARNING",
        status_message=None if res.ok else f"{res.outcome}: {res.detail}"[:500],
    )


def collect_linkedin(settings: Settings, regions: list[str], since: str, console: Console) -> list[SourceResult]:
    results: list[SourceResult] = []
    blocked_streak = 0
    searches = [(r, q) for r in regions for q in ROLE_QUERIES]
    for i, (region, query) in enumerate(searches):
        if blocked_streak >= LINKEDIN_CIRCUIT_BREAK:
            log.error("LinkedIn blocked %d times in a row; skipping the rest", blocked_streak)
            results.append(SourceResult("linkedin", "circuit-breaker", outcome="SKIPPED",
                                        detail=f"{len(searches) - i} searches skipped after blocks"))
            break
        if i:
            time.sleep(random.uniform(settings.linkedin_gap_min, settings.linkedin_gap_max))
        with obs.span(obs.NAMES.COLLECT_LINKEDIN, as_type="retriever",
                      input={"query": query, "region": region, "since": since},
                      metadata={"region": region, "query": query}) as sp:
            res = linkedin.collect(region, query, since=since,
                                   max_results=settings.linkedin_per_query,
                                   deadline=settings.scrape_deadline)
            _record(sp, res)
        results.append(res)
        blocked_streak = blocked_streak + 1 if res.outcome in ("BLOCKED", "RATE_LIMITED") else 0
        console.print(f"  [dim]LinkedIn {region:2s} {query:28s} {res.outcome:10s} kept {len(res.jobs):3d}[/dim]")
    return results


def collect_watchlist(settings: Settings, regions: list[str], console: Console) -> list[SourceResult]:
    def one(company):
        with obs.span(obs.NAMES.COLLECT_BOARD, as_type="retriever",
                      input={"company": company.name, "ats": company.ats, "slug": company.slug},
                      metadata={"company": company.name, "ats": company.ats, "tier": company.tier}) as sp:
            res = collect_company(company, cutoff="")  # all open roles; report splits by date
            res.jobs = [j for j in res.jobs if j.region in regions or j.region == "REMOTE_EU"]
            _record(sp, res)
            return res

    with ThreadPoolExecutor(settings.ats_workers) as pool:
        # Copy the context here, in the calling thread, so each span nests under
        # collect-jobs. (Copying inside the worker would copy an empty context.)
        futures = [pool.submit(contextvars.copy_context().run, one, c) for c in WATCHLIST]
        results = [f.result() for f in futures]
    for res in results:
        if res.jobs or not res.ok:
            console.print(f"  [dim]{res.source:10s} {res.label:20s} {res.outcome:6s} kept {len(res.jobs):3d}[/dim]")
    return results


def run(
    settings: Settings,
    *,
    since: str = "7d",
    regions: list[str] | None = None,
    use_llm: bool = True,
    use_agent: bool = True,
    skip_linkedin: bool = False,
    skip_ats: bool = False,
    max_score: int | None = None,
    console: Console | None = None,
) -> dict:
    console = console or Console()
    regions = regions or ["DE", "NL", "IE"]
    cutoff = cutoff_for(since)
    obs.init(settings)
    conn = store.connect(settings.db_path)
    run_id = store.start_run(conn, since)
    llm_on = use_llm and settings.llm_enabled
    stats: dict = {"run_id": run_id, "since": since, "regions": regions, "llm": llm_on,
                   "langfuse": obs.enabled(), "prompt_version": PROMPT_VERSION}
    trace_id = None
    md_path = xlsx_path = None
    briefing = ""

    try:
        with obs.trace_attributes(
            session_id=f"jobagent-{date.today().isoformat()}",
            user_id=settings.langfuse_user_id,
            tags=["jobagent", "daily-run", *(f"region:{r}" for r in regions)],
            trace_name=obs.NAMES.TRACE,
            metadata={"since": since, "regions": ",".join(regions), "llm": llm_on, "run_id": run_id},
            version=PROMPT_VERSION,
        ):
            with obs.span(obs.NAMES.ROOT,
                          input={"request": f"Newest AI jobs posted within {since} in "
                                            + ", ".join(REGION_LABELS[r] for r in regions),
                                 "regions": regions, "since": since, "role_queries": ROLE_QUERIES,
                                 "watchlist_companies": len(WATCHLIST)},
                          metadata={"run_id": run_id, "llm": llm_on, "skip_linkedin": skip_linkedin,
                                    "skip_ats": skip_ats}) as root:
                trace_id = root.trace_id

                # 1. collect
                console.print(f"[bold cyan]Collecting[/bold cyan] jobs posted within {since} "
                              f"in {', '.join(REGION_LABELS[r] for r in regions)}")
                results: list[SourceResult] = []
                with obs.span(obs.NAMES.COLLECT,
                              input={"linkedin_searches": 0 if skip_linkedin else len(regions) * len(ROLE_QUERIES),
                                     "job_boards": 0 if skip_ats else len(WATCHLIST)}) as cs:
                    if not skip_ats:
                        results += collect_watchlist(settings, regions, console)
                    if not skip_linkedin:
                        results += collect_linkedin(settings, regions, since, console)
                    cs.update(output={"sources": len(results),
                                      "failed": sum(1 for r in results if not r.ok)})
                stats["sources"] = {}
                for r in results:
                    agg = stats["sources"].setdefault(r.source, {"calls": 0, "kept": 0, "failed": 0})
                    agg["calls"] += 1
                    agg["kept"] += len(r.jobs)
                    agg["failed"] += 0 if r.ok else 1
                stats["failed_sources"] = [f"{r.source}:{r.label} ({r.outcome})" for r in results if not r.ok]

                # 2. dedupe + store
                with obs.span(obs.NAMES.STORE) as ss:
                    all_jobs = [j for r in results for j in r.jobs]
                    kept = store.dedupe_batch(all_jobs)
                    new_keys = store.upsert_jobs(conn, kept, run_id)
                    stats.update(collected=len(all_jobs), unique=len(kept), new=len(new_keys))
                    ss.update(output={"collected": len(all_jobs), "unique": len(kept), "new": len(new_keys)})
                console.print(f"[green]{len(kept)} unique relevant jobs[/green] "
                              f"({len(all_jobs)} before dedupe), {len(new_keys)} new since last run")

                # 3-4. LLM layer
                if llm_on:
                    briefing = _llm_phase(settings, conn, run_id, since, regions, max_score, use_agent, stats, console)
                elif use_llm:
                    console.print("[yellow]OPENAI_API_KEY not set: skipping scoring and the agent.[/yellow]")

                # 5. report
                with obs.span(obs.NAMES.REPORT) as rs:
                    md_path, xlsx_path, rows = _write_reports(settings, conn, run_id, since, cutoff,
                                                              stats, briefing, trace_id)
                    rs.update(output={"markdown": str(md_path), "excel": str(xlsx_path), "rows": len(rows)})
                report.print_console(rows, console)
                root.update(output=_root_output(rows, stats, briefing, md_path),
                            metadata={k: stats.get(k) for k in ("collected", "unique", "new", "scoring", "agent")})
    finally:
        store.finish_run(conn, run_id, stats, trace_id, str(md_path) if md_path else None)
        obs.flush()
        conn.close()

    stats.update(markdown=str(md_path), excel=str(xlsx_path), trace_id=trace_id,
                 trace_url=obs.trace_url(trace_id))
    return stats


def _root_output(rows: list[dict], stats: dict, briefing: str, md_path) -> dict:
    """What a reviewer needs at a glance: the best jobs, the briefing, where the report is."""
    top = [r for r in rows if r["score"] is not None][:10] or rows[:10]
    return {
        "top_jobs": [{"score": r["score"], "priority": r["priority"] or None, "title": r["title"],
                      "company": r["company"], "region": r["region"], "posted": r["posted"],
                      "new": r["is_new"], "url": r["url"]} for r in top],
        "counts": {"open": len(rows), "new": stats.get("new", 0),
                   "scored": sum(1 for r in rows if r["score"] is not None)},
        "briefing": briefing or None,
        "report": str(md_path),
    }


def _llm_phase(settings, conn, run_id, since, regions, max_score, use_agent, stats, console) -> str:
    from .llm.agent import run_agent
    from .llm.client import get_client, load_profile
    from .llm.scorer import score_jobs
    from .llm.tools import ToolContext

    client = get_client(settings)
    profile, real_profile = load_profile(settings)
    if not real_profile:
        console.print("[yellow]profile.md missing: scoring against the example profile. "
                      "Copy profile.example.md to profile.md and fill it in.[/yellow]")
    cap = max_score if max_score is not None else settings.max_score_per_run
    keys = store.unassessed_keys(conn, run_id, cap)
    console.print(f"[bold cyan]Scoring[/bold cyan] {len(keys)} jobs with {settings.openai_scorer_model}")
    stats["scoring"] = score_jobs(client, conn, settings, keys, profile)
    console.print(f"  scored {stats['scoring']['scored']}, failed {stats['scoring']['failed']}"
                  + (f", aborted: {stats['scoring']['aborted']}" if stats["scoring"]["aborted"] else ""))
    if stats["scoring"]["aborted"] or not use_agent:
        return ""

    console.print(f"[bold cyan]Agent[/bold cyan] researching with {settings.openai_model} "
                  f"(max {settings.agent_max_turns} turns)")
    rows = store.open_jobs(conn, run_id)
    counts = {REGION_LABELS[r]: sum(1 for x in rows if x["region"] == r) for r in REGION_LABELS}
    counts["scored"] = sum(1 for x in rows if x["fit_score"] is not None)
    ctx = ToolContext(settings=settings, conn=conn, run_id=run_id, since=since, regions=regions)
    result = run_agent(client, ctx, counts, profile)
    stats["agent"] = {"turns": result["turns"], "tool_calls": result["tool_calls"],
                      "new_jobs_found": len(ctx.found_keys), "candidates": ctx.candidates,
                      "error": result["error"] or None}
    _save_transcript(settings, run_id, result)
    console.print(f"  {result['turns']} turns, {result['tool_calls']} tool calls, "
                  f"{len(ctx.found_keys)} extra jobs, {ctx.candidates} company candidates"
                  + (f", error: {result['error']}" if result["error"] else ""))

    # --max-score is a budget for the whole run; otherwise agent finds get their own small cap.
    followup_cap = max(0, cap - stats["scoring"]["requested"]) if max_score is not None else AGENT_FOLLOWUP_SCORE_CAP
    if ctx.found_keys and followup_cap:
        extra = [k for k in ctx.found_keys if store.get_assessment(conn, k) is None][:followup_cap]
        followup = score_jobs(client, conn, settings, extra, profile, phase="agent-followup")
        stats["scoring"]["scored"] += followup["scored"]
        stats["scoring"]["failed"] += followup["failed"]
    return result["briefing"]


def _save_transcript(settings: Settings, run_id: int, result: dict) -> None:
    settings.agent_runs_dir.mkdir(parents=True, exist_ok=True)
    path = settings.agent_runs_dir / f"{date.today().isoformat()}-run{run_id}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _write_reports(settings, conn, run_id, since, cutoff, stats, briefing, trace_id):
    rows = report.build_rows(store.open_jobs(conn, run_id), cutoff)
    candidates = store.list_candidates(conn)
    stem = f"jobs_{date.today().isoformat()}"
    md_path = settings.reports_dir / f"{stem}.md"
    xlsx_path = settings.reports_dir / f"{stem}.xlsx"
    report.write_markdown(md_path, rows, since=since, cutoff=cutoff, stats=stats, briefing=briefing,
                          candidates=candidates, trace_link=obs.trace_url(trace_id))
    report.write_excel(xlsx_path, rows, candidates)
    return md_path, xlsx_path, rows


def rebuild_report(settings: Settings, console: Console) -> dict:
    """Re-render the latest run's report from the database (no network, no LLM)."""
    conn = store.connect(settings.db_path)
    last = store.last_run(conn)
    if last is None:
        raise SystemExit("No runs yet. Run `python -m jobagent run` first.")
    stats = json.loads(last["stats"] or "{}")
    briefing = ""
    for transcript in settings.agent_runs_dir.glob(f"*-run{last['id']}.json"):
        briefing = json.loads(transcript.read_text(encoding="utf-8")).get("briefing", "")
    md, xlsx, rows = _write_reports(settings, conn, last["id"], last["since"],
                                    cutoff_for(last["since"]), stats, briefing, last["trace_id"])
    report.print_console(rows, console)
    conn.close()
    return {"markdown": str(md), "excel": str(xlsx)}
