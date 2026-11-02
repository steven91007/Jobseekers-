#!/usr/bin/env python3
"""apply.py - step status, fit gate, tracker payload and summary for /apply (stdlib, WSL python3).

  status  <JD|dir> [--from N] [--sheet] [--threshold 50]   which step outputs exist in $D + the next step
  gate    <JD|dir> [--threshold 50] [--json]                library fit of jd.md (morning.py fit): PASS / STOP
  tracker <JD|dir> [--url URL]                              print the Notion 投遞紀錄 prompt + the 投遞表單 row JSON
  summary <JD|dir> [--out PATH] [--force] [--print] [--sheet]   assemble $D/summary.md (<= 25 lines)

<JD|dir> = a JD code from `library_db.py status`, an application dir (its v3_cards/ is used when it holds the
recorded CV), a jd.md path, or a company code matched against cv/applications/*_<Co>_*.

Read-only everywhere: the experience DB is opened mode=ro, the pipeline ledger is only read, --sheet reads the
tracker Sheet through sheets_api.py without a flush. The only file it writes is summary.md (summary command), and it
refuses to overwrite a summary.md it did not write (a standalone /tailor-cv report) unless --force.
Exit codes: gate 0 = PASS, 1 = STOP, 3 = cannot judge (fit null); status/summary 0; usage errors 2.
Cannot judge: interactive → ask the user (status shows `warn`); headless (--headless or CLAUDE_SCHEDULED=1) → STOP with
"cannot judge" in summary.md. A TTY test is not used: Claude's Bash tool never has one, so every run would look headless.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SK = ROOT / ".claude" / "skills"
DB = ROOT / "cv" / "profile" / "experience.sqlite"
APPS = ROOT / "cv" / "applications"
LETTERS = ROOT / "cv" / "cover_letters"
LEDGERS = Path(os.environ.get("PIPELINE_DATA") or ROOT / "data" / "pipeline") / "runs"
TEMPLATE_MD = ROOT / "cv" / "template" / "CV_template_v3.md"
MARK = "<!-- written by .claude/skills/apply/scripts/apply.py summary -->"
THRESHOLD = 50   # 2026-11-02 calibration: at 60 three of the ten applications the user chose would stop (AIVenture 53, Mapbox 55, Taktile 49)
for p in (SK / "morning-run" / "scripts", SK / "tailor-cv" / "scripts"):
    sys.path.insert(0, str(p))
import morning as M  # noqa: E402  (fit, ro_connect, url_key, norm_co: read-only helpers)


# ---------------------------------------------------------------- helpers
def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def con() -> sqlite3.Connection:
    return M.ro_connect(DB)


def app_path(d: str) -> Path:
    p = Path(d)
    if p.is_absolute():
        return p
    return ROOT / d if (ROOT / d).is_dir() else ROOT / "cv" / d


def short(s: str, n: int = 90) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def rel(p: Path | None) -> str:
    if not p:
        return "-"
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------- resolve the application
def pick_dir(p: Path, rows: list[sqlite3.Row]) -> Path:
    """An application top dir -> the recorded dir inside it (v3_cards/), else v3_cards/ if it has jd.md, else itself."""
    for r in rows:
        d = app_path(r["dir"])
        if d.resolve() in (p.resolve(), (p / "v3_cards").resolve()):
            return d
    return p / "v3_cards" if (p / "v3_cards" / "jd.md").exists() else p


def resolve(arg: str) -> tuple[Path, sqlite3.Row | None]:
    c = con()
    rows = c.execute("SELECT * FROM applications").fetchall()
    p = Path(arg)
    cands = [p] if p.is_absolute() else [Path.cwd() / p, ROOT / p]
    hit = next((x for x in cands if x.exists()), None)
    if hit is not None:
        d = pick_dir(hit.parent if hit.is_file() else hit, rows)
    else:
        row = c.execute("SELECT * FROM applications WHERE jd=? COLLATE NOCASE", (arg,)).fetchone()
        if row:
            return app_path(row["dir"]), row
        dirs = sorted(APPS.glob(f"*_{arg}_*"), reverse=True)
        if not dirs:
            sys.exit(f"apply.py: no application for {arg!r} (not a dir, not a JD code in library_db.py status, "
                     f"no cv/applications/*_{arg}_*)")
        d = pick_dir(dirs[0], rows)
    row = next((r for r in rows if app_path(r["dir"]).resolve() == d.resolve()), None)
    return d, row


def jd_meta(d: Path) -> dict:
    t = read(d / "jd.md")
    get = lambda *keys: next((m.group(1).strip() for k in keys
                              for m in [re.search(rf"^\s*-\s*\**{k}[:*]*\**:?\**\s*(.+)$", t, re.M | re.I)] if m), "")
    url = re.search(r"https?://[^\s)>\]]+", get("Source URL", "Source") or t)
    head = re.match(r"#\s*(.+?)\s+[—-]\s+(.+)", t.strip().splitlines()[0]) if t.strip() else None
    loc = get("Location / remote", "Location")
    mode = ("Remote" if re.search(r"\bremote\b", loc, re.I) and not re.search(r"not remote", loc, re.I)
            else "Hybrid" if re.search(r"hybrid", loc, re.I) else "On-site" if re.search(r"on-?site|office", loc, re.I) else "")
    company = re.sub(r"\s*\(.*?\)", "", get("Company") or (head.group(2) if head else "")).strip()
    return {"url": url.group(0).rstrip(".,") if url else "", "company": company,
            "title": get("Role") or (head.group(1) if head else ""), "location": loc, "mode": mode,
            "city": city_of(loc)}


def city_of(loc: str) -> str:
    """'Otto-Hahn-Straße, 56218 Mülheim-Kärlich, RP, Germany — hybrid' -> 'Mülheim-Kärlich'; 'Cologne, NRW, DE · Remote' -> 'Cologne'."""
    parts = [x.strip() for x in re.split(r"[,·—(]", loc or "") if x.strip()]
    for x in parts:
        m = re.match(r"\d{4,5}\s+(.+)", x)
        if m:
            return short(m.group(1), 40)
    return short(parts[0], 40) if parts else ""


def code_of(d: Path, row) -> str:
    if row:
        return row["jd"]
    top = d.parent if d.name == "v3_cards" else d
    m = re.match(r"\d{8}_([^_]+)_", top.name)
    return m.group(1) if m else top.name


# ---------------------------------------------------------------- per-step facts
def gate_result(d: Path, threshold: int) -> dict:
    t = read(d / "jd.md")
    if not t.strip():
        return {"state": "todo", "fit": None, "missing": [], "text": "no jd.md"}
    r = M.fit(t, con())
    miss = [M.missing_label(i) for i in r["missing"][:5]]
    if r["fit"] is None:
        state = "thin"
    else:
        state = "pass" if r["fit"] >= threshold else "stop"
    return {"state": state, "fit": r["fit"], "missing": miss, "missing_names": [i["name"] for i in r["missing"][:3]],
            "covered": [i["name"] for i in r["covered"][:5]], "threshold": threshold, "lines": r["lines"]}


def ledger_entry(d: Path) -> dict:
    """The daily pipeline's record for this dir (Notion 投遞紀錄 page, 投遞表單 row, Drive links), newest first."""
    for f in sorted(LEDGERS.glob("*.json"), reverse=True):
        try:
            jobs = json.loads(read(f)).get("jobs", {})
        except ValueError:
            continue
        for key, j in jobs.items():
            ad = (j.get("cv") or {}).get("app_dir")
            if ad and app_path(ad).resolve() in (d.resolve(), d.parent.resolve()):
                return {"ledger": f.name, "key": key, **j}
    return {}


def letter_dir(d: Path, code: str, led: dict) -> Path | None:
    cd = (led.get("cv") or {}).get("cover_dir")
    if cd and (ROOT / cd).is_dir():
        return ROOT / cd
    top = d.parent if d.name == "v3_cards" else d
    stem = re.sub(r"^\d{8}_", "", top.name)
    found = []
    for x in LETTERS.glob("*"):
        if not x.is_dir():
            continue
        lc = read(x / "letter_check.json")
        kw = ""
        if lc:
            try:
                kw = json.loads(lc).get("keywords_file") or ""
            except ValueError:
                pass
        same = kw and Path(kw).parent.resolve() == d.resolve()
        if same or re.sub(r"_v\d+$", "", re.sub(r"^\d{8}_", "", x.name)) == stem:
            found.append((bool(list(x.glob("CoverLetter_*.pdf"))), x.name[:8], x.name.endswith(tuple(f"_v{i}" for i in range(2, 9))), x))
    return max(found, key=lambda t: t[:3])[3] if found else None


def cv_summary_changed(d: Path) -> bool:
    lines = read(d / "03_cv.md").splitlines()
    tpl = read(TEMPLATE_MD).splitlines()
    return len(lines) > 4 and (len(tpl) <= 4 or lines[4].strip() != tpl[4].strip())


def sheet_rows(meta: dict, d: Path, company: str, title: str) -> list[dict]:
    """Tracker rows for this job (投遞表單 · 求職追蹤表_AI 投遞 · 求職追蹤表), read-only, no flush()."""
    sys.path.insert(0, str(SK / "daily-job-tracker" / "scripts"))
    import sheets_api as S
    b = S.Backend.from_config()
    bk = b.book.load([S.MAIN_NAME, S.AUTO_NAME, S.SUBMIT_NAME])
    want_url = M.url_key(meta.get("url") or "") if meta.get("url") else None
    want_ct = M.norm_co(company) + "|" + title.casefold()
    out = []
    for name in (S.SUBMIT_NAME, S.AUTO_NAME, bk.main_title):
        t = bk.tab(name)
        if t is None or not t.grid:
            continue
        h = S.headers(t)
        c = h["cols"]
        for r in range(h["row"] + 1, S.last_data_row(t, h) + 1):
            get = (lambda k: S.disp(t.cell(r, c[k])).strip() if k in c else "")
            pos = t.cell(r, c["Position"]) if "Position" in c else None
            url = (S.link(pos) if pos else "") or get("連結")
            ad = get("應用資料夾")
            ok = (want_url and url and M.url_key(url) == want_url) or \
                 (M.norm_co(get("公司")) + "|" + (S.disp(pos).strip().casefold() if pos else "") == want_ct) or \
                 (ad and app_path(ad).resolve() in (d.resolve(), d.parent.resolve()))
            if ok:
                cv = t.cell(r, c["履歷版本"]) if "履歷版本" in c else None
                nc = t.cell(r, c["Notion"]) if "Notion" in c else None
                notion = (S.link(nc) if nc else "") or (S.link(cv) if cv else "") or ""
                out.append({"tab": name, "row": r, "status": get("狀態"), "notion": notion if notion.startswith("http") else "",
                            "process": get("求職過程")[-120:]})
    return out


def headless(a=None) -> bool:
    return bool(getattr(a, "headless", False)) or os.environ.get("CLAUDE_SCHEDULED") == "1"


def collect(d: Path, row, threshold: int = THRESHOLD, sheet: bool = False, unattended: bool = False) -> dict:
    c = con()
    code = code_of(d, row)
    meta = jd_meta(d)
    led = ledger_entry(d)
    f = lambda n: (d / n).exists()
    info = {"dir": d, "code": code, "row": row, "meta": meta, "ledger": led,
            "company": row["company"] if row else meta["company"], "title": row["title"] if row else meta["title"]}
    steps = []

    def add(n, name, state, detail, hint=""):
        steps.append({"n": n, "name": name, "state": state, "detail": detail, "hint": hint})

    add("0", "JD (jd.md)", "done" if f("jd.md") else "todo", "jd.md" if f("jd.md") else "missing",
        "tailor-cv step 0: WebFetch / file / text → $D/jd.md")
    # 1 company research
    cj, cm = f("02_company.json"), f("02_company.md")
    det = "02_company.json + .md"
    if cj:
        try:
            cr = json.loads(read(d / "02_company.json"))
            det = f"{len(cr.get('hooks') or [])} hooks · {len(cr.get('news') or [])} news · {len(cr.get('sources') or [])} sources"
            info["company_research"] = cr
        except ValueError:
            det = "02_company.json unreadable"
    add("1", "Company research", "done" if cj and cm else "partial" if cj else "todo",
        det if cj else "missing (not a blocker for later steps)",
        "/company-research: company_research.py check → render" if cj else "/company-research <Co> (steps 1-5)")
    # 2 brief + keywords
    b1, b2 = f("01_jd.md"), f("07_job_keywords.json")
    add("2", "JD brief + keywords", "done" if b1 and b2 else "partial" if b1 or b2 else "todo",
        " + ".join(x for x, ok in (("01_jd.md", b1), ("07_job_keywords.json", b2)) if ok) or "missing",
        "tailor-cv step 1: brief, keywords JSON, normalize-keywords --write, match, gaps")
    # gate
    g = gate_result(d, threshold)
    info["gate"] = g
    cv_started = f("03_cv.md")
    if g["state"] == "pass":
        gs, gd = "done", f"PASS fit {g['fit']}% ≥ {threshold}%"
    elif g["state"] == "stop":
        gs, gd = ("override" if cv_started else "stop"), f"fit {g['fit']}% < {threshold}% · skip: " + "; ".join(g["missing_names"])
    elif g["state"] == "thin":   # fit null: interactive → ask the user; headless → stop (never read as 0 %)
        if cv_started:
            gs, gd = "override", "cannot judge (fit null, fewer than 8 scored items)"
        elif unattended:
            gs, gd = "stop", "cannot judge (fit null, fewer than 8 scored items) · headless run: stop, the user decides"
        else:
            gs, gd = "warn", "cannot judge (fit null, fewer than 8 scored items): ask the user before step 1"
    else:
        gs, gd = "todo", "needs jd.md"
    add("G", "Fit gate", gs, gd + (" · user went on (CV exists)" if gs == "override" else ""),
        "stop and log the skip; continue only if the user says go" if gs == "stop" else
        "ask the user: go or skip" if gs == "warn" else "")
    # 3 CV
    npicks = c.execute("SELECT count(*) FROM application_picks WHERE jd=?", (code,)).fetchone()[0] if row else 0
    pdf = sorted(d.glob("CV_*.pdf"))
    sub = [("picks recorded", npicks > 0, "tailor-cv step 2: pick → align → fit → record"),
           ("03_cv.md", f("03_cv.md"), "tailor-cv step 3: assemble_cv.py --jd <Co>"),
           ("Summary", cv_summary_changed(d), "tailor-cv step 4: /multi-writer summary"),
           ("PDF + 06_layout.json", bool(pdf) and f("06_layout.json"), "tailor-cv step 5: build_cv.sh patch + check"),
           ("07_external_ats.json", f("07_external_ats.json"), "tailor-cv step 5: build_cv.sh ats + ingest-ats"),
           ("04_review.md", f("04_review.md"), "tailor-cv step 6: Codex review")]
    miss = [s for s in sub if not s[1]]
    stale = pdf and f("03_cv.md") and (d / "03_cv.md").stat().st_mtime > max(p.stat().st_mtime for p in pdf) + 1
    det = "all outputs" if not miss else "missing: " + ", ".join(s[0] for s in miss)
    if stale:
        det += " · PDF older than 03_cv.md (rebuild, step 5)"
    det += " · 09_sot_leads.json" + (" yes" if f("09_sot_leads.json") else " no (step 7 optional)")
    info["pdf"] = pdf[-1] if pdf else None
    add("3", "CV (tailor-cv 2-7)", "done" if not miss and not stale else "partial" if len(miss) < len(sub) else "todo",
        det, (miss[0][2] if miss else "tailor-cv step 5: rebuild") if miss or stale else "")
    # 4 cover letter
    ld = letter_dir(d, code, led)
    info["letter_dir"] = ld
    lpdf = sorted(ld.glob("CoverLetter_*.pdf")) if ld else []
    lrev = ld and (ld / "review.md").exists()
    info["letter_pdf"] = lpdf[-1] if lpdf else None
    add("4", "Cover letter", "done" if lpdf and lrev else "partial" if ld else "todo",
        (rel(ld) + (" · PDF" if lpdf else " · no PDF") + (" · review.md" if lrev else " · no review.md")) if ld else "no cv/cover_letters dir",
        "" if lpdf and lrev else "/cover-letter (uses $APP = this dir)")
    # 5 interview
    ij, im = f("10_interview.json"), f("10_interview.md")
    nqa = c.execute("SELECT count(*) FROM interview_qa WHERE jd=?", (code,)).fetchone()[0] if row else 0
    todo = 0
    if ij:
        try:
            qs = json.loads(read(d / "10_interview.json"))
            todo = sum(1 for q in qs if "TODO candidate" in (q.get("answer") or ""))
            info["interview"] = {"n": len(qs), "ask": sum(1 for q in qs if q.get("kind") == "ask"), "todo": todo, "qa": nqa}
        except ValueError:
            pass
    add("5", "Interview prep", "done" if ij and im and nqa else "partial" if ij else "todo",
        f"10_interview.json{' + .md' if im else ''} · {nqa} Q&A rows · {todo} answers with TODO candidate" if ij else "missing",
        "" if ij and im and nqa else ("interview_prep.py check → render → qa import" if ij else "/interview-prep <Co>"))
    # 6 outreach (optional: needs a named person)
    drafts = sorted(d.glob("11_outreach_*.md"))
    orow = c.execute("SELECT kind, person, status FROM outreach WHERE jd=? ORDER BY id", (code,)).fetchall() if row else []
    info["outreach"] = {"drafts": [p.name for p in drafts], "rows": [dict(r) for r in orow]}
    add("6", "Outreach drafts", "done" if drafts else "optional",
        (", ".join(re.sub(r"^11_outreach_|\.md$", "", p.name) for p in drafts) + f" · {len(orow)} DB rows" +
         (f" ({', '.join(sorted({r['status'] for r in orow}))})" if orow else " (not recorded: outreach.py record)")) if drafts else "none (connect-note needs a known hiring manager; referral-letter needs a named referrer)",
        "outreach.py record (status draft)" if drafts and not orow else ("" if drafts else "/outreach <Co> connect-note|referral-letter, only with a named person"))
    # 7 tracker
    notion = ((led.get("notion") or {}).get("page_url") or "") if led else ""
    srow = (led.get("submission") or {}).get("row") if led else None
    trk = {"record": bool(row), "notion": notion, "sheet_row": srow, "sheet": None}
    if sheet:
        try:
            rows = sheet_rows(meta, d, info["company"], info["title"])
            trk["sheet"] = rows
            if rows:
                srow = srow or next((f"投遞表單!{r['row']}" for r in rows if r["tab"] == "投遞表單"), None) or f"{rows[0]['tab']}!{rows[0]['row']}"
                notion = notion or next((r["notion"] for r in rows if r["notion"]), "")
                trk.update(notion=notion, sheet_row=srow)
        except Exception as e:  # no token / offline: say so, keep going
            trk["sheet_error"] = short(str(e), 80)
    info["tracker"] = trk
    have = [("record", trk["record"]), ("Notion 投遞紀錄", bool(notion)), ("Sheet row", bool(srow))]
    det = " · ".join(f"{k} {'yes' if v else 'no'}" for k, v in have)
    if not sheet and not led:
        det += " (no pipeline ledger entry; --sheet to look the row up)"
    if trk.get("sheet_error"):
        det += f" · Sheet not read: {trk['sheet_error']}"
    add("7", "Tracker", "done" if all(v for _, v in have) else "partial" if any(v for _, v in have) else "todo", det,
        "" if all(v for _, v in have) else "apply.py tracker → Notion row + push_to_sheet.py --live --submissions-append")
    # 8 human gate
    st = [r["status"] for r in (trk["sheet"] or []) if r["status"]]
    sent = [s for s in st if s and s != "準備中"]
    info["status"] = st[0] if st else None
    add("8", "Human gate", "done" if sent else "user",
        f"狀態 {', '.join(dict.fromkeys(st))}" if st else ("狀態 unknown (status --sheet)" if not sheet else "no tracker row found"),
        "" if sent else "user reviews CV / CL / outreach, then marks 履歷送出 (or asks job-apply to fill the form, with approval)")
    # 9 summary
    s = read(d / "summary.md")
    add("9", "summary.md", "done" if MARK in s else "legacy" if s else "todo",
        "apply.py summary" if MARK in s else ("standalone tailor-cv report (kept; summary needs --force or --out)" if s else "missing"),
        "" if MARK in s else "apply.py summary <dir>")
    info["steps"] = steps
    return info


ORDER = ["0", "1", "2", "G", "3", "4", "5", "6", "7", "8", "9"]
BLOCKING = {"todo", "partial", "stop", "legacy"}


def next_step(steps: list[dict], start: str | None = None) -> dict | None:
    gate8 = next((s for s in steps if s["n"] == "8"), None)
    gate = next((s for s in steps if s["n"] == "G"), None)
    if not start and gate and gate["state"] == "stop":   # the gate needs only jd.md: a STOP wins over earlier gaps
        return gate
    if not start and gate8 and gate8["state"] == "done":   # already sent: earlier gaps are no longer the next action
        left = [s["n"] for s in steps if s["state"] in BLOCKING and s["n"] not in ("9",)]
        return {**gate8, "state": "sent", "hint": f"already sent ({gate8['detail']}); open steps {', '.join(left) or 'none'} "
                "only matter for the next stage (interview prep, follow-up)"}
    for s in steps:
        if start and ORDER.index(s["n"]) < ORDER.index(start):
            continue
        if start and ORDER.index(s["n"]) >= ORDER.index(start) and s["n"] not in ("G", "8"):
            return {**s, "hint": s["hint"] or f"re-run step {s['n']} (--from {start})"}
        if s["state"] in BLOCKING and not (s["n"] == "9" and s["state"] == "legacy"):
            return s
        if s["n"] == "8" and s["state"] == "user":
            return s
    return None


# ---------------------------------------------------------------- commands
def cmd_status(a) -> int:
    d, row = resolve(a.target)
    info = collect(d, row, a.threshold, a.sheet, headless(a))
    print(f"# /apply status · {info['company']} · {info['title']} (JD `{info['code']}`)")
    print(f"$D = {rel(d)}" + ("" if row else "  (no applications row yet: library_db.py record has not run)"))
    print()
    print("| # | Step | State | Detail |")
    print("|---|---|---|---|")
    for s in info["steps"]:
        mark = s["state"]
        if a.from_ and s["n"] not in ("G", "8") and ORDER.index(s["n"]) >= ORDER.index(a.from_):
            mark += " → redo"
        print(f"| {s['n']} | {s['name']} | {mark} | {s['detail']} |")
    nx = next_step(info["steps"], a.from_)
    print()
    if info["gate"]["state"] == "thin" and any(s["n"] == "G" and s["state"] == "warn" for s in info["steps"]):
        print("GATE: cannot judge the fit → ask the user first (go or skip); the NEXT line applies on go.")
    if not nx:
        print("NEXT: nothing left. The candidate sends the application by hand.")
    elif nx["state"] == "sent":
        print(f"NEXT: {nx['hint']}.")
    elif nx["state"] == "stop" and info["gate"]["state"] == "thin":
        print("NEXT: STOP at the fit gate (cannot judge, headless). Write summary.md (apply.py summary --headless); "
              "the user decides go or skip.")
    elif nx["state"] == "stop":
        print(f"NEXT: STOP at the fit gate. Log \"skip: {'; '.join(info['gate']['missing_names'])}\" in summary.md "
              f"(apply.py summary) unless the user says go.")
    else:
        print(f"NEXT: step {nx['n']} {nx['name']} — {nx['hint']}")
    return 0


def cmd_gate(a) -> int:
    d, _ = resolve(a.target)
    g = gate_result(d, a.threshold)
    if a.json:
        print(json.dumps({k: v for k, v in g.items()}, ensure_ascii=False, indent=1))
    elif g["state"] == "todo":
        print(f"GATE: no jd.md in {rel(d)}")
        return 2
    elif g["state"] == "thin":
        print(f"GATE: CANNOT JUDGE (fit null: fewer than 8 scored items, {g['lines']} requirement lines) → "
              + ("headless run: STOP, write summary.md with \"cannot judge\"; the user decides" if headless(a)
                 else "ask the user: go or skip (never read null as 0 %)"))
        print("top missing: " + ("; ".join(g["missing"]) or "-"))
    else:
        verdict = "PASS" if g["state"] == "pass" else "STOP"
        print(f"GATE: {verdict} · fit {g['fit']}% {'≥' if verdict == 'PASS' else '<'} {a.threshold}% ({g['lines']} requirement lines)")
        print("top missing: " + ("; ".join(g["missing"]) or "-"))
        print("strongest covered: " + (", ".join(g["covered"]) or "-"))
        if verdict == "STOP":
            print(f"log: skip: {'; '.join(g['missing_names'])}  (continue only if the user says go)")
    return {"pass": 0, "stop": 1, "thin": 3}.get(g["state"], 2)


def cmd_tracker(a) -> int:
    d, row = resolve(a.target)
    info = collect(d, row, sheet=not a.no_sheet)
    meta = info["meta"]
    url = a.url or meta["url"]
    if not url:
        sys.exit("apply.py tracker: no job URL in jd.md; pass --url")
    sys.path.insert(0, str(SK / "daily-job-tracker" / "scripts"))
    import daily_pipeline as P  # noqa: E402  (prompt builder only; nothing runs at import)
    ats = None
    if (d / "07_external_ats.json").exists():
        ats = json.loads(read(d / "07_external_ats.json")).get("ats_score", {}).get("overall_score")
    country = row["country"] if row and row["country"] else ""
    job = {"company": info["company"], "title": info["title"], "url": url, "city": meta["city"], "country": country,
           "mode": meta["mode"] or "未註明"}
    cv = {"cv_pdf": rel(info["pdf"]) if info.get("pdf") else "", "ats": ats, "app_dir": rel(d),
          "cover_letter_pdf": rel(info["letter_pdf"]) if info.get("letter_pdf") else "",
          "company": info["company"], "title": info["title"]}
    today = dt.date.today().isoformat()
    prompt = P.notion_row_prompt(job, cv, today, {}, [])
    prompt = prompt.replace("每日流程自動產生。", "/apply 產生。")
    sheet_row = {"date": today, "company": info["company"], "title": info["title"], "url": url, "type": "",
                 "source": "/apply", "city": meta["city"], "country": country, "mode": job["mode"], "ats": ats,
                 "requirements": [], "notion": "<page_url of the new 投遞紀錄 row>",
                 "has_cover_letter": bool(cv["cover_letter_pdf"]), "cv": cv["cv_pdf"], "cover_letter": cv["cover_letter_pdf"],
                 "app_dir": rel(d), "referrers": [], "referral_message": "", "referral_data": []}
    t = info["tracker"]
    print(f"# Tracker payload · {info['company']} · {info['title']} (prints only; nothing is written)")
    print(f"record: {'yes' if t['record'] else 'NO → library_db.py record first'} · Notion 投遞紀錄: {t['notion'] or 'none'} · "
          f"Sheet row: {t['sheet_row'] or 'none'}")
    if t["notion"] and t["sheet_row"]:
        print("Both tracker rows already exist (daily pipeline or an earlier run). Do not create duplicates.")
        return 0
    print("\n## 1. Notion 投遞紀錄 row (main agent, Notion MCP)\n"
          f"First query 投遞紀錄 (collection://{P.APPLICATIONS_DS}) for 職缺連結 = {url} and for 公司 = "
          f"{info['company']} (notion-query-data-sources / notion-search). A hand-made row exists → do not create one;\n"
          "use its URL below. Otherwise create it:\n")
    print(prompt)
    print("\n## 2. Sheet 投遞表單 row: replace \"notion\" with the new page URL, then\n"
          "##    python3 .claude/skills/daily-job-tracker/scripts/push_to_sheet.py --live --submissions-append -\n")
    print(json.dumps([sheet_row], ensure_ascii=False, indent=1))
    return 0


def first(rx: str, text: str, default: str = "-") -> str:
    m = re.search(rx, text)
    return m.group(1) if m else default


def build_summary(info: dict) -> str:
    d, code = info["dir"], info["code"]
    c = con()
    g = info["gate"]
    steps = {s["n"]: s for s in info["steps"]}
    L = [MARK, f"# /apply · {info['company']} · {info['title']} (JD `{code}`) · {dt.date.today().isoformat()}"]
    L.append(f"Dir: `{rel(d)}`" + (f" · posting: {info['meta']['url']}" if info["meta"]["url"] else ""))
    if g["state"] == "thin" and steps["G"]["state"] == "stop":
        L.append("**STOP**: cannot judge the library fit (fit null, too few scored items) in a headless run. "
                 "Nothing else was built.")
        L.append("Next action: the user decides go (→ /apply --from 1) or skip.")
        return "\n".join(L) + "\n"
    if g["state"] == "thin":
        L.append("Fit gate: cannot judge (fit null, too few scored items); " +
                 ("the user said go" if steps["G"]["state"] == "override" else "ask the user before going on"))
    if g["fit"] is not None:
        L.append(f"Fit gate: {'PASS' if g['state'] == 'pass' else 'below threshold'} · fit {g['fit']}% (threshold {g['threshold']}%) · "
                 f"top missing: {'; '.join(g['missing_names']) or '-'}" + (" · user went on" if steps['G']['state'] == 'override' else ""))
    if steps["G"]["state"] == "stop":
        L.append(f"**SKIP**: {'; '.join(g['missing_names'])}. Nothing else was built. Say \"go\" to continue anyway.")
        L.append("Next action: none (skipped), or say go → /apply --from 3.")
        return "\n".join(L) + "\n"
    cr = info.get("company_research")
    if cr:
        hooks = "; ".join(f"{'+'.join(h.get('concepts') or [])} → {','.join(h.get('bullets') or [])}" for h in (cr.get("hooks") or []))
        risk = next((r["text"] for r in cr.get("risks") or [] if r.get("kind") == "risk"), "")
        L.append(f"Company: {steps['1']['detail']} · hooks: {short(hooks, 110)}")
        if risk:
            L.append(f"Top risk: {short(risk, 120)}")
    else:
        L.append(f"Company research: {steps['1']['state']} ({steps['1']['detail']})")
    if info["row"]:
        pk = c.execute("SELECT kind, code, lead FROM application_picks WHERE jd=? ORDER BY position", (code,)).fetchall()
        cards = [p["code"] for p in pk if p["kind"] == "card"]
        bullets = [p["code"] for p in pk if p["kind"] == "bullet"]
        if pk:
            L.append(f"CV picks: cards {', '.join(cards)} · bullets {', '.join(bullets)} · headline: {short(info['row']['headline'], 70)}")
    ats = json.loads(read(d / "07_external_ats.json")) if (d / "07_external_ats.json").exists() else None
    lay = json.loads(read(d / "06_layout.json")) if (d / "06_layout.json").exists() else None
    rv = read(d / "04_review.md")
    if ats or lay or rv:
        km = (ats or {}).get("keyword_match") or {}
        util = next((ch["detail"] for ch in (lay or {}).get("checks", []) if ch.get("check") == "page_utilization"), "")
        L.append(f"ATS: {(ats or {}).get('ats_score', {}).get('overall_score', '-')} · keywords "
                 f"{len(km.get('found') or []) if km else '-'}/{km.get('n_terms', '-')} · layout {(lay or {}).get('status', '-')}"
                 + (f" ({first(r'(\d+%)', util)} of the page)" if util else "")
                 + f" · review HR {first(r'\bHR (\d+)', rv)} / HM {first(r'\bHM (\d+)', rv)} · verdict {first(r'VERDICT:\s*\**([A-Z][A-Z -]*[A-Z])', rv)}")
    if steps["3"]["state"] != "done":
        L.append(f"CV step: {steps['3']['state']} · {steps['3']['detail']}")
    ld = info.get("letter_dir")
    if ld:
        lr = read(ld / "review.md")
        cov = {}
        try:
            cov = json.loads(read(ld / "letter_check.json")).get("coverage", {}).get("required", {})
        except ValueError:
            pass
        llay = json.loads(read(ld / "layout.json")).get("status", "-") if (ld / "layout.json").exists() else "-"
        L.append(f"Cover letter: PERS {first(r'PERSONALISATION (\d+)', lr)} · HR {first(r'\bHR (\d+)', lr)} · COMPL "
                 f"{first(r'COMPLEMENTARITY (\d+)', lr)} · CONS {first(r'CONSISTENCY (\d+)', lr)} · verdict "
                 f"{first(r'VERDICT:?\s*\**([A-Z]+)', lr)} · required terms CV {cov.get('cv', '-')} → CV+letter "
                 f"{cov.get('union', '-')}/{cov.get('n', '-')} · layout {llay}")
    else:
        L.append("Cover letter: not written yet (/cover-letter)")
    iv = info.get("interview")
    if iv:
        L.append(f"Interview prep: {iv['n']} questions ({iv['ask']} to ask) · {iv['todo']} answers with TODO candidate · "
                 f"{iv['qa']} rows in interview_qa · `10_interview.md`")
    else:
        L.append("Interview prep: not written yet (/interview-prep)")
    o = info["outreach"]
    if o["drafts"]:
        rows = ", ".join(f"{r['kind']} {r['person']} ({r['status']})" for r in o["rows"]) or "not recorded"
        L.append(f"Outreach drafts (never sent by Claude): {', '.join(o['drafts'])} · DB: {short(rows, 90)}")
    else:
        L.append("Outreach: no drafts (no hiring manager or referrer named)")
    t = info["tracker"]
    L.append(f"Tracker: record {'yes' if t['record'] else 'no'} · Notion 投遞紀錄 {t['notion'] or 'no'} · "
             f"Sheet row {t['sheet_row'] or 'no'}" + (f" · 狀態 {info['status']}" if info.get("status") else ""))
    files = [rel(x) for x in (info.get("pdf"), info.get("letter_pdf")) if x]
    if files:
        L.append("Files: " + " · ".join(f"`{x}`" for x in files))
    nx = next_step([s for s in info["steps"] if s["n"] != "9"])
    if nx is None:
        act = "nothing left; the candidate sends the application by hand"
    elif nx["state"] == "sent":
        act = nx["hint"]
    elif nx["n"] == "8":
        act = "review the CV, cover letter and outreach drafts, then mark 履歷送出 in the tracker yourself (or ask job-apply to fill the form, with approval)"
    else:
        act = f"step {nx['n']} {nx['name']}: {nx['hint']}"
    L.append(f"**Next action**: {act}.")
    return "\n".join(L[:25]) + "\n"


def cmd_summary(a) -> int:
    d, row = resolve(a.target)
    info = collect(d, row, a.threshold, a.sheet, headless(a))
    text = build_summary(info)
    out = Path(a.out) if a.out else d / "summary.md"
    if a.print_:
        print(text, end="")
        return 0
    old = read(out)
    if old and MARK not in old and not a.force:
        print(text, end="")
        print(f"\n(not written: {rel(out)} is a standalone report, not an apply.py summary; use --out PATH, or --force "
              f"after the user agrees to replace it)", file=sys.stderr)
        return 0
    out.write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"(wrote {rel(out)}, {len(text.splitlines())} lines)", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "gate", "tracker", "summary"):
        p = sub.add_parser(name)
        p.add_argument("target", help="JD code, application dir or jd.md path")
        p.add_argument("--threshold", type=int, default=THRESHOLD)
        p.add_argument("--headless", action="store_true", help="unattended run (also CLAUDE_SCHEDULED=1): cannot judge = STOP")
        if name == "status":
            p.add_argument("--from", dest="from_", choices=[x for x in ORDER if x not in ("G", "8")])
        if name in ("status", "summary"):
            p.add_argument("--sheet", action="store_true", help="read the tracker Sheet (read-only) for the row and 狀態")
        if name == "gate":
            p.add_argument("--json", action="store_true")
        if name == "tracker":
            p.add_argument("--url")
            p.add_argument("--no-sheet", action="store_true", help="skip the read-only Sheet lookup for an existing row")
        if name == "summary":
            p.add_argument("--out")
            p.add_argument("--force", action="store_true")
            p.add_argument("--print", dest="print_", action="store_true", help="print only, write nothing")
    a = ap.parse_args(argv)
    return {"status": cmd_status, "gate": cmd_gate, "tracker": cmd_tracker, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
