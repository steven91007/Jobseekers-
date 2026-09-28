"""CLI: python -m jobagent <command>."""

import argparse
import logging
import sys

from rich.console import Console
from rich.table import Table

from . import config, observability as obs, store
from .companies import WATCHLIST
from .config import SINCE_CHOICES

console = Console()
FEEDBACK_LABELS = ["applied", "interview", "offer", "rejected", "good-match", "irrelevant"]


def cmd_run(args, settings) -> int:
    from .pipeline import run

    regions = [r.strip().upper() for r in args.regions.split(",")] if args.regions else None
    stats = run(
        settings, since=args.since, regions=regions, use_llm=not args.no_llm,
        use_agent=not args.no_agent, skip_linkedin=args.skip_linkedin,
        skip_ats=args.skip_ats, max_score=args.max_score, console=console,
    )
    console.print(f"\n[bold]Report:[/bold] {stats['markdown']}\n[bold]Excel:[/bold]  {stats['excel']}")
    if stats.get("trace_url"):
        console.print(f"[bold]Langfuse trace:[/bold] {stats['trace_url']}")
    return 0


def cmd_report(args, settings) -> int:
    from .pipeline import rebuild_report

    out = rebuild_report(settings, console)
    console.print(f"[bold]Report:[/bold] {out['markdown']}\n[bold]Excel:[/bold]  {out['excel']}")
    return 0


def cmd_companies(args, settings) -> int:
    if args.action == "list":
        t = Table("Company", "ATS", "Slug", "Tier")
        for c in WATCHLIST:
            t.add_row(c.name, c.ats, c.slug, c.tier)
        console.print(t)
        return 0
    if args.action == "candidates":
        conn = store.connect(settings.db_path)
        rows = store.list_candidates(conn, status=None)
        if not rows:
            console.print("No candidates yet. The agent records them during `run` (needs OPENAI_API_KEY).")
            return 0
        t = Table("Company", "ATS", "Slug", "Regions", "Reason", "Suggested")
        for c in rows:
            t.add_row(c["name"], c["ats"], c["slug"], c["regions"], c["reason"], c["suggested_at"][:10])
        console.print(t)
        return 0

    # verify
    from concurrent.futures import ThreadPoolExecutor

    from .sources import collect_company

    with ThreadPoolExecutor(settings.ats_workers) as pool:
        results = list(pool.map(lambda c: (c, collect_company(c, "")), WATCHLIST))
    t = Table("Company", "ATS", "Slug", "Outcome", "Open jobs", "Relevant in DE/NL/IE")
    broken = 0
    for c, r in results:
        ok = r.outcome in ("OK", "EMPTY")
        broken += 0 if ok else 1
        t.add_row(c.name, c.ats, c.slug, r.outcome if ok else f"[red]{r.outcome}[/red]",
                  str(r.raw_count), str(len(r.jobs)))
    console.print(t)
    console.print(f"{len(results) - broken} boards OK, {broken} failing.")
    return 1 if broken else 0


def cmd_feedback(args, settings) -> int:
    obs.init(settings)
    conn = store.connect(settings.db_path)
    if store.get_job(conn, args.job_key) is None:
        console.print(f"[red]Unknown job key {args.job_key}[/red]")
        return 1
    store.add_feedback(conn, args.job_key, args.label, args.comment)
    a = store.get_assessment(conn, args.job_key)
    sent = False
    if a and a["trace_id"]:
        sent = obs.create_score(trace_id=a["trace_id"], observation_id=a["observation_id"],
                                name="human_label", value=args.label, data_type="CATEGORICAL",
                                comment=args.comment)
        obs.flush()
    console.print(f"Saved '{args.label}' for {args.job_key}"
                  + (" and sent it to Langfuse." if sent else " locally (no Langfuse trace for this job)."))
    return 0


def cmd_search(args, settings) -> int:
    from .sources import linkedin

    res = linkedin.collect(args.region.upper(), args.query, since=args.since,
                           max_results=args.limit, deadline=settings.scrape_deadline)
    t = Table("Posted", "Title", "Company", "Location", "Job key", title=f"{res.outcome}: {len(res.jobs)} relevant")
    for j in res.jobs:
        t.add_row(j.posted_at[:10], j.title, j.company, j.location, j.job_key)
    console.print(t)
    return 0


def cmd_doctor(args, settings) -> int:
    ok = True
    console.print(f"Python OK. Database: {settings.db_path}")
    console.print(f"Profile: {'found' if settings.profile_path.exists() else '[yellow]missing, copy profile.example.md to profile.md[/yellow]'}")
    if settings.llm_enabled:
        try:
            from .llm.client import get_client

            m = get_client(settings).models.retrieve(settings.openai_model)
            console.print(f"OpenAI: key OK, model {m.id} available")
        except Exception as e:
            ok = False
            console.print(f"[red]OpenAI: {type(e).__name__}: {e}[/red]\n"
                          "  Set OPENAI_MODEL in .env to a model your key can use.")
    else:
        console.print("[yellow]OpenAI: OPENAI_API_KEY not set; scoring and the agent are off[/yellow]")
    if obs.init(settings):
        good, msg = obs.auth_check()
        ok &= good
        console.print(f"Langfuse ({settings.langfuse_base_url}): " + ("auth OK" if good else f"[red]{msg}[/red]"))
    else:
        console.print("[yellow]Langfuse: keys not set; tracing off[/yellow]")
    from .sources import linkedin

    res = linkedin.collect("DE", "AI Engineer", since="7d", max_results=5, deadline=30)
    console.print(f"LinkedIn: {res.outcome}, {len(res.jobs)} relevant jobs in a test search")
    ok &= res.ok
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jobagent", description="AI job agent for DE / NL / Dublin")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="collect, score, research and write the report")
    p.add_argument("--since", choices=list(SINCE_CHOICES), default="7d")
    p.add_argument("--regions", help="comma list of DE,NL,IE (default all)")
    p.add_argument("--no-llm", action="store_true", help="skip scoring and the agent")
    p.add_argument("--no-agent", action="store_true", help="score but skip the research agent")
    p.add_argument("--skip-linkedin", action="store_true")
    p.add_argument("--skip-ats", action="store_true")
    p.add_argument("--max-score", type=int, help="max jobs to score this run")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="re-render the latest run's report from the database")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("companies", help="watchlist tools")
    p.add_argument("action", choices=["verify", "list", "candidates"])
    p.set_defaults(func=cmd_companies)

    p = sub.add_parser("feedback", help="label a job; sent to Langfuse as a human score")
    p.add_argument("job_key")
    p.add_argument("--label", choices=FEEDBACK_LABELS, required=True)
    p.add_argument("--comment")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("search", help="one ad-hoc LinkedIn search")
    p.add_argument("query")
    p.add_argument("--region", default="DE", choices=["DE", "NL", "IE", "de", "nl", "ie"])
    p.add_argument("--since", choices=list(SINCE_CHOICES), default="7d")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("doctor", help="check keys, model access, Langfuse and LinkedIn")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("jobagent").setLevel(logging.INFO if not args.verbose else logging.DEBUG)
    return args.func(args, config.load())


if __name__ == "__main__":
    sys.exit(main())
