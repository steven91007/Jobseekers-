"""Morning run: new postings ranked by library fit · inbox proposals · interviews · follow-ups due → one digest.

    python3 morning.py fit <jd.txt|jd.md> [--json] [--top 5]       library fit % + top missing concepts (DB read-only)
    python3 morning.py jobs [--date D] [--top 5] [--json]          today's new postings (daily pipeline ledger) by fit
    python3 morning.py followups [--date D] [--days 10] [--json]   tracker rows 履歷送出 with no reply > N days
    python3 morning.py inbox --google [--since D] | <mails.json|-> [--json]   classify mails, map, PROPOSE status
    python3 morning.py calendar --google | <events.json|-> [--date D]        interviews today / this week, link 10_interview.md
    python3 morning.py since                                        date of the last digest (Gmail look-back start)
    python3 morning.py run [--date D] [--top 5] [--connectors -|F] [--inbox F|--no-gmail] [--calendar F|--no-calendar]
                           [--no-sheet] [--pipeline [--yes]] [--dry] [--out PATH]
                                                                    all of the above → cv/digest/YYYY-MM-DD.md + stdout
    python3 morning.py selftest                                     fixtures: classifier, matcher, proposals, fit order

Gmail / Calendar: by default GET-only calls to the Gmail and Calendar APIs through the shared google_auth login
(gmail.readonly, calendar.readonly); claude.ai connector JSON on stdin is the fallback.
Read-only everywhere except the digest file: the experience DB is opened with mode=ro, the Google Sheet is read with
the daily-job-tracker's sheets_api.py without ever flushing a write, the pipeline ledger and logs are only read. It never
sends mail, never creates or answers calendar events, never changes a tracker status — it prints proposals.
The one other write: google_auth.py refreshes ~/.config/ai-job-tracker/google_token.json when the access token expired.
`--pipeline` (opt-in) first runs `daily_pipeline.py --live --only search`, which appends new jobs to the Sheet tab
每日職缺 (that pipeline's normal, append-only write); without it the run reads what the 04:00 task already found.
--pipeline is refused when CLAUDE_SCHEDULED=1 or stdin is not a TTY, unless --yes (explicit user request only).

mails.json  = [{"id", "from", "subject", "date" (ISO), "snippet" | "body", "auto_submitted"?}]   (Gmail connector, via stdin)
events.json = [{"summary", "start" (ISO), "end", "location", "description", "attendees": [..]}]   (Calendar connector)
connectors  = {"mails": [...] | null, "events": [...] | null}   (run --connectors -: null = not connected)
Stdlib only; run with WSL python3. references/: fit_scoring.md, generic_terms.txt, inbox_rules.md, tech_terms.txt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
REPO = SKILL.parents[2]
REFS = SKILL / "references"
LIB = REPO / ".claude" / "skills" / "tailor-cv" / "scripts"
TRACKER = REPO / ".claude" / "skills" / "daily-job-tracker" / "scripts"
DB = Path(os.environ.get("MORNING_DB") or REPO / "cv" / "profile" / "experience.sqlite")
DIGEST = Path(os.environ.get("MORNING_DIGEST_DIR") or REPO / "cv" / "digest")
PIPE_DATA = Path(os.environ.get("PIPELINE_DATA") or REPO / "data" / "pipeline")
TZ = dt.timezone(dt.timedelta(hours=8))  # same day boundary as daily_pipeline.py (Asia/Taipei)
sys.path.insert(0, str(LIB))
import library_db as L  # noqa: E402  (pure functions only: alias_index, resolve_term, has_term, norm_term)
sys.path.insert(0, str(HERE))
import jev_questions as JQ  # noqa: E402

# ---- fit ---------------------------------------------------------------------------------------
# Section weights (references/fit_scoring.md). Same scale as library_db TYPE_W: required 3 / preferred 2 / kw 1,
# with duties between required and preferred, and boilerplate (benefits, about us, EEO) not scored.
SECTION_W = {"required": 3, "duties": 2, "preferred": 1, "other": 1, "skip": 0}
SECTION_RE = [
    ("preferred", r"nice[- ]to[- ]have|bonus|plus\b|preferred|would be great|ideally|desirable|wünschenswert|von vorteil"),
    ("required", r"requirement|qualification|must[- ]have|essential|thrive if|what you bring|what you('|’)ll bring|you have|you bring|"
                 r"about you|your profile|who you are|skills|experience you|we('|’)re looking for|we expect|"
                 r"what we('|’)re looking|traits|competenc|dein profil|ihr profil|anforderung"),
    ("duties", r"what you('|’)ll do|responsibilit|your role|the role|your tasks|in this role|you will|typical tasks|"
               r"job description|what you will do|day[- ]to[- ]day|deine aufgaben|ihre aufgaben|machine learning engineering|"
               r"data management|service|software engineering"),
    ("skip", r"what we offer|benefit|how we support|about us|who we are|company description|perks|we value|"
             r"equal opportunit|diversity|privacy notice|why join|what to expect|additional information|application process|"
             r"employment (type|requirements)|wir bieten|über uns"),
]
CREDIT = {"covered": 1.0, "SoT-supported wording": 0.75, "sot-supported": 0.75, "gap": 0.0, "do not claim": 0.0, "new": 0.0}
CLAIM_TAG = {"covered": "covered", "SoT-supported wording": "sot", "sot-supported": "sot", "gap": "gap",
             "do not claim": "do-not-claim", "new": "not in library"}
NOT_SCORED = {"LANG_LOC"}   # location / language words: the profile gate, not a skill


def ro_connect(db: Path = DB) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 80:
        return False
    if s.startswith("#") or (s.startswith("**") and s.rstrip(":").endswith("**")) or s.endswith(":"):
        return True
    return len(s.split()) <= 8 and not re.search(r"[.;,]$", s) and not s.startswith(("-", "*", "•"))


INLINE_HEAD = re.compile(r"^\s*([^:.;]{3,45}):\s+(.+)$")
MIN_ITEMS = 8           # fewer scored items → no fit %, "summary too thin"


def split_lines(text: str) -> list[str]:
    """Real line breaks; a run-on summary (the daily-pipeline ledger keeps a one-line JD digest) is split on
    sentence ends and ';' so each requirement becomes its own line."""
    if text.count("\n") >= 3:
        return text.splitlines()
    return [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff(])|;\s*", text) if x.strip()]


def head_kind(h: str) -> str | None:
    h = re.sub(r"[#*_:]", " ", h).strip().lower()
    if re.search(r"must[- ]have", h):
        return "required"
    for k, rx in SECTION_RE:
        if re.search(rx, h):
            return k
    return None


def sections(text: str) -> list[tuple[str, str]]:
    """[(kind, line)] — kind of the nearest heading above the line. "Requirements: Python, …" inline headings
    (summaries) switch the section and the rest of the line is scored."""
    kind, out = "other", []
    for line in split_lines(text):
        if is_heading(line):
            kind = head_kind(line) or kind
            continue  # the heading text itself is not scored
        m = INLINE_HEAD.match(line)
        if m and len(m.group(1).split()) <= 6 and head_kind(m.group(1)):
            kind = head_kind(m.group(1))
            line = m.group(2)
        out.append((kind, line))
    return out


def generic_terms() -> set[str]:
    p = REFS / "generic_terms.txt"
    return {t.strip().casefold() for t in p.read_text(encoding="utf-8").splitlines()
            if t.strip() and not t.startswith("#")} if p.exists() else set()


def tech_terms() -> list[str]:
    p = REFS / "tech_terms.txt"
    return [t.strip() for t in p.read_text(encoding="utf-8").splitlines()
            if t.strip() and not t.startswith("#")] if p.exists() else []


def vocabulary(con: sqlite3.Connection) -> tuple[list[dict], dict, set]:
    """Every term the scan looks for: concept aliases + Skills + library keywords + references/tech_terms.txt."""
    aliases = L.alias_index(con)
    excluded = {r[0] for r in con.execute("SELECT code FROM concepts WHERE notes LIKE '%exclude from scoring%'")} | NOT_SCORED
    concepts = {r["code"]: dict(r) for r in con.execute("SELECT code, name, claim FROM concepts")}
    vocab: dict[str, dict] = {}
    for a in aliases:
        vocab.setdefault(a["alias"].casefold(), {"term": a["alias"], "code": a["code"], "coverage": None})
    for sk in con.execute("SELECT name, variants FROM skills"):   # the Skills section: claimed tools (certificate-only skipped)
        if "certificate only" in sk["name"]:
            continue
        for t in re.split(r"\s+[/·]\s+|;\s*", sk["name"] + ";" + (sk["variants"] or "")):
            t = re.sub(r"\s*\(.*?\)", "", t).strip()
            if 2 <= len(t) <= 30 and t.casefold() not in vocab:
                r = L.resolve_term(t, aliases)
                vocab[t.casefold()] = {"term": t, "code": r["code"] if r["rule"] in (1, 2) else None, "coverage": "covered"}
    for k in con.execute("SELECT term, coverage, concept_code, notes FROM keywords"):
        if (k["notes"] or "").startswith("auto: unclassified"):
            continue                           # never classified by the writer: neither credit nor penalty
        v = vocab.setdefault(k["term"].casefold(), {"term": k["term"], "code": k["concept_code"], "coverage": None})
        v["coverage"] = k["coverage"]          # term-level decision wins over the concept claim and the Skills list
        v["code"] = v["code"] or k["concept_code"]
    for t in tech_terms():
        if t.casefold() not in vocab:
            r = L.resolve_term(t, aliases)
            code = r["code"] if r["rule"] in (1, 2) else None   # only exact / normalised hits; fuzzy = not in library
            vocab[t.casefold()] = {"term": t, "code": code, "coverage": None if code else "new"}
    generic = generic_terms()   # everyday JD words ("production", "latency", "scalable"): never scored
    terms = sorted((v for v in vocab.values() if v["code"] not in excluded and len(v["term"]) >= 2
                    and v["term"].casefold() not in generic),
                   key=lambda v: -len(v["term"]))
    return terms, concepts, excluded


def term_re(term: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(term.lower())}(?!\w)")


SOFT_RE = re.compile(r"willing(ness)? to learn|or similar|familiarity|exposure to|a plus|is a bonus|nice to have|"
                     r"ideally|desirable|von vorteil|wünschenswert", re.I)


def fit(text: str, con: sqlite3.Connection | None = None) -> dict:
    """Recruiter reading: every JD line that names library terms is one requirement. Line credit = mean credit of
    its items; line weight = its section weight (a "willingness to learn" line counts as preferred).
    fit % = sum(weight x credit) / sum(weight) over the lines. Missing items are ranked by the weight they lose."""
    con = con or ro_connect()
    terms, concepts, _ = vocabulary(con)
    items: dict[str, dict] = {}
    num = den = 0.0
    n_lines = 0
    for kind, line in sections(text):
        w = SECTION_W[kind]
        if not w:
            continue
        if SOFT_RE.search(line):
            w = min(w, SECTION_W["preferred"])
        low = line.lower()
        line_items: dict[str, float] = {}
        for v in terms:                       # longest first; a hit masks its span so "edge" inside "edge deployment" is not re-counted
            rx = term_re(v["term"])
            if not rx.search(low):
                continue
            low = rx.sub(" " * len(v["term"]), low)
            claim = v["coverage"] or (concepts.get(v["code"], {}).get("claim") if v["code"] else "new") or "new"
            concept_claim = concepts.get(v["code"], {}).get("claim") if v["code"] else None
            # same claim as its concept → one item per concept; a gap member of a covered concept is its own item
            key = v["code"] if v["code"] and CREDIT.get(claim) == CREDIT.get(concept_claim) else "term:" + v["term"].casefold()
            name = concepts[v["code"]]["name"] if key == v["code"] else v["term"]
            it = items.setdefault(key, {"key": key, "name": name, "concept": v["code"], "claim": claim, "weight": 0,
                                        "lost": 0.0, "lines": 0, "section": kind, "terms": []})
            if w > it["weight"]:
                it["weight"], it["section"] = w, kind
            if v["term"] not in it["terms"]:
                it["terms"].append(v["term"])
            line_items[key] = CREDIT.get(claim, 0.0)
        if not line_items:
            continue
        n_lines += 1
        credit = sum(line_items.values()) / len(line_items)
        num += w * credit
        den += w
        for key, c in line_items.items():
            items[key]["lines"] += 1
            items[key]["lost"] += w * (1 - c) / len(line_items)
    missing = sorted((i for i in items.values() if CREDIT.get(i["claim"], 0.0) < 1.0),
                     key=lambda i: (-i["lost"], -i["weight"], i["name"].lower()))
    thin = len(items) < MIN_ITEMS
    return {"fit": None if thin or not den else round(100 * num / den), "thin": thin, "lines": n_lines, "scored_items": len(items), "weight": den,
            "covered": sorted((i for i in items.values() if CREDIT.get(i["claim"], 0.0) == 1.0),
                              key=lambda i: (-i["weight"], -i["lines"])),
            "missing": missing}


def missing_label(i: dict) -> str:
    return f"{i['name']} ({CLAIM_TAG.get(i['claim'], i['claim'])}, {i['section']})"


def print_fit(r: dict, top: int) -> None:
    print(("fit –  (summary too thin: < %d scored items)" % MIN_ITEMS if r["fit"] is None else f"fit {r['fit']}%")
          + f" · {r['lines']} requirement lines, {r['scored_items']} items, weight {r['weight']:g}")
    print("top missing: " + ("; ".join(missing_label(i) for i in r["missing"][:top]) or "-"))
    print("strongest covered: " + (", ".join(i["name"] for i in r["covered"][:top]) or "-"))


# ---- jobs (daily pipeline ledger, read-only) ----------------------------------------------------
def url_key(u: str) -> str:
    s = str(u or "").strip()
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d+)", s)
    if m:
        return "li:" + m.group(1)
    return re.sub(r"^https?://(www\.)?", "", s.split("#")[0].split("?")[0], flags=re.I).rstrip("/").lower()


def last_json_block(text: str):
    for b in reversed(re.findall(r"```json\s*(.*?)```", text or "", re.S)):
        try:
            return json.loads(b)
        except ValueError:
            continue
    return {}


def new_jobs(day: str, look_back: int = 2) -> dict:
    """New postings of the latest pipeline search on or before `day` (look back a couple of days)."""
    d0 = dt.date.fromisoformat(day)
    for back in range(look_back + 1):
        d = (d0 - dt.timedelta(days=back)).isoformat()
        led = PIPE_DATA / "runs" / f"{d}.json"
        if not led.exists():
            continue
        st = json.loads(led.read_text(encoding="utf-8")).get("steps", {}).get("search", {})
        if not st.get("done"):
            continue
        jd_text = {}
        log = PIPE_DATA / "logs" / f"{d}_search.json"
        if log.exists():
            res = json.loads(log.read_text(encoding="utf-8")).get("result", "")
            for j in (last_json_block(res) or {}).get("jobs", []):
                jd_text[url_key(j.get("url"))] = j.get("jd") or ""
        rows = [{**r, "jd": jd_text.get(url_key(r.get("url")), "")} for r in st.get("new", [])]
        return {"day": d, "rows": rows, "found": st.get("found"), "note": "" if back == 0 else f"latest search is from {d}"}
    return {"day": None, "rows": [], "note": f"no daily-pipeline search on {day} (or the {look_back} days before)"}


LOW_SCORE = 3   # Codex 1–10 score of the daily pipeline (seniority, location, visa, …): ≤ this → bottom of the list


def low_reason(r: dict) -> str:
    rs = r.get("reasons") or []
    rs = rs if isinstance(rs, list) else [str(rs)]
    blocker = next((x for x in rs if re.search(r"硬條件|未符合|sponsorship|不收|排除|未證明", x)), rs[0] if rs else "low score")
    return "Codex %s: %s" % (r.get("score"), blocker)


def rank_jobs(rows: list[dict], con: sqlite3.Connection) -> list[dict]:
    """Codex score ≤ 3 sinks to the bottom with its reason; within each group: fit desc, then Codex score."""
    out = []
    for r in rows:
        jd = r.get("jd") or ""
        f = fit(jd, con) if len(jd) > 200 else None
        miss = sorted(f["missing"], key=lambda i: CREDIT.get(i["claim"], 0.0) > 0)[:3] if f else []   # hard gaps before SoT
        low = isinstance(r.get("score"), (int, float)) and r["score"] <= LOW_SCORE
        note = "JD text not in the pipeline log" if not f else "summary too thin" if f["fit"] is None else ""
        out.append({**r, "fit": f["fit"] if f else None, "low": low,
                    "missing": [i["name"] + (" (SoT)" if CREDIT.get(i["claim"], 0.0) > 0 else "") for i in miss],
                    "fit_note": low_reason(r) if low else note})
    return sorted(out, key=lambda r: (r["low"], r["fit"] is None, -(r["fit"] or 0), -(r.get("score") or 0)))


# ---- tracker (Google Sheet, read-only) ----------------------------------------------------------
SENT_STATUS = "履歷送出"
STAGE_RE = re.compile(r"第(.+)關面試")
NUM = "一二三四五六七八九十"
SENT_RE = re.compile(r"投遞|送出|sent|applied|apply|submitted|bewerbung", re.I)
FOLLOW_RE = re.compile(r"follow|追蹤信|跟進|催", re.I)
REPLY_RE = re.compile(r"回覆|回信|收到回|replied|reply from|answered|responded|拒絕|被拒|rejected|rejection|absage|"
                      r"面試邀(請|約)|邀請面試|invited|interview (invite|invitation|scheduled|booked)|offer", re.I)
DATE_RE = re.compile(r"(?<!\d)(?:(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})|(\d{1,2})/(\d{1,2}))(?![\d/])")


def parse_dates(line: str, today: dt.date) -> list[dt.date]:
    out = []
    for y, m, d, m2, d2 in DATE_RE.findall(line):
        try:
            if y:
                out.append(dt.date(int(y), int(m), int(d)))
            else:
                x = dt.date(today.year, int(m2), int(d2))
                out.append(x if x <= today + dt.timedelta(days=7) else x.replace(year=today.year - 1))
        except ValueError:
            continue
    return out


def contact_dates(process: str, date_col: str, today: dt.date) -> dict:
    sent, follow, reply, any_ = [], [], [], []
    for line in (process or "").splitlines():
        ds = parse_dates(line, today)
        any_ += ds
        if not ds:
            continue
        if REPLY_RE.search(line):
            reply += ds
        elif FOLLOW_RE.search(line):
            follow += ds
        elif SENT_RE.search(line):
            sent += ds
    inferred = not sent
    if not sent and date_col:
        sent = parse_dates(date_col, today)
    if not sent and any_:
        sent = [min(any_)]
    return {"sent": max(sent) if sent else None, "follow": max(follow) if follow else None,
            "reply": max(reply) if reply else None, "inferred": inferred}


def norm_co(s: str) -> str:
    s = re.sub(r"\(.*?\)|（.*?）", " ", str(s or "")).casefold()
    s = re.sub(r"\b(gmbh|ag|se|inc|ltd|llc|bv|b\.v\.|plc|recycling|group|holding)\b\.?", " ", s)
    return re.sub(r"[^\w]+", "", s)


def tracker_rows() -> list[dict]:
    """Rows of 求職追蹤表 · 求職追蹤表_AI 投遞 · 投遞表單 (deduped by link / company+title). Read-only: no flush()."""
    sys.path.insert(0, str(TRACKER))
    import sheets_api as S
    b = S.Backend.from_config()
    bk = b.book.load([S.MAIN_NAME, S.AUTO_NAME, S.SUBMIT_NAME])
    rows, seen = [], set()
    for name in (S.SUBMIT_NAME, S.AUTO_NAME, bk.main_title):   # 投遞表單 first: its rows carry 日期 + 應用資料夾
        t = bk.tab(name)
        if t is None or not t.grid:
            continue
        h = S.headers(t)
        c = h["cols"]
        for r in range(h["row"] + 1, S.last_data_row(t, h) + 1):
            get = (lambda k: S.disp(t.cell(r, c[k])).strip() if k in c else "")
            pos = t.cell(r, c["Position"])
            url = S.link(pos) or get("連結")
            co, title = get("公司"), S.disp(pos).strip()
            keys = {url_key(url)} if url else set()
            keys.add("ct:" + norm_co(co) + "|" + title.casefold())
            if keys & seen:
                continue
            seen |= keys
            rows.append({"tab": name, "row": r, "company": co, "title": title, "url": url, "status": get("狀態"),
                         "process": get("求職過程"), "date": get("日期"), "end": get("結束追蹤日期"),
                         "referral_status": get("內推狀態"), "app_dir": get("應用資料夾")})
    return rows


def applications(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT jd, company, title, dir FROM applications")]


def app_for(company: str, apps: list[dict]) -> dict | None:
    n = norm_co(company)
    if not n:
        return None
    return next((a for a in apps if norm_co(a["company"]) == n), None)   # exact: "AutoScout24" is not "Scout24"


def outreach_rows(con: sqlite3.Connection) -> list[dict]:
    try:
        return [dict(r) for r in con.execute("SELECT * FROM outreach")]
    except sqlite3.OperationalError:
        return []


STALE_DAYS = 45


def followups(today: dt.date, rows: list[dict], con: sqlite3.Connection, days: int = 10, stale: int = STALE_DAYS,
              mail_companies: set[str] | None = None) -> dict:
    apps, outs = applications(con), outreach_rows(con)
    due, stale_n, undated, replied_flag = [], 0, [], []
    for r in rows:
        if r["status"] != SENT_STATUS or r["end"]:
            continue
        d = contact_dates(r["process"], r["date"], today)
        a = app_for(r["company"], apps)
        co_n = norm_co(r["company"])
        for o in outs:   # an outreach follow-up / referral the candidate sent restarts the clock
            if norm_co(o.get("company")) == co_n or (a and o.get("jd") == a["jd"]):
                if o.get("replied"):
                    d["reply"] = max(filter(None, [d["reply"], dt.date.fromisoformat(o["replied"][:10])]))
                if o.get("sent") and o.get("status") in ("sent", "no-reply"):
                    d["follow"] = max(filter(None, [d["follow"], dt.date.fromisoformat(o["sent"][:10])]))
        if not d["sent"]:
            undated.append(r)
            continue
        last = max(filter(None, [d["sent"], d["follow"]]))
        if d["reply"] and d["reply"] >= d["sent"]:
            replied_flag.append({**r, "reply": d["reply"].isoformat()})
            continue
        if mail_companies and co_n in mail_companies:
            continue
        age = (today - last).days
        if age <= days:
            continue
        if age > stale:
            stale_n += 1
            continue
        due.append({**r, "sent": d["sent"].isoformat(), "sent_inferred": d["inferred"], "last_contact": last.isoformat(), "age": age,
                    "followed_up": bool(d["follow"]), "jd": a["jd"] if a else None,
                    "suggest": f"/outreach {a['jd'] if a else r['company']} follow-up"})
    out_due = []
    for o in outs:   # outreach rows the candidate marked sent, no reply after N days
        if o.get("status") == "sent" and o.get("sent") and not o.get("replied"):
            age = (today - dt.date.fromisoformat(o["sent"][:10])).days
            if age > days:
                out_due.append({**o, "age": age, "suggest": f"/outreach {o['jd']} follow-up"})
    due.sort(key=lambda x: x["age"])
    return {"due": due, "outreach_due": out_due, "stale": stale_n, "stale_days": stale, "undated": undated,
            "reply_logged_status_unchanged": replied_flag, "days": days}


# ---- inbox ------------------------------------------------------------------------------------
def ats_domains() -> list[str]:
    p = REFS / "inbox_rules.md"
    if not p.exists():
        return []
    block = re.search(r"<!-- ats-domains -->(.*?)<!-- /ats-domains -->", p.read_text(encoding="utf-8"), re.S)
    return [d.strip().lower() for d in (block.group(1) if block else "").split() if "." in d]


def ats_hosts() -> set[str]:
    """Registrable names of ATS / job-board hosts (personio, greenhouse, …): never a company match."""
    return {norm_co(registrable(d).split(".")[0]) for d in ats_domains()} | {"workday", "join", "lever", "ashby"}


# Classifier (references/inbox_rules.md). Order: auto-reply → offer → invitation → rejection decision → request →
# ack → interview mention → other. Each pattern returns the matched phrase so the digest can show it.
AUTO_RE = r"out of (the )?office|automatic reply|auto[- ]?reply|autoreply|abwesenheit|automatische antwort|abwesenheitsnotiz"
OFFER_RE = (r"(pleased|happy|delighted|excited) to (extend|make|offer) (you )?(an |the |a )?(formal |written )?(job )?offer|"
            r"offer letter|job offer|employment offer|vertragsangebot|angebot (zu )?(unterbreiten|machen)|arbeitsvertrag (zukommen|anbei)")
INVITE_RE = (r"invit\w* you|like to invite|schedule (a|an|your|the) (call|chat|interview|meeting|time|conversation)|"
             r"(share|send|let us know) your availability|your availability|availability (for|to)|book a (time|slot)|"
             r"book a (timeslot|time slot|call|meeting)|on my (agenda|calendar)|pick a (time|slot)|calendly|reschedul\w*|terminvorschlag|termin (verschieben|vorschlagen|vereinbaren)|"
             r"\w*einladen|einladung zu\w*|verschieben")
REJECT_RE = (r"unfortunately[^.]{0,160}?\b(not|decided|unable to offer|other candidates whose|will not|won't|cannot)\b|"
             r"regret to inform|decided not to (proceed|move)|(are|is) not able to (move|proceed)|unable to (move|proceed)|"
             r"not (be )?able to (offer|consider)|(will|are) not (be )?(mov|progress|proceed)\w*|"
             r"not been successful|no longer (under consideration|considered)|"
             r"leider[^.]{0,160}?(absage|nicht berücksichtigen|nicht weiter|anderen kandidat\w*|nicht in die engere|"
             r"für (einen|eine) andere\w* (kandidat|bewerber)\w*)|absage")
REQUEST_RE = (r"could you (please )?(send|share|provide)|please (provide|send|complete|fill)|assessment|take[- ]home|"
              r"coding (challenge|test)|questionnaire|salary expectation|notice period|work permit|"
              r"additional (information|documents)|unterlagen nachreichen|gehaltsvorstellung|fragebogen")
ACK_RE = (r"thank you for (your )?(applying|application|interest)|thanks for applying|received your application|"
          r"application (has been )?received|we have received|eingangsbestätigung|bewerbung (ist )?(bei uns )?eingegangen|"
          r"vielen dank für (ihre|deine) bewerbung|danke für deine bewerbung|application confirmation|your application was sent to")
INTERVIEW_RE_MAIL = r"interview|vorstellungsgespräch|kennenlerngespräch|phone screen|technical (call|round)"
RECRUIT_RE = re.compile(r"application|bewerbung|recruit|talent|hiring|candidate|interview|position|role|vacanc|stelle|offer", re.I)
KINDS = [("other", AUTO_RE), ("offer", OFFER_RE), ("interview", INVITE_RE), ("rejection", REJECT_RE),
         ("request", REQUEST_RE), ("ack", ACK_RE), ("interview", INTERVIEW_RE_MAIL)]


CONDITIONAL_RE = re.compile(r"\bif\b|\bonce\b|\bshould (we|the|your|you)\b|\bwe will\b|\bwe('|’)ll\b|\bwill (send|invite|contact|reach)|"
                            r"in case|as soon as|\bsollte\b|\bfalls\b|werden wir|wir werden|sobald", re.I)


# a condition on the candidate's own interest keeps the invitation ("If so, feel free to book a timeslot")
CANDIDATE_IF = re.compile(r"\bif (so|yes|you('|’)re|you are|this (sounds|is)|that (sounds|works)|interested|you('|’)d like|"
                          r"you would like|you are still|you're still)\b|\bfalls (ja|du|sie) (interesse|noch)", re.I)


def sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+|\n+", text) if x.strip()]


def classify(text: str, auto_submitted: str = "") -> tuple[str, str]:
    """(kind, matched phrase). An Auto-Submitted header other than "no" means an auto-reply → other.
    An invitation only counts in a sentence that is not conditional / future ("If the feedback is positive, we will
    send you a proposal for a first interview" is an ack)."""
    if auto_submitted and auto_submitted.strip().lower() not in ("no", ""):
        return "other", f"Auto-Submitted: {auto_submitted}"
    for kind, rx in KINDS:
        if rx is INVITE_RE:
            m = next((m for x in sentences(text) if not CONDITIONAL_RE.search(CANDIDATE_IF.sub(" ", x))
                      for m in [re.search(rx, x, re.I)] if m), None)
        else:
            m = re.search(rx, text, re.I)
        if m:
            return kind, m.group(0)[:60]
    return "other", ""


def next_stage(status: str) -> str:
    m = STAGE_RE.search(status or "")
    if not m:
        return "第一關面試"
    i = NUM.find(m.group(1))
    return f"第{NUM[i + 1]}關面試" if 0 <= i < len(NUM) - 1 else status


def propose(kind: str, status: str) -> tuple[str | None, str]:
    """(proposed 狀態 or None, note). 狀態 = furthest stage reached: never downgraded; 被拒絕 / 收到Offer rows get no
    proposal. Table: references/inbox_rules.md."""
    in_interview = bool(STAGE_RE.search(status or ""))
    if status == "被拒絕":
        return None, "row is 被拒絕 already — check by hand"
    if kind == "offer":
        return (None, "offer already recorded") if status == "收到Offer" else ("收到Offer", "offer mail")
    if status == "收到Offer":
        return None, ""
    if kind == "rejection":
        return (None, "rejected after interview: keep the furthest stage, fill 結束追蹤日期") if in_interview \
            else ("被拒絕", "fill 結束追蹤日期")
    if kind == "interview":
        return (None, f"scheduling mail on {status}: new round? then {next_stage(status)}") if in_interview \
            else ("第一關面試", "confirm a slot yourself; run /interview-prep")
    if kind == "ack":
        return ("履歷送出", "application acknowledged") if status in ("", "準備中") else (None, "ack only")
    if kind == "request":
        return None, "reply needed (info / assessment)"
    return None, ""


MULTI_TLD = {"co.uk", "org.uk", "ac.uk", "com.au", "co.jp", "com.br", "co.in", "com.tw", "co.nz"}


def registrable(domain: str) -> str:
    parts = domain.lower().strip(".").split(".")
    n = 3 if ".".join(parts[-2:]) in MULTI_TLD else 2
    return ".".join(parts[-n:])


def clean_name(company: str) -> str:
    s = re.sub(r"\(.*?\)|（.*?）", " ", str(company or ""))
    s = re.sub(r"\b(gmbh|ag|se|inc|ltd|llc|b\.?v\.?|plc|recycling|group|holding)\b\.?", " ", s, flags=re.I)   # = norm_co
    return re.sub(r"\s+", " ", s).strip()


def company_matcher(rows: list[dict], apps: list[dict]):
    """Word-boundary match of the company name on the original text, or the sender's registrable domain.
    Names < 5 characters need the exact domain (so "Warm regards" is not Arm); ATS hosts never match."""
    hosts = ats_hosts()
    cands = {}
    for r in rows + [{"company": a["company"], "title": a["title"], "status": "", "tab": "-"} for a in apps]:
        name = clean_name(r["company"])
        key = norm_co(name)
        if len(key) < 2 or key in hosts or key in cands or any(norm_co(w) in hosts for w in name.split()):
            continue   # junk rows like "SmartRecruiters Senior AI Engineer" (role text in the company cell)
        rx = re.compile(r"(?<![\w])" + r"[\s\-.]*".join(map(re.escape, name.split())) + r"(?![\w])", re.I)
        cands[key] = (rx, r)

    first: dict[str, list] = {}
    for key, (rx, r) in cands.items():
        w = clean_name(r["company"]).split()
        if len(w) > 1 and len(w[0]) >= 5 and w[0].casefold() not in FIRST_WORD_STOP:
            first.setdefault(w[0].casefold(), []).append(r)

    def match(text: str, domain: str, ats_sub: str = "") -> dict | None:
        for label in filter(None, [registrable(domain).split(".")[0] if domain else "", ats_sub]):
            if norm_co(label) not in hosts and norm_co(label) in cands:   # tomra.com · sogelink.teamtailor-mail.com
                return cands[norm_co(label)][1]
        for key, (rx, r) in sorted(cands.items(), key=lambda kv: -len(kv[0])):
            if len(key) >= 5 and rx.search(text):
                return r
        for w, rs in first.items():   # "EVIDENT Europe GmbH" → "Evident Microscopy": unique, distinctive first word only
            if len(rs) == 1 and re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text, re.I):
                return rs[0]
        return None
    return match


FIRST_WORD_STOP = {"senior", "global", "digital", "smart", "munich", "berlin", "german", "germany", "europe", "european",
                   "international", "united", "general", "applied", "advanced", "intelligent", "future", "modern", "tenth",
                   "first", "national", "software", "systems", "technology", "technologies", "solutions", "consulting",
                   "health", "health", "energy", "capital", "partners", "group", "data", "cloud", "power", "north", "south"}


ROLE_RE = re.compile(r"(?:application|bewerbung)\s+(?:for|as|to|als|für|auf)\s+(?:the\s+|a\s+|an\s+|die\s+|der\s+)?"
                     r"(?:(?:position|role|stelle)\s+(?:of\s+)?)?(.+?)(?:\s+(?:at|bei|@|with)\s+|\s+-\s+|\s*\||\s*$)", re.I)


def mail_role(subject: str) -> str:
    """The job title a subject names ("Your application as PhD: Surface Physics at Bosch" → "PhD: Surface Physics")."""
    m = ROLE_RE.search(subject or "")
    r = m.group(1).strip(" :.!") if m else ""
    return "" if re.fullmatch(r"(a |our )?(position|role|job|stelle)( at .*)?", r, re.I) else r


def clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\((m|w|f|d|x)[/|,]?.*?\)|[^\w\s]", " ", str(t or ""), flags=re.I)).strip().casefold()


def ats_subdomain(domain: str, doms: list[str]) -> str:
    """'sogelink' from sogelink.teamtailor-mail.com, 'dexter-health' from dexter-health.msg.join.com."""
    for d in doms:
        if domain.endswith("." + d):
            labels = [x for x in domain[: -len(d) - 1].split(".") if x not in ("msg", "mail", "email", "jobs", "careers", "hire")]
            return labels[0] if labels else ""
    return ""


BOARD_FROM = re.compile(r"@([\w-]+\.)*(linkedin|glassdoor|indeed|stepstone|arbeitnow|otta|welcometothejungle|xing|wellfound|"
                        r"jobgether|monster|jobleads|honeypot)\.\w+", re.I)
OWN_APPLICATION = re.compile(r"your application|application (was|has been) (sent|viewed|received)|bewerbung|"
                             r"you applied|applied to|interview", re.I)
ALERT_QUERY_EXCLUDE = ["jobalerts-noreply@linkedin.com", "newsletters-noreply@linkedin.com", "editors-noreply@linkedin.com",
                       "security-noreply@linkedin.com", "messages-noreply@linkedin.com", "jobagent.stepstone.de",
                       "noreply@glassdoor.com", "alert@indeed.com"]


def is_job_alert(m: dict) -> bool:
    """Job-board alerts, newsletters and marketing (webinar, event, digest …) → other/newsletter, dropped. Any mail from a job board (LinkedIn, Glassdoor,
    StepStone, Indeed, …) counts as one unless its subject is about the candidate's own application
    (e.g. LinkedIn Easy Apply "Your application was sent to X")."""
    frm, subj = str(m.get("from") or ""), str(m.get("subject") or "")
    if NEWSLETTER_SUBJ.search(subj) and not OWN_APPLICATION.search(subj):
        return True
    return bool(BOARD_FROM.search(frm)) and not OWN_APPLICATION.search(subj)


NEWSLETTER_SUBJ = re.compile(r"\[?webinar\]?|newsletter|digest|\bevent\b|\d+% off|register now|save the date|"
                             r"product update|whitepaper|e-?book", re.I)


class MailCtx:
    """Shared per-run state for classifying mails: tracker rows, applications, matcher, classifier + Jev client."""

    def __init__(self, rows: list[dict], con: sqlite3.Connection, classifier: str = "regex", client=None):
        self.rows, self.apps, self.doms = rows, applications(con), ats_domains()
        self.match = company_matcher(rows, self.apps)
        self.classifier, self.client = classifier, client
        self.fallbacks = 0
        hosts = ats_hosts()
        self.names: dict[str, dict] = {}   # cleaned company name -> {"name", "key", "roles", "row"}
        for r in rows + [{"company": a["company"], "title": a["title"], "status": "", "tab": "-"} for a in self.apps]:
            name = clean_name(r["company"])
            key = norm_co(name)
            if len(key) < 2 or key in hosts or any(norm_co(w) in hosts for w in name.split()):
                continue
            e = self.names.setdefault(key, {"name": name, "key": key, "roles": [], "row": r})
            if r.get("title") and r["title"] not in e["roles"]:
                e["roles"].append(r["title"])


def mail_parts(m: dict, doms: list[str]) -> dict:
    frm = str(m.get("from") or "")
    dom = (re.search(r"@([\w.-]+)", frm) or [None, ""])[1].lower()
    via_ats = any(dom == d or dom.endswith("." + d) for d in doms)
    return {"from": frm, "dom": dom, "via_ats": via_ats, "display": re.sub(r"<.*?>", " ", frm),
            "text": " ".join(str(m.get(k) or "") for k in ("subject", "snippet", "body")),
            "auto": str(m.get("auto_submitted") or (m.get("headers") or {}).get("Auto-Submitted", ""))}


def refine_row(hit: dict | None, text: str, subject: str, rows: list[dict]) -> tuple[dict | None, str]:
    """Several rows of one company → the row whose title the mail names; a role the company's rows lack → note."""
    if not hit:
        return None, ""
    same = [r for r in rows if norm_co(r["company"]) == norm_co(hit["company"])]
    named = [r for r in same if r.get("title") and clean_title(r["title"]) in clean_title(text)]
    if named:
        return max(named, key=lambda r: len(r["title"])), ""
    role = mail_role(subject)
    if role and not any(clean_title(r.get("title")) and (clean_title(r["title"]) in clean_title(role)
                                                         or clean_title(role) in clean_title(r["title"])) for r in same):
        return hit, f"role not in tracker: {role[:40]} — check by hand"
    return hit, ""


def shortlist(m: dict, mp: dict, ctx: MailCtx, regex_hit: dict | None, cap: int = 8) -> list[dict]:
    """Candidate companies for the Jev Choice: the regex match, a domain / ATS-subdomain label, or a distinctive
    word (>= 4 chars) of the name in sender, subject or text. Never the whole tracker."""
    hay = mp["display"] + " " + mp["text"]
    labels = {norm_co(registrable(mp["dom"]).split(".")[0])} if mp["dom"] else set()
    if mp["via_ats"]:
        labels.add(norm_co(ats_subdomain(mp["dom"], ctx.doms)))
    scored = []
    for key, e in ctx.names.items():
        s = 0
        if regex_hit and norm_co(clean_name(regex_hit["company"])) == key:
            s += 10
        if key in labels:
            s += 5
        words = [w for w in e["name"].split() if len(w) >= 4 and w.casefold() not in FIRST_WORD_STOP]
        s += sum(2 for w in words if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", hay, re.I))
        if s:
            scored.append((s, e))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    return [{"name": e["name"], "key": e["key"], "role": "; ".join(e["roles"][:3])} for _, e in scored[:cap]]


def jev_text(m: dict) -> str:
    """The body when fetch_gmail read it (snippet ambiguous), else the snippet; cut at a sentence end ≤ TEXT_LIMIT."""
    t = re.sub(r"\s+", " ", str(m.get("body") or m.get("snippet") or "")).strip()
    if len(t) <= JQ.TEXT_LIMIT:
        return t
    cut = t[: JQ.TEXT_LIMIT]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > JQ.TEXT_LIMIT // 2 else cut) + " …"


def classify_regex(m: dict, mp: dict, ctx: MailCtx, prefilter: bool = True) -> dict:
    if prefilter and is_job_alert(m):
        return {"kind": "other", "phrase": "job alert / newsletter", "hit": None, "alert": True, "prob": None}
    hit = ctx.match(mp["display"] + " " + mp["text"], "" if mp["via_ats"] else mp["dom"],
                    ats_subdomain(mp["dom"], ctx.doms) if mp["via_ats"] else "")
    kind, phrase = classify(mp["text"], mp["auto"])
    if is_job_alert(m):
        kind, phrase = "other", "job alert / newsletter"
    return {"kind": kind, "phrase": phrase, "hit": hit, "alert": is_job_alert(m), "prob": None}


def classify_jev(m: dict, mp: dict, ctx: MailCtx) -> dict:
    """One request per mail with every question; code decides (jev_questions.decide). API error → regex fallback."""
    rx = classify_regex(m, mp, ctx, prefilter=False)
    cands = shortlist(m, mp, ctx, rx["hit"])
    payload = {"model": JQ.MODEL, "state": JQ.state({**m, "from_domain": mp["dom"]}, cands, jev_text(m)),
               "questions": JQ.questions(cands)}
    t0 = time.monotonic()
    try:
        out = ctx.client.system_one(payload)
    except Exception as e:   # surfaced as an error, never as an answer
        ctx.fallbacks += 1
        return {**rx, "phrase": f"(regex fallback: {str(e)[:60]}) {rx['phrase']}", "fallback": True,
                "latency": time.monotonic() - t0}
    ans = out["answers"]
    d = JQ.decide(ans)
    c = JQ.decide_company(ans, cands)
    hit = None
    if c["name"] and c["band"] == "yes":
        key = next((x["key"] for x in cands if x["name"] == c["name"]), None)
        hit = ctx.names[key]["row"] if key else None
    return {"kind": d["kind"], "phrase": f"jev {d['why']} {d['prob']:.2f}" if d["prob"] is not None else "jev",
            "prob": d["prob"], "band": d["band"], "hit": hit, "company_jev": c, "alert": d["why"] == "mass_mailing",
            "nouls": {k: (ans.get(k) or {}).get("noul") for k in JQ.NOULS}, "candidates": [x["name"] for x in cands],
            "fallback": False, "latency": out.get("_latency_s", time.monotonic() - t0),
            "tokens": (out.get("usage") or {}).get("input_tokens")}


def inbox(mails: list[dict], rows: list[dict], con: sqlite3.Connection, classifier: str = "regex", client=None,
          ctx: MailCtx | None = None) -> list[dict]:
    """Classify, map, PROPOSE. classifier='jev' needs a jev_client.Client; board alerts are still dropped by the
    regex prefilter first (cheap and exact), so Jev is asked only about the rest."""
    ctx = ctx or MailCtx(rows, con, classifier, client)
    out = []
    for m in mails:
        if is_job_alert(m):
            continue                      # other/newsletter
        mp = mail_parts(m, ctx.doms)
        r = classify_jev(m, mp, ctx) if ctx.classifier == "jev" and ctx.client else classify_regex(m, mp, ctx)
        hit, kind = r["hit"], r["kind"]
        if not (mp["via_ats"] or hit or RECRUIT_RE.search(m.get("subject") or "")):
            continue
        if kind == "other" and not hit:
            continue
        hit, role_note = refine_row(hit, mp["text"], m.get("subject") or "", rows)
        a = app_for(hit["company"], ctx.apps) if hit else None
        status = hit.get("status", "") if hit else ""
        if kind == "review":
            new, note = None, f"needs review ({r['phrase']})"
        else:
            new, note = propose(kind, status) if hit else (None, "no matching application — check by hand")
        if hit and (r.get("company_jev") or {}).get("band") == "review":
            new, note = None, "company unsure — check by hand"
        if role_note:   # same company, a role the tracker does not have (Bosch PhD mail vs the Bosch Österreich row)
            new, note = None, role_note
        out.append({"id": m.get("id"), "date": str(m.get("date") or "")[:10], "from": mp["from"], "subject": m.get("subject"),
                    "kind": kind, "phrase": r["phrase"], "company": hit["company"] if hit else None,
                    "title": hit.get("title") if hit else None, "jd": a["jd"] if a else None,
                    "tab": hit.get("tab") if hit else None, "row": hit.get("row") if hit else None,
                    "status": status, "proposed_status": new, "note": note, "via_ats": mp["via_ats"],
                    "classifier": "regex" if r.get("fallback") or ctx.classifier != "jev" else "jev"})
    return out


def jev_client_or_none(debug: bool = False):
    """A Jev client, or None with 'Jev: no API key' on stderr (→ regex)."""
    import jev_client
    key = jev_client.api_key()
    if not key:
        print("Jev: no API key — using the regex classifier", file=sys.stderr)
        return None
    return jev_client.Client(key, debug=debug)


# ---- local Google fetch (read-only scopes of the shared google_auth login) ---------------------------
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GCAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
RO_SCOPES = {"gmail": "https://www.googleapis.com/auth/gmail.readonly",
             "calendar": "https://www.googleapis.com/auth/calendar.readonly"}
SUBJECT_WORDS = ["application", "bewerbung", "interview", '"your application"', "candidacy", '"next steps"',
                 "assessment", "absage", "offer", "vorstellungsgespräch", "recruiting", "position"]


def google_http(kind: str):
    """google_auth.Http() after checking the token carries the read-only scope. Only GET is ever called."""
    sys.path.insert(0, str(TRACKER))
    import google_auth
    tok = json.loads(google_auth.TOKEN.read_text(encoding="utf-8")) if google_auth.TOKEN.exists() else {}
    if RO_SCOPES[kind] not in (tok.get("scopes") or []):
        raise RuntimeError(f"Google token has no {RO_SCOPES[kind].rsplit('/', 1)[1]} scope (google_auth.py SCOPES + --auth)")
    return google_auth.Http()


def gmail_query(start: dt.date, rows: list[dict]) -> str:
    doms = " OR ".join(ats_domains())
    open_cos = sorted({clean_name(r["company"]) for r in rows if r.get("status") not in ("被拒絕", "")
                       and len(norm_co(r["company"])) >= 5 and not r.get("end")})[:40]
    cos = " OR ".join(f'"{c}"' for c in open_cos)
    excl = " ".join(f"-from:{x}" for x in ALERT_QUERY_EXCLUDE)
    return (f"after:{start:%Y/%m/%d} -from:me -category:promotions -category:social {excl} "
            f"{{from:({doms}) subject:({' OR '.join(SUBJECT_WORDS)})" + (f" {cos}" if cos else "") + "}")


def body_text(payload: dict, limit: int = 1500) -> str:
    import base64
    stack = [payload or {}]
    while stack:
        part = stack.pop(0)
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            raw = base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
            return re.sub(r"\s+", " ", raw)[:limit]
        stack += part.get("parts") or []
    stack, html = [payload or {}], ""
    while stack and not html:
        part = stack.pop(0)
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            raw = base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
            raw = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw)
            import html as _h
            html = _h.unescape(re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", ". ", raw))
            html = re.sub(r"<[^>]+>", " ", html)
        stack += part.get("parts") or []
    return re.sub(r"\s+", " ", html)[:limit]


def fetch_gmail(start: dt.date, rows: list[dict], max_n: int = 200) -> list[dict]:
    """messages.list (q) + messages.get format=metadata (From, Subject, Date, Auto-Submitted + snippet). format=full only
    for a kept mail whose snippet does not classify. Read-only: no modify / labels / drafts."""
    import urllib.parse
    http = google_http("gmail")
    ids, token = [], None
    while len(ids) < max_n:
        q = urllib.parse.urlencode({"q": gmail_query(start, rows), "maxResults": min(100, max_n - len(ids)),
                                    **({"pageToken": token} if token else {})})
        page = http.request("GET", f"{GMAIL}?{q}")
        ids += [m["id"] for m in page.get("messages", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    out = []
    for i in ids:
        meta = http.request("GET", f"{GMAIL}/{i}?format=metadata&metadataHeaders=From&metadataHeaders=Subject"
                                   "&metadataHeaders=Date&metadataHeaders=Auto-Submitted&metadataHeaders=List-Id")
        h = {x["name"].lower(): x["value"] for x in meta.get("payload", {}).get("headers", [])}
        ms = int(meta.get("internalDate", 0)) / 1000
        m = {"id": i, "from": h.get("from", ""), "subject": h.get("subject", ""), "snippet": meta.get("snippet", ""),
             "date": dt.datetime.fromtimestamp(ms, TZ).isoformat() if ms else "", "auto_submitted": h.get("auto-submitted", ""),
             "list_id": h.get("list-id", "")}
        # snippets are cut at ~200 chars and rejections open with "thank you for your interest": read the body
        # whenever the snippet alone says only ack / other
        if not is_job_alert(m) and classify(m["subject"] + " " + m["snippet"])[0] in ("other", "ack"):
            full = http.request("GET", f"{GMAIL}/{i}?format=full")
            m["body"] = body_text(full.get("payload", {}))
        out.append(m)
    return out


def fetch_calendar(today: dt.date) -> list[dict]:
    import urllib.parse
    http = google_http("calendar")
    t0 = dt.datetime.combine(today, dt.time(0), TZ)
    q = urllib.parse.urlencode({"timeMin": t0.isoformat(), "timeMax": (t0 + dt.timedelta(days=7)).isoformat(),
                                "singleEvents": "true", "orderBy": "startTime", "maxResults": 100})
    out = []
    for e in http.request("GET", f"{GCAL}?{q}").get("items", []):
        st, en = e.get("start", {}), e.get("end", {})
        out.append({"summary": e.get("summary", ""), "start": st.get("dateTime") or st.get("date"),
                    "end": en.get("dateTime") or en.get("date"), "location": e.get("location", ""),
                    "description": (e.get("description") or "")[:1500],
                    "attendees": [x.get("email", "") for x in e.get("attendees", [])]})
    return out


# ---- calendar ---------------------------------------------------------------------------------
INTERVIEW_RE = re.compile(r"interview|gespräch|screen|technical|onsite|on-site|hiring|recruit|kennenlernen|call with|"
                          r"meet(ing)? with|case study|panel", re.I)


def to_dt(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        x = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (x if x.tzinfo else x.replace(tzinfo=TZ)).astimezone(TZ)


def interview_dir(a: dict | None) -> Path | None:
    if not a or not a.get("dir"):
        return None
    d = Path(a["dir"]) if Path(a["dir"]).is_absolute() else REPO / a["dir"]
    return d


def calendar(events: list[dict], today: dt.date, rows: list[dict], con: sqlite3.Connection) -> dict:
    apps = applications(con)
    match = company_matcher(rows, apps)
    week_end = today + dt.timedelta(days=7)
    out = {"today": [], "week": []}
    for e in events:
        start = to_dt(e.get("start"))
        if not start or not (today <= start.date() < week_end):
            continue
        text = " ".join(str(e.get(k) or "") for k in ("summary", "description", "location")) + " " + \
            " ".join(map(str, e.get("attendees") or []))
        doms = [d for d in re.findall(r"@([\w.-]+)", text) if registrable(d) not in ("gmail.com", "outlook.com", "googlemail.com",
                                                                                   "hotmail.com", "yahoo.com", "icloud.com")]
        hit = next(filter(None, (match(text, d) for d in doms)), None) or match(text, "")
        co = hit["company"] if hit else None
        if not (co or INTERVIEW_RE.search(e.get("summary") or "")):
            continue
        a = app_for(co, apps) if co else None
        d = interview_dir(a)
        prep = d / "10_interview.md" if d and (d / "10_interview.md").exists() else None
        item = {"start": start.strftime("%a %m-%d %H:%M"), "summary": e.get("summary"), "company": co,
                "jd": a["jd"] if a else None,
                "prep": str(prep.relative_to(REPO)) if prep else None,
                "suggest": None if prep else f"/interview-prep {a['jd'] if a else (co or '<Co>')}"}
        out["today" if start.date() == today else "week"].append(item)
    return out


# ---- digest -----------------------------------------------------------------------------------
def since(today: dt.date) -> dt.date:
    prev = sorted(p.stem for p in DIGEST.glob("????-??-??.md") if p.stem < today.isoformat())
    return dt.date.fromisoformat(prev[-1]) if prev else today - dt.timedelta(days=2)


def cell(s, n=40) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).replace("|", "/")
    return s if len(s) <= n else s[: n - 1] + "…"


def digest(today: dt.date, jobs: dict, fu: dict | None, mails: list[dict] | None, cal: dict | None,
           notes: list[str], top: int, older: list[dict] | None = None) -> str:
    L_ = [f"# Morning run {today.isoformat()}", ""]
    ranked = jobs.get("ranked", [])[:top]
    L_.append(f"## New postings ({len(jobs.get('rows', []))} new{', ' + jobs['note'] if jobs.get('note') else ''}) — top {len(ranked)} by library fit")
    if ranked:
        L_ += ["| Fit | Score | Company | Role | Where | Top missing |", "|---|---|---|---|---|---|"]
        for r in ranked:
            L_.append(f"| {str(r['fit']) + '%' if r['fit'] is not None else '–'} | {r.get('score') or '–'} | {cell(r['company'], 22)} | "
                      f"[{cell(r['title'], 38)}]({r['url']}) | {cell(r.get('city'), 12)} {r.get('mode') or ''} | "
                      f"{cell(r['fit_note'] if r['low'] else ('; '.join(r['missing']) or r['fit_note']), 60)} |")
    L_.append("")
    L_.append("## Inbox")
    if mails is None:
        L_.append("- Gmail not read — local Google login (gmail.readonly) or the connector (see SKILL.md).")
    elif not mails:
        L_.append("- No recruiter / ATS mail since the last run.")
    else:
        for m in mails[:6]:
            prop = f" → PROPOSE 狀態 **{m['proposed_status']}**" if m["proposed_status"] else ""
            L_.append(f"- {m['date']} {m['kind']} «{cell(m['phrase'], 30)}»: {cell(m['company'] or m['from'], 24)} — {cell(m['subject'], 40)}{prop}"
                      f"{' (' + m['note'] + ')' if m['note'] else ''}")
        if len(mails) > 6:
            L_.append(f"- … {len(mails) - 6} more (`morning.py inbox` for all)")
    if older:
        grp = {}
        for m in older:
            grp.setdefault((m["company"], m["kind"], m["proposed_status"]), []).append(m)
        L_.append(f"- Older mail ({STALE_DAYS} d), proposal not applied: " + "; ".join(
            f"{cell(c, 20)} {k}{' ×' + str(len(v)) if len(v) > 1 else ''} → **{p}**" for (c, k, p), v in grp.items())
            + f" (`morning.py inbox --google --since {(today - dt.timedelta(days=STALE_DAYS)).isoformat()}`)")
    L_.append("")
    L_.append("## Interviews")
    if cal is None:
        L_.append("- Calendar not read — local Google login (calendar.readonly) or the connector (see SKILL.md).")
    elif not cal["today"] and not cal["week"]:
        L_.append("- None today or in the next 7 days.")
    else:
        for when, items in (("today", cal["today"]), ("this week", cal["week"])):
            for i in items[:4]:
                L_.append(f"- {when} {i['start']}: {cell(i['summary'], 40)} — "
                          + (f"prep: {i['prep']}" if i["prep"] else f"no prep yet → `{i['suggest']}`"))
    L_.append("")
    L_.append("## Follow-ups due")
    if fu is None:
        L_.append("- Tracker not read (Google Sheet unavailable or --no-sheet).")
    else:
        if not fu["due"] and not fu["outreach_due"]:
            L_.append(f"- None ({SENT_STATUS} > {fu['days']} days without reply: 0 in the actionable window).")
        for r in fu["due"][:6]:
            L_.append(f"- {cell(r['company'], 28)} · {cell(r['title'], 34)} · sent{'≈' if r['sent_inferred'] else ''} {r['sent']} ({r['age']} d"
                      f"{', followed up' if r['followed_up'] else ''}) → `{r['suggest']}`")
        if len(fu["due"]) > 6:
            L_.append(f"- … {len(fu['due']) - 6} more (`morning.py followups`)")
        for o in fu["outreach_due"][:3]:
            L_.append(f"- outreach #{o['id']} {o['person']} ({o['company']}) sent {o['sent']}, no reply {o['age']} d → `{o['suggest']}`")
        extra = []
        if fu["stale"]:
            extra.append(f"{fu['stale']} rows {SENT_STATUS} > {fu['stale_days']} d with no reply (consider closing)")
        if fu["undated"]:
            extra.append(f"{len(fu['undated'])} rows without a sent date in 求職過程")
        if fu["reply_logged_status_unchanged"]:
            extra.append(f"{len(fu['reply_logged_status_unchanged'])} rows log a reply but 狀態 is still {SENT_STATUS}")
        if extra:
            L_.append("- " + " · ".join(extra) + ".")
    L_.append("")
    L_.append("## Next actions")
    acts = []
    if ranked and ranked[0]["fit"] is not None:
        acts.append(f"Reply with the postings to run `/apply` on (best fit: {cell(ranked[0]['company'], 20)} {ranked[0]['fit']}%).")
    if mails or older:
        n = sum(1 for m in (mails or []) + (older or []) if m["proposed_status"])
        if n:
            acts.append(f"Confirm {n} proposed status change(s); nothing was written.")
    if cal and (cal["today"] or cal["week"]):
        acts.append("Prepare the interviews listed above.")
    if fu and fu["due"]:
        acts.append(f"Draft {min(len(fu['due']), 6)} follow-up(s) with `/outreach`; send them yourself.")
    acts += notes
    L_ += [f"{i}. {a}" for i, a in enumerate(acts or ["Nothing pressing today."], 1)]
    return "\n".join(L_) + "\n"


def shorten(md: str, limit: int = 40) -> str:
    lines = md.rstrip("\n").split("\n")
    return md if len(lines) <= limit else "\n".join(lines[: limit - 1] + ["… (trimmed to 40 lines)"]) + "\n"


def selftest(con: sqlite3.Connection) -> int:
    """Fixture checks: classifier phrases, company matching, job-alert filter, status proposals, fit order."""
    fx = json.loads((HERE / "fixtures" / "inbox_cases.json").read_text(encoding="utf-8"))
    bad = 0

    def check(ok: bool, label: str) -> None:
        nonlocal bad
        bad += not ok
        print(("ok   " if ok else "FAIL ") + label)
    for c in fx["classify"]:
        kind, phrase = classify(c["text"])
        check(kind == c["kind"], f"classify {kind:<9} «{phrase}» (want {c['kind']}: {c['why']})")
    match = company_matcher(fx["rows"], applications(con))
    for c in fx["match"]:
        dom = (re.search(r"@([\w.-]+)", c["from"]) or [None, ""])[1].lower()
        via_ats = any(dom == d or dom.endswith("." + d) for d in ats_domains())
        hit = match(re.sub(r"<.*?>", " ", c["from"]) + " " + c["subject"] + " " + c["snippet"], "" if via_ats else dom,
                    ats_subdomain(dom, ats_domains()) if via_ats else "")
        got = hit["company"] if hit else None
        check(got == c["company"], f"match {c['from'][:40]!r} → {got} (want {c['company']}: {c['why']})")
    for c in fx.get("inbox", []):
        got = inbox([{"id": "fx", **c}], fx["rows"], con)
        prop = got[0]["proposed_status"] if got else "dropped"
        check(prop == c["proposed"], f"inbox {c['subject'][:46]!r} → {prop} (want {c['proposed']}: {c['why']})")
    for c in fx.get("alerts", []):
        got = is_job_alert(c)
        check(got == c["alert"], f"job alert {got!s:<5} {c['from'][:34]!r} {c['subject'][:40]!r}")
    for c in fx["propose"]:
        got = propose(c["kind"], c["status"])[0]
        check(got == c["proposed"], f"propose {c['kind']} on {c['status']} → {got} (want {c['proposed']})")
    tt = [({"rejection": 0.95, "interview_invitation": 0.05}, "rejection"),
          ({"rejection": 0.9, "interview_invitation": 0.9}, "interview"),     # invite for another role beats 'filled'
          ({"auto_reply": 0.9, "interview_invitation": 0.9}, "other"),
          ({"rejection": 0.6}, "review"), ({"ack": 0.95, "rejection": 0.1}, "ack"), ({"ack": 0.6}, "review"),
          ({"offer": 0.85, "ack": 0.9}, "offer"), ({"info_request": 0.9, "ack": 0.9}, "request"), ({}, "other")]
    for nouls, want in tt:
        got = JQ.decide({k: {"type": "noul", "noul": v} for k, v in nouls.items()})["kind"]
        check(got == want, f"jev truth table {nouls} → {got} (want {want})")
    q0, q1 = JQ.questions([]), JQ.questions([{"name": "TOMRA", "role": "Senior Python Software Engineer"}])
    check("company" not in q0 and "names_a_candidate_company" not in q0 and "company" in q1
          and q1["company"]["type"] == "choice" and all(v["type"] == "noul" for k, v in q1.items() if k != "company"),
          "jev request builder: company Choice + companion Noul only with candidates")
    st = JQ.state({"from": "a <b@c.de>", "from_domain": "c.de", "subject": "S", "date": "2026-11-02T08:00"}, [], "T")
    check(set(st["email"]) == {"from", "from_domain", "subject", "date", "text"} and "candidate_companies" not in st,
          "jev state: named email fields, no empty candidate list")
    c = JQ.decide_company({"names_a_candidate_company": {"noul": 0.2}, "company": {"choice": "TOMRA", "probabilities": {"TOMRA": 1}}},
                          [{"name": "TOMRA"}])
    check(c["name"] is None, "jev company: companion Noul no → no company even if the Choice picked one")
    apps = REPO / "cv" / "applications"
    for a, b, label in ((next(apps.glob("*_TOMRA_*/jd.md")), next(apps.glob("*_Mapbox_*/jd.md")), "full jd.md"),
                        (HERE / "fixtures" / "tomra_ledger.txt", HERE / "fixtures" / "mapbox_ledger.txt", "ledger summary")):
        fa, fb = fit(a.read_text(encoding="utf-8"), con)["fit"], fit(b.read_text(encoding="utf-8"), con)["fit"]
        check(fa is not None and fb is not None and fa > fb, f"fit TOMRA {fa}% > Mapbox {fb}% ({label})")
    print(f"{'all ok' if not bad else str(bad) + ' failed'}")
    return 1 if bad else 0


def run_abtest(a, con) -> int:
    import abtest as AB
    import jev_client
    if a.score:
        print(AB.score_csv(a.score))
        return 0
    today = today_of(a)
    fx_items, fx_rows = AB.fixture_items()
    fx_rows = fx_rows + tracker_rows() if not a.fixtures else fx_rows
    if a.stub:
        gold = {it["subject"] + "|" + jev_text(it)[:80]: it["gold_kind"] or "other" for it in fx_items}
        client = jev_client.StubClient(AB.stub_responder(gold))
    else:
        client = jev_client_or_none(a.debug)
        if not client:
            print("Jev: no API key — abtest needs one (or --stub)", file=sys.stderr)
            return 2
    print(f"fixtures: {len(fx_items)} cases …", file=sys.stderr)
    fx_recs = AB.run_arms(fx_items, fx_rows, con, client)
    real_recs, real_path, labels_path, note = [], None, None, ""
    if not a.fixtures and not a.stub:
        rows = tracker_rows()
        mails, real_path = AB.real_items(a.days, rows, today, a.refetch)
        print(f"real: {len(mails)} mails ({a.days} d) …", file=sys.stderr)
        real_recs = AB.run_arms(mails, rows, con, client)
        labels_path = DIGEST / f"abtest_labels_{today.isoformat()}.csv"
        AB.write_labels(real_recs, labels_path)
        note = f"last {a.days} days, {sum(1 for m in mails if m.get('sampled_alert'))} sampled job alerts; gold empty"
    md = AB.report(fx_recs, real_recs, client, today, real_path, labels_path, a.stub, note)
    out = a.out or DIGEST / f"abtest_{today.isoformat()}{'_STUB' if a.stub else ''}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"(written: {out})")
    return 0


# ---- CLI --------------------------------------------------------------------------------------
def load_json(path: str | None):
    if not path:
        return None
    return json.loads(sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8"))


def today_of(a) -> dt.date:
    return dt.date.fromisoformat(a.date) if getattr(a, "date", None) else dt.datetime.now(TZ).date()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fit")
    p.add_argument("jd", nargs="+", type=Path)
    p.add_argument("--json", action="store_true")
    p.add_argument("--top", type=int, default=5)
    p = sub.add_parser("jobs")
    p.add_argument("--date")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("followups")
    p.add_argument("--date")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("inbox")
    p.add_argument("mails", nargs="?", help="mails JSON file or - (stdin); omit with --google")
    p.add_argument("--google", action="store_true", help="fetch via the local Google login (gmail.readonly)")
    p.add_argument("--since", help="look-back start YYYY-MM-DD (default: `since`)")
    p.add_argument("--date")
    p.add_argument("--json", action="store_true")
    p.add_argument("--classifier", choices=["regex", "jev"], default="regex")
    p.add_argument("--debug", action="store_true", help="Jev request / response payloads to stderr (key redacted)")
    p = sub.add_parser("calendar")
    p.add_argument("events", nargs="?", help="events JSON file or - (stdin); omit with --google")
    p.add_argument("--google", action="store_true", help="fetch via the local Google login (calendar.readonly)")
    p.add_argument("--date")
    p.add_argument("--json", action="store_true")
    sub.add_parser("since").add_argument("--date")
    sub.add_parser("selftest")
    p = sub.add_parser("abtest", help="regex vs Jev inbox classifier on identical inputs (scripts/abtest.py)")
    p.add_argument("--days", type=int, default=45, help="real-mail window (default 45 = the follow-up window)")
    p.add_argument("--fixtures", action="store_true", help="fixtures only (no Gmail)")
    p.add_argument("--stub", action="store_true", help="recorded fake Jev answers (pipeline proof, not a result)")
    p.add_argument("--refetch", action="store_true", help="fetch the real mail set again")
    p.add_argument("--score", type=Path, help="score a filled labels CSV")
    p.add_argument("--out", type=Path)
    p.add_argument("--date")
    p.add_argument("--debug", action="store_true")
    p = sub.add_parser("run")
    p.add_argument("--date")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--google", action="store_true", help="Gmail + Calendar via the local Google login (the default when no "
                                                          "--connectors / --inbox / --calendar is given)")
    p.add_argument("--since", help="Gmail look-back start YYYY-MM-DD (default: the last digest)")
    p.add_argument("--classifier", choices=["regex", "jev"], default="regex", help="inbox classifier (default regex)")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--inbox", help="mails JSON (connector fallback)")
    p.add_argument("--no-gmail", action="store_true", help="skip the inbox")
    p.add_argument("--calendar", help="events JSON (connector fallback)")
    p.add_argument("--no-calendar", action="store_true")
    p.add_argument("--no-sheet", action="store_true", help="skip the Google Sheet read (follow-ups, inbox mapping)")
    p.add_argument("--connectors", help='one JSON {"mails": [..] | null, "events": [..] | null} from a file or "-" (stdin); '
                                         'null / missing key = that connector is not connected')
    p.add_argument("--pipeline", action="store_true", help="first run daily_pipeline.py --live --only search (appends to 每日職缺); "
                                                            "interactive only, refused when scheduled unless --yes")
    p.add_argument("--yes", action="store_true", help="allow --pipeline without a TTY")
    p.add_argument("--dry", action="store_true", help="print only, do not write cv/digest/")
    p.add_argument("--out", type=Path, help="write the digest here instead of cv/digest/YYYY-MM-DD.md")
    a = ap.parse_args(argv)
    con = ro_connect()

    if a.cmd == "fit":
        res = []
        for path in a.jd:
            r = fit(path.read_text(encoding="utf-8"), con)
            res.append({"jd": str(path), **r})
            if not a.json:
                print(f"== {path}")
                print_fit(r, a.top)
        if a.json:
            print(json.dumps(res if len(res) > 1 else res[0], ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "selftest":
        return selftest(con)
    if a.cmd == "abtest":
        return run_abtest(a, con)
    if a.cmd == "since":
        print(since(today_of(a)).isoformat())
        return 0
    today = today_of(a)
    if a.cmd == "jobs":
        j = new_jobs(today.isoformat())
        ranked = rank_jobs(j["rows"], con)
        if a.json:
            print(json.dumps({**j, "rows": None, "ranked": [{k: v for k, v in r.items() if k != "jd"} for r in ranked]},
                             ensure_ascii=False, indent=1))
        else:
            print(f"{len(ranked)} new postings ({j['day']}) {j['note']}")
            for r in ranked[: a.top]:
                print(f"  {r['fit'] if r['fit'] is not None else '–':>3}%  score {r.get('score') or '–':>2}  {r['company']} — {r['title']}"
                      f"  {'↓ ' + r['fit_note'] if r['low'] else 'missing: ' + ('; '.join(r['missing']) or r['fit_note'])}")
        return 0
    if a.cmd == "followups":
        fu = followups(today, tracker_rows(), con, a.days)
        if a.json:
            print(json.dumps(fu, ensure_ascii=False, indent=1, default=str))
        else:
            print(f"follow-up due ({SENT_STATUS}, no reply > {a.days} d, ≤ {fu['stale_days']} d): {len(fu['due'])}")
            for r in fu["due"]:
                print(f"  {r['company']} · {r['title']} · sent{'≈' if r['sent_inferred'] else ''} {r['sent']} · last contact {r['last_contact']} ({r['age']} d) → {r['suggest']}")
            print(f"outreach due: {len(fu['outreach_due'])} · stale > {fu['stale_days']} d: {fu['stale']} · undated: {len(fu['undated'])}"
                  f" · reply logged but status unchanged: {len(fu['reply_logged_status_unchanged'])}")
            for r in fu["undated"]:
                print(f"  undated: {r['tab']} row {r['row']} {r['company']} · {r['title']}")
            for r in fu["reply_logged_status_unchanged"]:
                print(f"  reply logged {r['reply']}: {r['tab']} row {r['row']} {r['company']} · {r['title']}")
        return 0
    if a.cmd == "inbox":
        rows = tracker_rows()
        try:
            mails = fetch_gmail(dt.date.fromisoformat(a.since) if a.since else since(today), rows) if a.google \
                else load_json(a.mails or "-") or []
        except Exception as e:   # auth / scope / network: say so, no traceback
            print(f"Gmail not read: {str(e)[:200]}", file=sys.stderr)
            return 2
        client = jev_client_or_none(a.debug) if a.classifier == "jev" else None
        out = inbox(mails, rows, con, "jev" if client else "regex", client)
        n_alert = sum(map(is_job_alert, mails))
        if a.json:
            print(json.dumps({"mails": len(mails), "job_alerts": n_alert, "kept": out}, ensure_ascii=False, indent=1))
        else:
            print(f"{len(mails)} mails, {n_alert} job alerts dropped (other/newsletter), {len(out)} kept")
            print("\n".join(f"{m['date']} {m['kind']:<9} {m['company'] or '?':<20} {m['subject']}  «{m['phrase']}»"
                            + (f"  → PROPOSE {m['status'] or '∅'} → {m['proposed_status']}" if m['proposed_status'] else "")
                            + (f"  ({m['note']})" if m['note'] else "") for m in out) or "no recruiter / ATS mail")
        return 0
    if a.cmd == "calendar":
        try:
            events = fetch_calendar(today) if a.google else load_json(a.events or "-") or []
        except Exception as e:
            print(f"Calendar not read: {str(e)[:200]}", file=sys.stderr)
            return 2
        out = calendar(events, today, tracker_rows(), con)
        print(json.dumps({"events": len(events), **out}, ensure_ascii=False, indent=1))
        return 0
    # run
    notes = []
    if a.pipeline and not a.yes and (os.environ.get("CLAUDE_SCHEDULED") == "1" or not sys.stdin.isatty()):
        print("--pipeline writes the Sheet tab 每日職缺 and runs only on an explicit user request: refused "
              "(scheduled or no TTY). Add --yes if the user asked for it.", file=sys.stderr)
        return 2
    conn = load_json(a.connectors) if a.connectors else {}
    if a.connectors:
        a.inbox = a.inbox or ("-mails-" if conn.get("mails") is not None else None)
        a.calendar = a.calendar or ("-events-" if conn.get("events") is not None else None)
    if a.pipeline:
        r = subprocess.run([sys.executable, str(TRACKER / "daily_pipeline.py"), "--live", "--only", "search"], cwd=REPO)
        if r.returncode:
            notes.append(f"daily_pipeline search exited {r.returncode} (see data/pipeline/logs/scheduler.log).")
    jobs = new_jobs(today.isoformat())
    jobs["ranked"] = rank_jobs(jobs["rows"], con)
    if not jobs["day"]:
        notes.append("The 04:00 daily pipeline has no search for today: check Task Scheduler, or rerun with --pipeline.")
    rows, fu = [], None
    if not a.no_sheet:
        try:
            rows = tracker_rows()
        except Exception as e:  # expired Google login, no network: the digest still goes out
            notes.append(f"Google Sheet not read ({str(e)[:120]}); fix: google_auth.py --auth.")
    use_google = a.google or not (a.connectors or a.inbox or a.calendar)   # default: local Google login, headless
    raw_mails = None
    if a.inbox:
        raw_mails = (conn.get("mails") if a.inbox == "-mails-" else load_json(a.inbox)) or []
    start = dt.date.fromisoformat(a.since) if a.since else since(today)
    if not a.inbox and use_google and not a.no_gmail:
        try:   # one fetch over the follow-up window: recent mail → Inbox, older → pending proposals + follow-up check
            raw_mails = fetch_gmail(min(start, today - dt.timedelta(days=STALE_DAYS)), rows)
        except Exception as e:
            notes.append(f"Gmail not read ({str(e)[:110]}).")
    client = jev_client_or_none(a.debug) if a.classifier == "jev" and raw_mails else None
    if a.classifier == "jev" and raw_mails and not client:
        notes.append("Jev: no API key — inbox classified with regex.")
    all_mails = None if (a.no_gmail or raw_mails is None) else inbox(raw_mails, rows, con, "jev" if client else "regex", client)
    mails = None if all_mails is None else [m for m in all_mails if m["date"] >= start.isoformat()]
    older = [m for m in all_mails or [] if m["date"] < start.isoformat() and m["proposed_status"]]
    if mails is not None:
        n_alert = sum(map(is_job_alert, raw_mails))
        if n_alert:
            notes.append(f"{n_alert} job-alert mail(s) skipped (other/newsletter).")
    if not a.no_sheet and rows:
        fu = followups(today, rows, con, a.days,
                       mail_companies={norm_co(m["company"]) for m in all_mails or [] if m["company"] and m["kind"] != "other"})
    raw_events = None
    if a.calendar:
        raw_events = (conn.get("events") if a.calendar == "-events-" else load_json(a.calendar)) or []
    elif use_google and not a.no_calendar:
        try:
            raw_events = fetch_calendar(today)
        except Exception as e:
            notes.append(f"Calendar not read ({str(e)[:110]}).")
    cal = None if (a.no_calendar or raw_events is None) else calendar(raw_events, today, rows, con)
    md = shorten(digest(today, jobs, fu, mails, cal, notes, a.top, older))
    if not a.dry:
        out = a.out or DIGEST / f"{today.isoformat()}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        md += f"\n(written: {out.relative_to(REPO) if out.is_relative_to(REPO) else out})\n"
    print(md, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
