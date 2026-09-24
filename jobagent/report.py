"""Markdown digest, Excel export and console table for one run."""

import json
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from rich import box
from rich.console import Console
from rich.table import Table

from .config import REGION_LABELS, REGIONS, REMOTE_EU

TIER_RANK = {"ai_native": 0, "ai_heavy": 1, "other": 2}
TIER_LABEL = {"ai_native": "AI-native", "ai_heavy": "AI-heavy", "other": ""}
LANG_FLAG = {"german": "DE req", "dutch": "NL req", "german_nice_to_have": "DE nice",
             "dutch_nice_to_have": "NL nice"}
REGION_ORDER = [*REGIONS, REMOTE_EU]
TOP_DETAILS = 15


def _assessment(row) -> dict:
    try:
        return json.loads(row["assessment"]) if row["assessment"] else {}
    except (TypeError, ValueError):
        return {}


def build_rows(db_rows, cutoff: str) -> list[dict]:
    rows = []
    for r in db_rows:
        a = _assessment(r)
        extra = json.loads(r["extra"] or "{}")
        posted = (r["posted_at"] or "")[:10]
        rows.append({
            "job_key": r["job_key"], "title": r["title"], "company": r["company"],
            "tier": r["company_tier"], "location": r["location"], "region": r["region"],
            "url": r["url"], "posted": posted, "source": r["source"],
            "is_new": bool(r["is_new"]),
            "recent": (not posted) or posted >= cutoff,
            "score": r["fit_score"], "priority": r["apply_priority"] or "",
            "lang": a.get("local_language_required", ""),
            "visa": a.get("visa_or_relocation", ""),
            "salary": a.get("salary") or extra.get("salary") or "",
            "seniority": a.get("seniority", ""),
            "employer_type": a.get("employer_type", ""),
            "why_fit": a.get("why_fit", ""), "pitch": a.get("suggested_pitch", ""),
            "gaps": a.get("gaps", []), "must_haves": a.get("must_haves", []),
            "trace_id": r["a_trace_id"],
        })
    rows.sort(key=sort_key)
    return rows


def sort_key(row: dict):
    score = row["score"] if row["score"] is not None else -1
    return (-score, TIER_RANK.get(row["tier"], 3), _neg_date(row["posted"]))


def _neg_date(iso_date: str) -> str:
    # Sort newest first inside an ascending sort by inverting each digit.
    return "".join(chr(ord("9") - ord(c) + ord("0")) if c.isdigit() else c for c in iso_date) or "~"


def _md(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _md_table(rows: list[dict]) -> list[str]:
    out = ["| Score | New | Posted | Role | Company | Location | Lang | Source |",
           "|---:|:---:|---|---|---|---|---|---|"]
    for r in rows:
        score = "" if r["score"] is None else f"**{r['score']}**" if r["priority"] == "now" else str(r["score"])
        company = _md(r["company"]) + (f" · {TIER_LABEL[r['tier']]}" if TIER_LABEL.get(r["tier"]) else "")
        out.append(
            f"| {score} | {'🆕' if r['is_new'] else ''} | {r['posted']} | "
            f"[{_md(r['title'])}]({r['url']}) | {company} | {_md(r['location'])[:40]} | "
            f"{LANG_FLAG.get(r['lang'], '')} | {r['source']} |"
        )
    return out


def write_markdown(path: Path, rows: list[dict], *, since: str, cutoff: str, stats: dict,
                   briefing: str, candidates: list, trace_link: str | None) -> None:
    today = date.today().isoformat()
    scored = [r for r in rows if r["score"] is not None]
    lines = [
        f"# AI jobs in Germany, the Netherlands and Dublin: {today}",
        "",
        f"{len(rows)} open roles in this run, {sum(r['is_new'] for r in rows)} new since the last run, "
        f"{len(scored)} scored. New-this-window means posted on or after {cutoff} ({since}).",
        "",
    ]
    if trace_link:
        lines += [f"Langfuse trace: {trace_link}", ""]

    if briefing:
        lines += ["## Agent briefing", "", briefing.strip(), ""]

    top = [r for r in scored if r["priority"] == "now"][:12]
    if top:
        lines += ["## Apply now", ""] + _md_table(top) + [""]

    for region in REGION_ORDER:
        region_rows = [r for r in rows if r["region"] == region]
        if not region_rows:
            continue
        recent = [r for r in region_rows if r["recent"]]
        older = [r for r in region_rows if not r["recent"]]
        lines += [f"## {REGION_LABELS[region]} ({len(region_rows)})", ""]
        if recent:
            lines += [f"### Posted in the last {since} ({len(recent)})", ""] + _md_table(recent) + [""]
        if older:
            lines += [f"### Still open at watchlist companies, posted earlier ({len(older)})", ""]
            lines += _md_table(older) + [""]

    detailed = scored[:TOP_DETAILS]
    if detailed:
        lines += ["## Assessments for the top matches", ""]
        for r in detailed:
            lines += [
                f"### {r['score']} · {_md(r['title'])} at {_md(r['company'])}",
                "",
                f"{r['location']} · posted {r['posted'] or 'unknown'} · {r['seniority']} · "
                f"{r['employer_type']} · [{r['job_key']}]({r['url']})",
                "",
                f"**Why:** {r['why_fit']}",
                "",
            ]
            if r["must_haves"]:
                lines.append("**Must-haves:** " + "; ".join(r["must_haves"]))
            if r["gaps"]:
                lines.append("**Gaps:** " + "; ".join(r["gaps"]))
            if r["salary"]:
                lines.append(f"**Salary:** {r['salary']}")
            lines += ["", "**Pitch:**", "", *[f"> {line}" for line in r["pitch"].splitlines() if line.strip()], ""]

    if candidates:
        lines += ["## Companies the agent proposes adding to the watchlist", "",
                  "| Company | ATS | Slug | Regions | Why | Careers |", "|---|---|---|---|---|---|"]
        for c in candidates:
            lines.append(f"| {_md(c['name'])} | {c['ats']} | {c['slug']} | {c['regions']} | "
                         f"{_md(c['reason'])} | {c['careers_url']} |")
        lines.append("")

    src = stats.get("sources", {})
    lines += ["## Run details", "",
              f"- Sources: {json.dumps(src)}",
              f"- Failed sources: {', '.join(stats.get('failed_sources', [])) or 'none'}",
              f"- Scoring: {json.dumps(stats.get('scoring', {}))}",
              f"- Agent: {json.dumps(stats.get('agent', {}))}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


EXCEL_COLUMNS = [
    ("Region", 12), ("Score", 7), ("Priority", 9), ("New", 6), ("Posted", 11), ("Title", 45),
    ("Company", 22), ("Tier", 10), ("Location", 28), ("Local language", 14), ("Visa", 18),
    ("Salary", 20), ("Why fit", 60), ("Source", 10), ("Job key", 22), ("Link", 50),
]


def write_excel(path: Path, rows: list[dict], candidates: list) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append([c for c, _ in EXCEL_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for r in rows:
        ws.append([
            REGION_LABELS.get(r["region"], r["region"]), r["score"], r["priority"],
            "yes" if r["is_new"] else "", r["posted"], r["title"], r["company"],
            TIER_LABEL.get(r["tier"], ""), r["location"], r["lang"], r["visa"], r["salary"],
            r["why_fit"], r["source"], r["job_key"], r["url"],
        ])
        link = ws.cell(row=ws.max_row, column=len(EXCEL_COLUMNS))
        if r["url"]:
            link.hyperlink = r["url"]
            link.style = "Hyperlink"
        ws.cell(row=ws.max_row, column=13).alignment = Alignment(wrap_text=True, vertical="top")
    for i, (_, width) in enumerate(EXCEL_COLUMNS, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    if candidates:
        cs = wb.create_sheet("Company candidates")
        cs.append(["Company", "ATS", "Slug", "Regions", "Reason", "Careers URL", "Suggested"])
        for cell in cs[1]:
            cell.font = Font(bold=True)
        for c in candidates:
            cs.append([c["name"], c["ats"], c["slug"], c["regions"], c["reason"],
                       c["careers_url"], c["suggested_at"][:10]])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def print_console(rows: list[dict], console: Console, limit: int = 20) -> None:
    table = Table(title="Top jobs", box=box.ROUNDED, header_style="bold cyan", show_lines=False)
    for col, kw in [("#", {"justify": "right", "style": "dim"}), ("Score", {"justify": "right"}),
                    ("New", {"justify": "center"}), ("Region", {}), ("Posted", {"style": "dim"}),
                    ("Title", {"style": "bold white", "max_width": 44}),
                    ("Company", {"style": "yellow", "max_width": 22}),
                    ("Job key", {"style": "dim", "max_width": 24})]:
        table.add_column(col, **kw)
    for i, r in enumerate(rows[:limit], 1):
        score = "" if r["score"] is None else str(r["score"])
        style = "bold green" if r["priority"] == "now" else ""
        table.add_row(str(i), f"[{style}]{score}[/]" if style else score, "🆕" if r["is_new"] else "",
                      r["region"], r["posted"], r["title"], r["company"], r["job_key"])
    console.print(table)
