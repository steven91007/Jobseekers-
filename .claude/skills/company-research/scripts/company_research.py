#!/usr/bin/env python3
"""company_research.py - skeleton, validation and rendering for /company-research (stdlib, DB read-only).

  init    --jd <Co> [--force]                         skeleton $D/02_company.json (JD brief, JD concepts, candidate cards)
  init    --company "<Name>" --jd-file <f> --out <dir> same, for a job without a library_db.py status row
  check   <json> [--today YYYY-MM-DD]                 every fact sourced (url + access date), news <= 12 months,
                                                      hooks cite existing concepts/cards/bullets, no claim beyond the library
  render  <json> [--out path]                         -> 02_company.md next to the json
  excerpt --jd <Co> [--max N]                         compact block for other skills' packs (interview-prep, outreach)

Optional sections interview_process / interview_reports hold publicly reported interview rounds and questions
(Glassdoor, Blind, Reddit, kununu, company careers pages). They feed /interview-prep question selection only and are
never evidence: company_text() leaves them out.

The main agent does the web research (WebSearch / web-fetch); this script only structures and validates.
"""
import argparse, datetime as dt, json, os, re, sqlite3, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, *[".."] * 4))
sys.path.insert(0, os.path.join(ROOT, ".claude", "skills", "interview-prep", "scripts"))
from truth import db, read, conf_items, corpus_for, check_numbers, mc_phase_claims  # noqa: E402

LIB = os.path.join(ROOT, ".claude", "skills", "tailor-cv", "scripts", "library_db.py")
SNAP_FIELDS = ("what", "size", "hq", "office", "parent", "revenue", "other")
SNAP_REQUIRED = ("what", "size", "hq", "office")
SRC_KINDS = {"company-site", "press-release", "financial-report", "news", "trade-press", "jd", "job-post",
             "blog", "github", "review-site", "forum", "registry", "other"}
CONFIRM_KINDS = {"company-site", "press-release", "jd", "job-post", "blog", "github"}
JD_KINDS = {"jd", "job-post"}
TECH_STATUS = ("confirmed", "jd-listed", "inferred")  # in use per a company source | named in a JD as requirement/wish | inferred
HOOK_USE = ("all", "interview-only")  # interview-only: cites a library bullet the submitted CV does not show
HOOKS_MIN, HOOKS_MAX, Q_MIN, Q_MAX = 3, 5, 3, 6
NEWS_DAYS = 365
OUT_JSON, OUT_MD = "02_company.json", "02_company.md"
SECTIONS = ["snapshot", "product_team", "tech_stack", "news", "hooks", "risks", "questions"]
OPTIONAL = ["interview_process", "interview_reports"]   # older files may lack them
REPORT_KINDS = {"review-site", "forum", "blog", "company-site", "job-post", "jd", "other"}
STAGES = ("recruiter", "technical", "coding", "take-home", "system-design", "hiring-manager", "team", "onsite",
          "final", "unknown")
SEEN = ("page", "snippet")        # read on the page itself | only a search-result snippet (login wall)
REPORT_MONTHS = 36                 # older reports: warn (process may have changed)
PRIVATE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b[\w.+-]+\s+(?:at|\[at\])\s+[\w-]+\s+(?:dot|\[dot\])\s+\w+\b"
                     r"|linkedin\.com/in/|\+\d[\d ()/-]{7,}\d|\b\d{3,4}[/-]\d{3,4}[/-]\d{3,5}\b|\b0\d{2,4}[ /-]?\d{6,8}\b", re.I)
HANDLE = re.compile(r"(?<![\w.`])@[A-Za-z_]\w{1,}(?![\w(`])")   # warn only: "@lru_cache" / "`@dataclass`" are Python
PERSON = re.compile(r"(?i:interviewer|recruiter|hiring manager|\bhr\b|manager|reviewer)\s*(?i:was|named|called|is|:)?\s*"
                    r"[A-Z][a-z]+\s+[A-Z][a-z]+")
ROLE_STOP = set("senior junior lead staff principal head intern working student m w d f x i ii iii iv and of the for in "
                "with at".split())


def lib(*args):
    r = subprocess.run([sys.executable, LIB, *args], capture_output=True, text=True, cwd=ROOT)
    return r.returncode, (r.stdout if r.returncode == 0 else r.stdout + r.stderr).strip()


def app(jd):
    row = db().execute("select jd,company,title,dir,country from applications where jd=? collate nocase", (jd,)).fetchone()
    if not row:
        sys.exit(f"unknown JD '{jd}' (library_db.py status)")
    d = row[3] or ""
    if d and not os.path.isabs(d):
        d = os.path.join(ROOT, d) if os.path.isdir(os.path.join(ROOT, d)) else os.path.join(ROOT, "cv", d)
    return dict(jd=row[0], company=row[1], title=row[2], dir=d, country=row[4] or "")


def picks(jd):
    if not jd:
        return []
    return db().execute("select kind,code,lead from application_picks where jd=? order by position", (jd,)).fetchall()


def jd_meta(text):
    meta = {}
    for k, key in (("Company", "company"), ("Role", "title"), ("Source URL", "url"), ("Location / remote", "location"),
                   ("Posted", "posted"), ("Captured on", "captured")):
        m = re.search(r"\*\*" + re.escape(k) + r":\*\*\s*(.+)", text)
        if m:
            meta[key] = m.group(1).strip()
    if "url" in meta:
        meta["url"] = meta["url"].split()[0]
    return meta


def match_concepts(d):
    """-> {concept: {'level': req|pref|kw, 'coverage': .., 'terms': [..], 'bullets': [..]}} from library_db.py match."""
    kw = os.path.join(d, "07_job_keywords.json")
    if not os.path.exists(kw):
        return {}
    rc, out = lib("match", kw)
    if rc != 0:
        return {}
    res, rank = {}, {"req": 0, "pref": 1, "kw": 2}
    for line in out.splitlines():
        m = re.match(r"^\[(req|pref|kw)\]\s+(.+?)\s+—\s+(.+?)\s+→\s+([A-Z][A-Z_0-9]+)(.*)$", line)
        if not m:
            continue
        lvl, term, cov, code, rest = m.groups()
        bm = re.search(r"bullets\s+([A-Za-z0-9,]+)", rest)
        e = res.setdefault(code, {"level": lvl, "coverage": cov, "terms": [], "bullets": []})
        if rank[lvl] < rank[e["level"]]:
            e["level"] = lvl
        if cov == "covered":
            e["coverage"] = "covered"
        e["terms"].append(term if cov == "covered" else f"{term} [{cov}]")
        for b in (bm.group(1).split(",") if bm else []):
            if b and b not in e["bullets"]:
                e["bullets"].append(b)
    return res


def gap_terms(jd):
    """Gap / do-not-claim terms for this JD (library_db.py gaps --jd): candidate text must not claim them."""
    if not jd:
        return set()
    rc, out = lib("gaps", "--jd", jd)
    if rc != 0:
        return set()
    terms, sec = set(), None
    for line in out.splitlines():
        if line.startswith("## "):
            sec = "gap" if line.startswith(("## gap", "## do not claim")) else ("tnc" if "term not claimable" in line else None)
            continue
        m = re.match(r"^- ([A-Z][A-Z_0-9]+)\b(?: \(w\d+\))?\s*(.*)$", line)
        if not sec or not m:
            continue
        pieces = m.group(2).split(" · ")
        if sec == "gap":  # whole concept is a gap / do-not-claim: its name and every JD term count
            terms.add(re.sub(r"^JD:\s*", "", pieces[0]).strip().lower())
        for p in pieces[1:]:
            tm = re.match(r"^(?:JD:\s*)?(.+?)\s*\[([^\]]+)\]$", p.strip())
            if tm and (sec == "gap" or tm.group(2) in ("gap", "do not claim")):
                terms.add(tm.group(1).strip().lower())
    return terms


def cv_bullets(jd):
    """Bullet codes recorded as picks for this application (= what the submitted CV shows); empty set if unknown."""
    return {c for k, c, _ in picks(jd) if k == "bullet"}


def hook_use(h, on_cv):
    """'interview-only' when set, or when any cited bullet is not on the CV; else 'all'."""
    if h.get("use") == "interview-only" or (on_cv and any(b not in on_cv for b in h.get("bullets") or [])):
        return "interview-only"
    return "all"


def candidate_cards(jd):
    con = db()
    picked = {c for k, c, _ in picks(jd) if k == "card"}
    picked_b = {c for k, c, _ in picks(jd) if k == "bullet"}
    out = []
    for code, name, status in con.execute("select code,name,status from projects order by rowid"):
        t = con.execute("select title from project_titles where project_code=? order by ord", (code,)).fetchone()
        bl = []
        for b, bst, body in con.execute("select code,status,body from bullets where project_code=? order by code", (code,)):
            cs = sorted({r[0] for r in con.execute(
                "select concept_code from bullet_leads where bullet_code=? and concept_code is not null union "
                "select concept_code from concept_evidence where kind='bullet' and code=?", (b, b))})
            bl.append({"code": b, "status": bst, "picked": b in picked_b, "concepts": cs,
                       "body": body if len(body) <= 170 else body[:165] + " …"})
        out.append({"code": code, "name": name, "title": t[0] if t else name, "status": status,
                    "picked": code in picked, "bullets": bl})
    return out


# ---------------------------------------------------------------- init
def cmd_init(a):
    if a.jd:
        ap_ = app(a.jd)
        d, jd, company, title = ap_["dir"], ap_["jd"], ap_["company"], ap_["title"]
        jd_text = read(os.path.join(d, "jd.md"))
    else:
        if not (a.company and a.jd_file and a.out):
            sys.exit("init needs --jd <Co>, or --company + --jd-file + --out")
        d, jd, company = os.path.abspath(a.out), "", a.company
        jd_text = read(a.jd_file)
        title = jd_meta(jd_text).get("title", "")
        os.makedirs(d, exist_ok=True)
    path = os.path.join(d, OUT_JSON)
    if os.path.exists(path) and not a.force:
        sys.exit(f"{path} exists (use --force to overwrite)")
    meta = jd_meta(jd_text)
    data = {
        "jd": jd, "company": company, "title": title, "dir": d,
        "researched": dt.date.today().isoformat(),
        "office_city": "",
        "_help": {
            "sources": "[{id:'S1', url, title, publisher, published:'YYYY-MM-DD'|'', accessed:'YYYY-MM-DD', kind}] "
                       f"kind in {sorted(SRC_KINDS)}",
            "facts": "every item in snapshot/product_team/tech_stack/news/hooks/risks carries src:['S1',...]",
            "snapshot": f"[{{field in {list(SNAP_FIELDS)}, text, src}}]; need {list(SNAP_REQUIRED)}",
            "product_team": "[{text, src}] the JD's team/product, named teams, sibling postings",
            "tech_stack": "[{tech, status:'confirmed'|'inferred', evidence, src}] confirmed = named by the company itself",
            "news": f"[{{date:'YYYY-MM-DD', headline, text, src}}] within {NEWS_DAYS} days of researched",
            "hooks": f"{HOOKS_MIN}-{HOOKS_MAX} x {{hook, company_fact, src, concepts:[codes], cards:[codes], bullets:[codes], "
                     "candidate_angle, gap_note, notes}}; candidate_angle uses only the cited bullets' facts",
            "risks": "[{text, kind:'risk'|'unknown', src, searched}] unknown = searched and not found (src may be empty)",
            "questions": f"{Q_MIN}-{Q_MAX} x {{question, why, concepts, src}} feeds interview-prep kind 'ask'",
            "interview_process": "[{text, date:'YYYY'|'YYYY-MM'|'YYYY-MM-DD'|'', src}] rounds, take-home, duration, "
                                 "language, as reported (careers page, posting, review sites)",
            "interview_reports": f"[{{id:'R1', question, stage in {list(STAGES)}, role, date:'YYYY-MM', src, "
                                 f"seen:'page'|'snippet', notes}}] questions candidates report being asked; paraphrase "
                                 f"(<= 300 chars), no names/handles/emails; src kind in {sorted(REPORT_KINDS)}",
        },
        "jd_brief": {"meta": meta, "brief": read(os.path.join(d, "01_jd.md")).strip()[:1500]},
        "jd_concepts": match_concepts(d),
        "candidate_cards": candidate_cards(jd),
        "sources": [], "snapshot": [], "product_team": [], "tech_stack": [], "news": [],
        "hooks": [], "risks": [], "questions": [], "interview_process": [], "interview_reports": [], "notes": "",
    }
    if meta.get("url"):
        data["sources"].append({"id": "S1", "url": meta["url"], "title": f"{company} — {title} (job posting)",
                                "publisher": company, "published": meta.get("posted", "")[:10],
                                "accessed": (meta.get("captured") or data["researched"])[:10], "kind": "jd"})
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"wrote {path}")
    print(f"JD concepts: {len(data['jd_concepts'])} · cards: {len(data['candidate_cards'])} "
          f"(picked: {', '.join(c['code'] for c in data['candidate_cards'] if c['picked']) or '-'})")


# ---------------------------------------------------------------- check
def pdate(s):
    """'YYYY-MM-DD' | 'YYYY-MM' | 'YYYY' -> date (first day) or None."""
    s = (s or "").strip()
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", s):
        return None
    try:
        return dt.datetime.strptime(s, {10: "%Y-%m-%d", 7: "%Y-%m", 4: "%Y"}[len(s)]).date()
    except ValueError:
        return None


def check_data(data, today):
    errs, warns = [], []
    for k in ("company", "researched", "sources") + tuple(SECTIONS):
        if k not in data:
            errs.append(f"missing top-level key '{k}'")
    if errs:
        return errs, warns
    res = pdate(data["researched"])
    if not res or len(data["researched"]) != 10:
        errs.append("researched must be YYYY-MM-DD")
        res = today
    if res > today:
        errs.append(f"researched {res} is in the future")
    # sources
    src = {}
    for i, s in enumerate(data["sources"], 1):
        t = f"source #{i}"
        sid = s.get("id", "")
        if not re.fullmatch(r"S\d+", sid or ""):
            errs.append(f"{t} id {sid!r} must look like S1")
        if sid in src:
            errs.append(f"{t} duplicate id {sid}")
        if not re.match(r"^https?://[^\s/]+\.[^\s]+$", s.get("url", "")):
            errs.append(f"{t} ({sid}) bad url {s.get('url')!r}")
        acc = pdate(s.get("accessed", ""))
        if not acc or len(s.get("accessed", "")) != 10:
            errs.append(f"{t} ({sid}) needs accessed YYYY-MM-DD")
        elif acc > today:
            errs.append(f"{t} ({sid}) accessed {acc} is in the future")
        elif (res - acc).days > 90:
            warns.append(f"{t} ({sid}) accessed {acc}, more than 90 days before research date: re-check")
        if s.get("published") and not pdate(s["published"]):
            errs.append(f"{t} ({sid}) bad published date {s['published']!r}")
        if s.get("kind") not in SRC_KINDS:
            errs.append(f"{t} ({sid}) kind {s.get('kind')!r} not in {sorted(SRC_KINDS)}")
        if not s.get("title"):
            warns.append(f"{t} ({sid}) has no title")
        src[sid] = s
    used = set()

    def need_src(t, item, allow_empty=False):
        ids = item.get("src") or []
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.split(",") if x.strip()]
        if not ids and not allow_empty:
            errs.append(f"{t} has no source (src)")
        for x in ids:
            if x not in src:
                errs.append(f"{t} cites unknown source {x}")
            used.add(x)
        return ids

    # snapshot
    fields = set()
    for i, s in enumerate(data["snapshot"], 1):
        t = f"snapshot #{i}"
        if s.get("field") not in SNAP_FIELDS:
            errs.append(f"{t} field {s.get('field')!r} not in {list(SNAP_FIELDS)}")
        fields.add(s.get("field"))
        if not (s.get("text") or "").strip():
            errs.append(f"{t} empty text")
        need_src(t, s)
    for f in SNAP_REQUIRED:
        if f not in fields:
            errs.append(f"snapshot lacks field '{f}'")
    if data.get("office_city") and not any(data["office_city"].lower() in (s.get("text") or "").lower()
                                           for s in data["snapshot"] if s.get("field") == "office"):
        warns.append(f"snapshot 'office' does not name the JD's office city {data['office_city']!r}")
    for i, s in enumerate(data["product_team"], 1):
        if not (s.get("text") or "").strip():
            errs.append(f"product_team #{i} empty text")
        need_src(f"product_team #{i}", s)
    if not data["product_team"]:
        errs.append("product_team is empty")
    # tech stack
    for i, s in enumerate(data["tech_stack"], 1):
        t = f"tech_stack #{i} ({s.get('tech')})"
        if not s.get("tech"):
            errs.append(f"{t} no tech name")
        st = s.get("status")
        ids = need_src(t, s)
        if st not in TECH_STATUS:
            errs.append(f"{t} status must be one of {'|'.join(TECH_STATUS)}")
        elif st == "confirmed" and ids and not any(src.get(x, {}).get("kind") in CONFIRM_KINDS for x in ids):
            errs.append(f"{t} confirmed needs a company-owned source ({sorted(CONFIRM_KINDS)}); else mark inferred")
        elif st == "jd-listed" and ids and not any(src.get(x, {}).get("kind") in JD_KINDS for x in ids):
            errs.append(f"{t} jd-listed needs a jd/job-post source")
        if st == "inferred" and not (s.get("evidence") or "").strip():
            errs.append(f"{t} inferred needs evidence (why it is inferred)")
    if not any(s.get("status") in ("confirmed", "jd-listed") for s in data["tech_stack"]):
        warns.append("tech_stack has no confirmed item")
    # news
    for i, n in enumerate(data["news"], 1):
        t = f"news #{i}"
        d = pdate(n.get("date", ""))
        ids = need_src(t, n)
        if not d or len(n.get("date", "")) < 7:
            errs.append(f"{t} needs date YYYY-MM-DD")
        else:
            if d > res:
                errs.append(f"{t} date {d} after research date")
            elif (res - d).days > NEWS_DAYS:
                errs.append(f"{t} date {d} older than {NEWS_DAYS} days: move it to snapshot/product_team or drop it")
            for x in ids:
                p = pdate(src.get(x, {}).get("published", ""))
                if p and abs((p - d).days) > 45:
                    warns.append(f"{t} date {d} differs from {x} published {p} by > 45 days")
        if not (n.get("headline") or "").strip():
            errs.append(f"{t} no headline")
    if len(data["news"]) < 2:
        warns.append(f"news has {len(data['news'])} items (aim for 2+ in the last 12 months)")
    # hooks
    con = db()
    concepts = {r[0]: r[1] for r in con.execute("select code,claim from concepts")}
    cards = {r[0]: r[1] for r in con.execute("select code,status from projects")}
    bullets = {r[0]: (r[1], r[2]) for r in con.execute("select code,project_code,status from bullets")}
    jd = data.get("jd") or ""
    d = data.get("dir") or ""
    if jd:
        try:
            d = app(jd)["dir"]
        except SystemExit:
            errs.append(f"jd '{jd}' not in applications")
            jd = ""
    cv_text = read(os.path.join(d, "03_cv.md")) if d else ""
    jdc = match_concepts(d) if d else {}
    pk = picks(jd)
    picked_cards = {c for k, c, _ in pk if k == "card"}
    on_cv = cv_bullets(jd)
    gterms = gap_terms(jd)
    conf, conf_skipped, _dup = conf_items()
    ev = {}
    for c, k, code in con.execute("select concept_code,kind,code from concept_evidence where kind='bullet'"):
        ev.setdefault(code, set()).add(c)
    for b, c in con.execute("select bullet_code,concept_code from bullet_leads where concept_code is not null"):
        ev.setdefault(b, set()).add(c)
    hooks = data["hooks"]
    if not HOOKS_MIN <= len(hooks) <= HOOKS_MAX:
        errs.append(f"hooks: {len(hooks)} (need {HOOKS_MIN}-{HOOKS_MAX})")
    seen_cards = set()
    for i, h in enumerate(hooks, 1):
        t = f"hook #{i}"
        for f in ("hook", "company_fact", "candidate_angle"):
            if not (h.get(f) or "").strip():
                errs.append(f"{t} empty {f}")
        need_src(t, h)
        cs, ks, bs = (h.get(x) or [] for x in ("concepts", "cards", "bullets"))
        if not cs:
            errs.append(f"{t} cites no concept")
        if not ks:
            errs.append(f"{t} cites no card")
        if not bs:
            errs.append(f"{t} cites no bullet")
        for c in cs:
            if c not in concepts:
                errs.append(f"{t} unknown concept {c}")
            elif concepts[c] in ("gap", "do not claim"):
                errs.append(f"{t} concept {c} is '{concepts[c]}' in the library: a hook cannot claim it (use gap_note)")
            elif jdc and c not in jdc:
                warns.append(f"{t} concept {c} is not among the JD's matched concepts")
        for k in ks:
            if k not in cards:
                errs.append(f"{t} unknown card {k}")
            elif pk and k not in picked_cards:
                warns.append(f"{t} card {k} is not one of the CV's picked cards {sorted(picked_cards)}: the reader has not seen it")
            seen_cards.add(k)
        for b in bs:
            if b not in bullets:
                errs.append(f"{t} unknown bullet {b}")
                continue
            if bullets[b][0] not in ks:
                errs.append(f"{t} bullet {b} belongs to card {bullets[b][0]}, not in cards {ks}")
            if bullets[b][1] == "Legacy wording":
                warns.append(f"{t} bullet {b} is legacy wording; prefer a Production/POC bullet")
        use = h.get("use", "all")
        if use not in HOOK_USE:
            errs.append(f"{t} use {use!r} must be one of {HOOK_USE}")
        off = [b for b in bs if b in bullets and on_cv and b not in on_cv]
        if off and use != "interview-only":
            errs.append(f"{t} cites bullet(s) {off} not on the submitted CV: set use 'interview-only' or cite a CV bullet")
        if cs and bs and not any(set(cs) & ev.get(b, set()) for b in bs if b in bullets):
            warns.append(f"{t} none of bullets {bs} is library evidence for concepts {cs}")
        angle = h.get("candidate_angle") or ""
        low = angle.lower()
        for g in sorted(gterms):
            if re.search(r"(?<![a-z])" + re.escape(g) + r"(?![a-z])", low):
                errs.append(f"{t} candidate_angle names gap / do-not-claim term '{g}': move it to gap_note")
        if "MC" in ks and mc_phase_claims(angle):
            errs.append(f"{t} MaterialCheck: only phase 1 is in production (CONF-an); phases 2–3 are the plan")
        hard = "\n".join(corpus_for(con, b, cv_text) for b in bs if b in bullets)
        e2, w2 = check_numbers(t, angle, hard, h.get("notes"), conf, conf_skipped, ",".join(bs))
        errs += [e.replace("number", "candidate number", 1) for e in e2]
        warns += w2
        if h.get("gap_note") and not re.search(r"\b(not|no|haven't|never|gap|missing|learn)\b", h["gap_note"], re.I):
            warns.append(f"{t} gap_note should say plainly what is missing")
    if pk and picked_cards - seen_cards:
        warns.append(f"picked card(s) {sorted(picked_cards - seen_cards)} carry no hook")
    # risks
    for i, r in enumerate(data["risks"], 1):
        t = f"risk #{i}"
        if r.get("kind") not in ("risk", "unknown"):
            errs.append(f"{t} kind must be risk|unknown")
        if not (r.get("text") or "").strip():
            errs.append(f"{t} empty text")
        unk = r.get("kind") == "unknown"
        need_src(t, r, allow_empty=unk)
        if unk and not (r.get("searched") or "").strip():
            errs.append(f"{t} unknown needs 'searched' (where you looked)")
    if not data["risks"]:
        warns.append("risks is empty")
    # questions
    qs = data["questions"]
    if not Q_MIN <= len(qs) <= Q_MAX:
        errs.append(f"questions: {len(qs)} (need {Q_MIN}-{Q_MAX})")
    for i, q in enumerate(qs, 1):
        t = f"question #{i}"
        if not (q.get("question") or "").strip().endswith("?"):
            errs.append(f"{t} must be a question ending in '?'")
        if not (q.get("why") or "").strip():
            errs.append(f"{t} needs why (what to listen for)")
        for c in q.get("concepts") or []:
            if c not in concepts:
                errs.append(f"{t} unknown concept {c}")
        need_src(t, q, allow_empty=True)
    check_interview(data, src, need_src, res, errs, warns)
    for sid in src:
        if sid not in used:
            warns.append(f"source {sid} is not cited by any item")
    return errs, warns


def months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def check_interview(data, src, need_src, res, errs, warns):
    """interview_process / interview_reports: sourced, dated, staged, no private data. Never evidence."""
    for i, p in enumerate(data.get("interview_process") or [], 1):
        t = f"interview_process #{i}"
        if not (p.get("text") or "").strip():
            errs.append(f"{t} empty text")
        need_src(t, p)
        if p.get("date") and not pdate(p["date"]):
            errs.append(f"{t} bad date {p['date']!r}")
        if PRIVATE.search(p.get("text") or ""):
            errs.append(f"{t} contains an email, profile link or phone number: remove private data")
        if PERSON.search(p.get("text") or ""):
            warns.append(f"{t} seems to name a person: drop names")
    reps = data.get("interview_reports") or []
    title = {w for w in re.findall(r"[a-z]+", (data.get("title") or "").lower()) if w not in ROLE_STOP}
    ids = set()
    for i, r in enumerate(reps, 1):
        rid = str(r.get("id", "") or "")
        t = f"interview_report #{i} ({rid})"
        if not re.fullmatch(r"R\d+", rid or ""):
            errs.append(f"{t} id must look like R1")
        elif rid in ids:
            errs.append(f"{t} duplicate id {rid}")
        ids.add(rid)
        q = (r.get("question") or "").strip()
        if not q:
            errs.append(f"{t} empty question")
        elif len(q) > 300:
            warns.append(f"{t} question is {len(q)} chars: paraphrase it (<= 300), do not copy a review")
        if r.get("stage") not in STAGES:
            errs.append(f"{t} stage {r.get('stage')!r} not in {list(STAGES)}")
        if r.get("seen", "page") not in SEEN:
            errs.append(f"{t} seen {r.get('seen')!r} must be page|snippet")
        d = pdate(r.get("date", ""))
        if not d:
            errs.append(f"{t} needs date YYYY-MM (when the candidate was interviewed, or the post date)")
        elif d > res:
            errs.append(f"{t} date {d} after research date")
        elif months_between(d, res) > REPORT_MONTHS:
            warns.append(f"{t} dated {r['date']}, older than {REPORT_MONTHS} months: the process may have changed")
        role = (r.get("role") or "").strip()
        if not role:
            warns.append(f"{t} has no role: say which position the report was for")
        elif title and not title & {w for w in re.findall(r"[a-z]+", role.lower()) if w not in ROLE_STOP}:
            warns.append(f"{t} role {role!r} shares no word with the JD title: weaker signal for this interview")
        ids_ = need_src(t, r)
        if ids_ and not any(src.get(x, {}).get("kind") in REPORT_KINDS for x in ids_):
            errs.append(f"{t} needs a source of kind {sorted(REPORT_KINDS)} (where candidates report interviews)")
        for f in ("question", "notes", "role"):
            v = str(r.get(f) or "")
            if PRIVATE.search(v):
                errs.append(f"{t} {f} contains an email, profile link or phone number: remove private data")
            if HANDLE.search(v):
                warns.append(f"{t} {f} has an @handle: drop it unless it is code (put code in backticks)")
            if PERSON.search(v):
                warns.append(f"{t} {f} seems to name a person: drop names")
    if not reps and not any(x.get("kind") == "unknown" and "interview" in (x.get("text", "") + x.get("searched", "")).lower()
                            for x in data.get("risks") or []):
        warns.append("no interview_reports: add them, or a risk of kind 'unknown' saying the interview search found "
                     "nothing (searched: Glassdoor, Blind, Reddit, kununu, careers page)")


def selftest():
    """Each mutation of a minimal valid file must raise the named error; the base must pass."""
    import copy
    T = dt.date(2026, 11, 2)
    s = lambda i, **k: dict(dict(id=f"S{i}", url=f"https://example.com/{i}", title="t", publisher="p",
                                 published="2026-05-01", accessed="2026-11-02", kind="press-release"), **k)
    hook = dict(hook="h", company_fact="f", src=["S1"], concepts=["CV"], cards=["VI"], bullets=["VI2"],
                candidate_angle="Grew training data from 105 to 418 images.", gap_note="", notes="")
    base = dict(jd="", company="X", title="Python Software Engineer", researched="2026-11-02", office_city="",
                sources=[s(1), s(2, kind="trade-press"), s(3, kind="review-site")],
                snapshot=[dict(field=f, text="x", src=["S1"]) for f in SNAP_REQUIRED],
                product_team=[dict(text="x", src=["S1"])],
                tech_stack=[dict(tech="Python", status="confirmed", evidence="jd", src=["S1"])],
                news=[dict(date="2026-05-01", headline="n", text="x", src=["S1"]), dict(date="2026-06-01", headline="m", text="y", src=["S2"])],
                hooks=[dict(hook) for _ in range(3)], risks=[dict(kind="unknown", text="x", src=[], searched="web")],
                questions=[dict(question=f"q{i}?", why="w", concepts=[], src=[]) for i in range(3)],
                interview_process=[dict(text="Recruiter call, then a technical interview.", date="2026-03", src=["S3"])],
                interview_reports=[dict(id="R1", question="Walk me through a Python service you built.", stage="technical",
                                        role="Software Engineer", date="2026-03", src=["S3"], seen="snippet", notes="")])
    cases = [
        ("base passes", lambda d: None, None),
        ("source without access date", lambda d: d["sources"][0].pop("accessed"), "needs accessed"),
        ("fact without source", lambda d: d["product_team"][0].update(src=[]), "has no source"),
        ("unknown source id", lambda d: d["snapshot"][0].update(src=["S9"]), "unknown source S9"),
        ("news older than 12 months", lambda d: d["news"][0].update(date="2025-09-01"), "older than"),
        ("confirmed tech from trade press", lambda d: d["tech_stack"][0].update(src=["S2"]), "confirmed needs"),
        ("unknown concept", lambda d: d["hooks"][0].update(concepts=["NOPE"]), "unknown concept NOPE"),
        ("gap concept as hook", lambda d: d["hooks"][0].update(concepts=["EDGE"]), "cannot claim"),
        ("bullet from another card", lambda d: d["hooks"][0].update(bullets=["PS2"]), "belongs to card PS"),
        ("invented candidate number", lambda d: d["hooks"][0].update(candidate_angle="Grew data 7× to 418 images."), "candidate number '7×'"),
        ("MaterialCheck phase 3 shipped", lambda d: d["hooks"][0].update(cards=["MC"], bullets=["MC4"], concepts=["OWNERSHIP_PM"],
                                                                         candidate_angle="Delivered phase 3 in production."), "phases 2–3"),
        ("MaterialCheck phase 1 in production is fine", lambda d: d["hooks"][0].update(cards=["MC"], bullets=["MC4"], concepts=["OWNERSHIP_PM"],
                                                                         candidate_angle="Phase 1 runs in production."), None),
        ("too few hooks", lambda d: d["hooks"].pop(), "hooks: 2"),
        ("unknown without searched", lambda d: d["risks"][0].pop("searched"), "needs 'searched'"),
        ("bad tech status", lambda d: d["tech_stack"][0].update(status="maybe"), "status must be one of"),
        ("jd-listed from trade press", lambda d: d["tech_stack"][0].update(status="jd-listed", src=["S2"]), "jd-listed needs"),
        ("jd-listed from the JD passes", lambda d: d["sources"][0].update(kind="jd") or d["tech_stack"][0].update(status="jd-listed"), None),
        ("TOMRA: CV bullet passes", lambda d: d.update(jd="TOMRA"), None),
        ("TOMRA: off-CV bullet without interview-only", lambda d: d.update(jd="TOMRA") or d["hooks"][0].update(
            bullets=["VI3"], concepts=["TYPESCRIPT_UI"], candidate_angle="Built a TypeScript viewer."), "not on the submitted CV"),
        ("TOMRA: off-CV bullet marked interview-only passes", lambda d: d.update(jd="TOMRA") or d["hooks"][0].update(
            bullets=["VI3"], concepts=["TYPESCRIPT_UI"], candidate_angle="Built a TypeScript viewer.", use="interview-only"), None),
        ("bad use tag", lambda d: d["hooks"][0].update(use="sometimes"), "use 'sometimes'"),
        ("report from a press release", lambda d: d["interview_reports"][0].update(src=["S1"]), "needs a source of kind"),
        ("report with bad stage", lambda d: d["interview_reports"][0].update(stage="vibes"), "stage 'vibes'"),
        ("report dated after research", lambda d: d["interview_reports"][0].update(date="2027-01"), "after research date"),
        ("report without date", lambda d: d["interview_reports"][0].update(date=""), "needs date"),
        ("duplicate report id", lambda d: d["interview_reports"].append(dict(d["interview_reports"][0])), "duplicate id R1"),
        ("report with a profile link", lambda d: d["interview_reports"][0].update(notes="see linkedin.com/in/jdoe"), "private data"),
        ("report with a German mobile number", lambda d: d["interview_reports"][0].update(notes="call 0176 12345678"), "private data"),
        ("report asking about @lru_cache passes", lambda d: d["interview_reports"][0].update(question="What does @lru_cache do?"), None),
        ("big number is not a phone", lambda d: d["interview_reports"][0].update(question="How would you process 100 000 000 events?"), None),
        ("report dated 2026-13", lambda d: d["interview_reports"][0].update(date="2026-13"), "needs date"),
        ("non-string report id", lambda d: d["interview_reports"][0].update(id=1), "must look like R1"),
        ("MaterialCheck phases two and three; next …", lambda d: d["hooks"][0].update(cards=["MC"], bullets=["MC4"], concepts=["OWNERSHIP_PM"],
            candidate_angle="Delivered phases two and three in production; next is a new site."), "phases 2–3"),
        ("report with an email", lambda d: d["interview_reports"][0].update(question="Mail hr.person@corp.com your code?"), "private data"),
        ("report with a date is not a phone", lambda d: d["interview_reports"][0].update(notes="asked on 2026-03-14"), None),
        ("process without source", lambda d: d["interview_process"][0].update(src=[]), "has no source"),
        ("old file without interview sections passes", lambda d: d.pop("interview_process") and d.pop("interview_reports"), None),
    ]
    ok = True
    for name, mut, want in cases:
        d = copy.deepcopy(base)
        mut(d)
        errs, _ = check_data(d, T)
        good = (not errs) if want is None else any(want in e for e in errs)
        print(("PASS " if good else "FAIL ") + name + ("" if good else f": {errs}"))
        ok &= good
    sys.exit(0 if ok else 1)


def cmd_check(a):
    if a.selftest:
        selftest()
    if not a.json:
        sys.exit("check needs a json path (or --selftest)")
    try:
        data = json.load(open(a.json, encoding="utf-8"))
    except Exception as e:
        sys.exit(f"ERROR cannot read JSON: {e}")
    today = pdate(a.today) if a.today else dt.date.today()
    errs, warns = check_data(data, today)
    n = {k: len(data.get(k) or []) for k in ["sources"] + SECTIONS}
    print(f"{data.get('company')} ({data.get('jd') or '-'}) · " + " · ".join(f"{k}={v}" for k, v in n.items()))
    for w in warns:
        print("WARN ", w)
    for e in errs:
        print("ERROR", e)
    print("RESULT:", "FAIL" if errs else "OK", f"({len(errs)} errors, {len(warns)} warnings)")
    sys.exit(1 if errs else 0)


# ---------------------------------------------------------------- render
def refs(item):
    ids = item.get("src") or []
    return (" " + " ".join(f"[{x}]" for x in ids)) if ids else ""


def cmd_render(a):
    data = json.load(open(a.json, encoding="utf-8"))
    errs, _ = check_data(data, dt.date.today())
    if errs:
        print(f"WARN rendering a file with {len(errs)} check errors (run check first)", file=sys.stderr)
    con = db()
    cname = {r[0]: r[1] for r in con.execute("select code,name from concepts")}
    o = [f"# Company research: {data['company']}" + (f" ({data['jd']})" if data.get("jd") else ""), "",
         f"Role: {data.get('title') or '-'} · researched {data['researched']} · every fact cites a source [S#] "
         "with URL and access date (see Sources). Hooks map company facts to the candidate's library cards.", ""]
    o += ["## Snapshot", ""]
    order = {f: i for i, f in enumerate(SNAP_FIELDS)}
    for s in sorted(data["snapshot"], key=lambda x: order.get(x.get("field"), 99)):
        o.append(f"- **{s['field']}**: {s['text']}{refs(s)}")
    o += ["", "## Product & team for this role", ""]
    o += [f"- {s['text']}{refs(s)}" for s in data["product_team"]]
    o += ["", "## Tech stack", "", "| Tech | Status | Evidence | Src |", "|---|---|---|---|"]
    for s in sorted(data["tech_stack"], key=lambda x: (TECH_STATUS.index(x["status"]) if x.get("status") in TECH_STATUS else 9, x.get("tech", "").lower())):
        o.append(f"| {s['tech']} | {s['status']} | {s.get('evidence', '').replace('|', '/')} | {' '.join(s.get('src') or [])} |")
    o += ["", "## Recent news (last 12 months)", ""]
    for n in sorted(data["news"], key=lambda x: x.get("date", ""), reverse=True):
        o.append(f"- **{n['date']} · {n['headline']}** — {n.get('text', '')}{refs(n)}")
    o += ["", "## Why-this-company hooks", ""]
    on_cv = cv_bullets(data.get("jd") or "")
    hooks = sorted(data["hooks"], key=lambda h: hook_use(h, on_cv) != "all")
    for i, h in enumerate(hooks, 1):
        o.append(f"### {i}. {h['hook']}" + ("  *(interview only: evidence not on the CV)*" if hook_use(h, on_cv) != "all" else ""))
        o.append(f"*concepts: {', '.join(f'{c} ({cname.get(c, c)})' for c in h.get('concepts') or [])} · "
                 f"cards: {', '.join(h.get('cards') or [])} · evidence: {', '.join(h.get('bullets') or [])}*")
        o += ["", f"- **Company:** {h['company_fact']}{refs(h)}", f"- **Candidate:** {h['candidate_angle']}"]
        if h.get("gap_note"):
            o.append(f"- **Honest gap:** {h['gap_note']}")
        o.append("")
    o += ["## Risks / unknowns", ""]
    for r in data["risks"]:
        extra = f" (searched: {r['searched']})" if r.get("searched") else ""
        o.append(f"- **{r['kind']}**: {r['text']}{refs(r)}{extra}")
    proc, reps = data.get("interview_process") or [], data.get("interview_reports") or []
    if proc or reps:
        o += ["", "## Interview process & reported questions", "",
              "*Reported by candidates on public sites; a signal for practice, not a fact about this interview.*", ""]
        o += [f"- {p['text']}" + (f" ({p['date']})" if p.get("date") else "") + refs(p) for p in proc]
        if proc and reps:
            o.append("")
        srcs = {x["id"]: x for x in data["sources"]}
        for r in sorted(reps, key=lambda r: (STAGES.index(r["stage"]) if r.get("stage") in STAGES else 99, r.get("id", ""))):
            pub = ", ".join(sorted({srcs.get(x, {}).get("publisher", x) for x in r.get("src") or []}))
            o.append(f"- **{r['id']}** [{r.get('stage')} · {r.get('role') or 'role ?'} · {r.get('date')} · {pub}"
                     + (" · snippet only" if r.get("seen") == "snippet" else "") + f"] {r['question']}{refs(r)}")
    o += ["", "## Questions to ask", ""]
    for i, q in enumerate(data["questions"], 1):
        cs = f" · concepts: {', '.join(q['concepts'])}" if q.get("concepts") else ""
        o += [f"{i}. **{q['question']}**{refs(q)}", f"   Listen for: {q['why']}{cs}"]
    o += ["", "## Sources", ""]
    for s in data["sources"]:
        pub = f" · published {s['published']}" if s.get("published") else ""
        o.append(f"- **[{s['id']}]** {s.get('title', '')} · {s.get('publisher', '')} ({s['kind']}){pub} · "
                 f"accessed {s['accessed']} · <{s['url']}>")
    if data.get("notes"):
        o += ["", "## Notes", "", data["notes"]]
    text = "\n".join(o) + "\n"
    path = a.out or os.path.join(os.path.dirname(os.path.abspath(a.json)), OUT_MD)
    open(path, "w", encoding="utf-8").write(text)
    print(f"wrote {path} ({len(text)} chars)")


# ---------------------------------------------------------------- excerpt
def _load(jd):
    try:
        d = app(jd)["dir"]
    except SystemExit:
        return None
    p = os.path.join(d, OUT_JSON)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def reports(data):
    """interview_reports newest first (closest role first among equal dates is left to the reader)."""
    return sorted(data.get("interview_reports") or [], key=lambda r: r.get("date", ""), reverse=True)


def company_text(jd):
    """The ONLY company corpus other skills may use as evidence: snapshot, product_team, news and each hook's
    company_fact. Never candidate_angle, gap_note, interview_reports or any candidate text. '' when no 02_company.json exists."""
    data = _load(jd)
    if not data:
        return ""
    o = [f"{s.get('field')}: {s.get('text', '')}" for s in data.get("snapshot", [])]
    o += [s.get("text", "") for s in data.get("product_team", [])]
    o += [f"{n.get('date')}: {n.get('headline', '')}. {n.get('text', '')}" for n in data.get("news", [])]
    o += [h.get("company_fact", "") for h in data.get("hooks", [])]
    return "\n".join(x for x in o if x.strip())


def excerpt(jd, n=4500):
    """Compact text for other skills' context packs; '' when no 02_company.json exists.
    Each hook line says whether its evidence is on the submitted CV and how it may be used:
    use: all = any skill (outreach, CV, letter) · use: interview-only = interview-prep only (evidence not on the CV)."""
    data = _load(jd)
    if not data:
        return ""
    on_cv = cv_bullets(jd)
    short = lambda x, m=180: x if len(x) <= m else x[: m - 2].rstrip() + " …"
    o = [f"(from {OUT_JSON}, researched {data.get('researched')}; company facts are sourced, cite them as company facts, "
         "not candidate facts; candidate facts come only from the cited bullets)"]
    o += [f"- {s['field']}: {short(s['text'])}" for s in data.get("snapshot", []) if s.get("field") in ("what", "office", "size")]
    o += [f"- process: {short(p['text'])}" for p in data.get("interview_process") or []]
    o += [f"- reported {r['id']} [{r.get('stage')} · {r.get('role') or '?'} · {r.get('date')}"
          + (" · snippet" if r.get("seen") == "snippet" else "") + f"]: {short(r['question'], 200)}"
          for r in reports(data)]
    hooks = sorted(data.get("hooks", []), key=lambda h: hook_use(h, on_cv) != "all")
    for h in hooks:
        bs = h.get("bullets") or []
        cv = "unknown" if not on_cv else ("yes" if all(b in on_cv for b in bs) else
                                          "no (" + ",".join(b for b in bs if b not in on_cv) + " not on CV)")
        line = (f"- hook [use: {hook_use(h, on_cv)} · on CV: {cv} · concepts {','.join(h.get('concepts') or [])} · "
                f"bullets {','.join(bs)}]: {h['hook']} — {short(h['company_fact'])}")
        if h.get("gap_note"):
            line += f" · gap_note: {h['gap_note']}"
        if h.get("notes"):
            line += f" · notes: {h['notes']}"
        o.append(line)
    o += [f"- ask: {q['question']}" for q in data.get("questions", [])]
    o += [f"- news {x['date']}: {x['headline']}" for x in sorted(data.get("news", []), key=lambda x: x.get("date", ""), reverse=True)[:3]]
    t = "\n".join(o)
    return t if len(t) <= n else t[: n - 8] + " …[cut]"


def cmd_excerpt(a):
    t = excerpt(a.jd, a.max)
    print(t or f"(no {OUT_JSON} for {a.jd})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("init"); p.add_argument("--jd"); p.add_argument("--company"); p.add_argument("--jd-file")
    p.add_argument("--out"); p.add_argument("--force", action="store_true"); p.set_defaults(f=cmd_init)
    p = sp.add_parser("check"); p.add_argument("json", nargs="?"); p.add_argument("--today")
    p.add_argument("--selftest", action="store_true"); p.set_defaults(f=cmd_check)
    p = sp.add_parser("render"); p.add_argument("json"); p.add_argument("--out"); p.set_defaults(f=cmd_render)
    p = sp.add_parser("excerpt"); p.add_argument("--jd", required=True); p.add_argument("--max", type=int, default=4500)
    p.set_defaults(f=cmd_excerpt)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
