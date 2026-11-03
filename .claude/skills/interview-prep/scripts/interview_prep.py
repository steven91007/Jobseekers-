#!/usr/bin/env python3
"""interview_prep.py - context pack, validation and rendering for /interview-prep.

  pack   --jd <Co>                 markdown context pack (stdlib; reads the experience DB read-only)
  check  <json>                    validate Q&A file (schema, mix, codes, length, number truth check)
  render <json> [--out path]       readable markdown grouped by kind

Q&A JSON = list of objects: jd, company, question, kind, concepts, bullets, answer, source,
outcome, notes.  kind "ask" = a question the candidate asks THEM (answer = what to listen for);
kind "gap" = a JD gap answered honestly.  Old files: notes starting "ask-them"/"gap" still read as such.
Notes may cite CONF items ("CONF-o2"); their text then counts as evidence for the number check.
Optional field `reported` = ids of interview_reports in 02_company.json ("R1,R3") the item practises; every
report should be covered by one item (warning), unknown ids are errors. Reports are never evidence.
"""
import argparse, json, os, re, sqlite3, subprocess, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truth  # noqa: E402
from truth import (toks, word_toks, numbers, fmt, conf_items, conf_refs, corpus_for, check_numbers, SKIP_MARK)  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "company-research", "scripts"))
try:  # optional: /company-research output (02_company.json) for the pack; '' when missing
    from company_research import excerpt as company_excerpt, company_text, _load as company_json  # noqa: E402
except Exception:  # pragma: no cover - keep interview prep usable without the company skill
    company_excerpt = lambda jd, n=1800: ""  # noqa: E731
    company_text = lambda jd: ""  # noqa: E731
    company_json = lambda jd: None  # noqa: E731

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 4))
DB_PATH = os.path.join(ROOT, "cv", "profile", "experience.sqlite")
LIB = os.path.join(ROOT, ".claude", "skills", "tailor-cv", "scripts", "library_db.py")
PROFILE = os.path.join(ROOT, "cv", "profile", "candidate_profile.md")
KINDS = {"intro", "behavioural", "technical", "system-design", "project", "motivation", "gap", "ask", "other"}
SOURCES = {"generated", "asked", "self"}
FIELDS = ["jd", "company", "question", "kind", "concepts", "bullets", "answer", "source", "outcome", "notes"]
# target mix: (label, predicate, count)
MIX = [("intro", 2, 2), ("behavioural", 3, 5), ("project", 3, 5), ("technical", 2, 4), ("system-design", 1, 3),
       ("gap", 2, 5), ("motivation", 2, 4), ("ask", 3, 3)]  # (label, min, max)
CONF_FILE = os.path.join(ROOT, "cv", "profile", "candidate_confirmations.md")
ANS_MIN, ANS_MAX = 60, 150


def db():
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def lib(*args):
    r = subprocess.run([sys.executable, LIB, *args], capture_output=True, text=True, cwd=ROOT)
    return r.returncode, (r.stdout + ("" if r.returncode == 0 else r.stderr)).strip()


def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return ""


def app(jd):
    row = db().execute("select jd,company,title,dir from applications where jd=? collate nocase", (jd,)).fetchone()
    if not row:
        sys.exit(f"unknown JD '{jd}' (library_db.py status)")
    d = row[3]
    if d and not os.path.isabs(d):
        d = os.path.join(ROOT, d) if os.path.isdir(os.path.join(ROOT, d)) else os.path.join(ROOT, "cv", d)
    return (row[0], row[1], row[2], d)


def csv(s):
    if isinstance(s, (list, tuple)):
        return [str(x).strip() for x in s if str(x).strip()]
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def picks(jd):
    return db().execute("select kind,code,lead from application_picks where jd=? order by position", (jd,)).fetchall()


# ---------------------------------------------------------------- pack
HARD_CAP = 12500
PROFILE_HEADS = ("Identity and contact", "Target countries", "Languages", "Compensation", "Rules every agent")


def cap(t, n):
    t = t.strip()
    return t if len(t) <= n else t[: n - 12].rstrip() + " …[cut]"


def profile_excerpt(n=2000):
    prof = re.sub(r"<!--.*?-->", "", read(PROFILE), flags=re.S)
    if not prof.strip():
        return "(MISSING cv/profile/candidate_profile.md: motivation / logistics answers must stay TODO)"
    parts = re.split(r"^## ", prof, flags=re.M)[1:]
    keep = []
    for p in parts:
        title = p.split("\n", 1)[0]
        if title.startswith(PROFILE_HEADS):
            lines = [l.rstrip() for l in p.splitlines() if l.strip()]
            if title.startswith("Identity"):
                lines = lines[:2]
            keep.append("### " + "\n".join(lines))
    return cap("\n".join(keep), n)


def gap_sections(text):
    """-> list of (code, name, [terms]) from the gap / do-not-claim / covered-but-term-not-claimable sections."""
    res, sec = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            sec = ("gap" if line.startswith("## gap") else "dnc" if line.startswith("## do not claim")
                   else "tnc" if line.startswith("## covered concept, term not claimable") else None)
            continue
        m = re.match(r"^- ([A-Z][A-Z_0-9]+)\b(?: \(w\d+\))?\s*(.*)$", line)
        if not sec or not m:
            continue
        code, rest = m.group(1), m.group(2)
        pieces = [p.strip() for p in rest.split(" · ")]
        name = re.sub(r"^JD:\s*", "", pieces[0]) if pieces else code
        terms = []
        for p in pieces[1:]:
            tm = re.match(r"^(?:JD:\s*)?(.+?)\s*\[([^\]]+)\]$", p)
            if tm and (sec != "tnc" or tm.group(2) in ("gap", "do not claim")):
                terms.append(tm.group(1).strip())
        res.append((code, name, terms, sec))
    return res


def cmd_pack(a):
    jd, company, title, d = app(a.jd)
    con = db()
    S = []  # (heading, body)
    S.append(("JD brief (01_jd.md)", cap(read(os.path.join(d, "01_jd.md")), 1500)))
    m = re.search(r"### Company Description\s+(.+?)(?=\n###|\Z)", read(os.path.join(d, "jd.md")), re.S)
    if m:
        S.append(("Company description (jd.md)", cap(m.group(1), 300)))
    cr = company_excerpt(jd, 2900)
    if cr:
        S.append(("Company research (02_company.json): hooks, interview process, reported questions (R#), asks", cr))
    rc, delta = lib("render", "--jd", jd, "--part", "delta")
    heads = [l for l in delta.splitlines() if l.strip() and not l.startswith("- ")]
    S.append(("Scope line + card contexts as submitted", cap("\n".join(heads), 900) if rc == 0 else "(render failed)"))
    bl = []
    for kind, code, lead in picks(jd):
        if kind == "bullet":
            b = con.execute("select project_code,body,metric,conf from bullets where code=?", (code,)).fetchone()
            if b:
                bl.append(f"- **{code}** (card {b[0]}) lead: {lead} | body: {b[1]} | metric: {b[2] or '-'} | CONF: {b[3] or '-'}")
    S.append(("Picked bullets (cite these codes)", cap("\n".join(bl), 2600)))
    cl = []
    cvt = read(os.path.join(d, "03_cv.md"))
    oncv = cvt + "\n" + "\n".join(corpus_for(con, c, cvt) for k, c, _ in picks(jd) if k == "bullet")
    for kind, code, _ in picks(jd):
        if kind == "card":
            p = con.execute("select name,metrics from projects where code=?", (code,)).fetchone()
            cl.append(truth.card_metrics_line(code, p[0], p[1], oncv))
    S.append(("Picked cards (metrics supported by this CV)", cap("\n".join(cl), 800)))
    rc, match = lib("match", os.path.join(d, "07_job_keywords.json"))
    if rc == 0:
        ls = match.splitlines()
        req = [l for l in ls if l.startswith("[req]")]
        pref = [l for l in ls if l.startswith("[pref]")]
        S.append(("JD concepts with library evidence (match)", cap("\n".join(req + ["preferred:"] + pref[:12]), 2100)))
    rc, gaps = lib("gaps", "--jd", jd)
    if rc == 0:
        gl = ["Never claim these. A `gap` question must name one of the terms:"]
        for code, name, terms, _sec in gap_sections(gaps):
            gl.append(f"- {code} {name}: {', '.join(terms) if terms else '(no term)'}")
        S.append(("Gaps (gap / do-not-claim / term not claimable)", cap("\n".join(gl), 1100)))
    rc, qa = lib("qa", "list", "--jd", jd)
    S.append(("Existing Q&A rows for this JD", cap(qa, 700) if rc == 0 and qa else "(none / qa table not available)"))
    S.append(("Candidate profile (selected sections)", profile_excerpt(1900)))
    out = [f"# Interview-prep context pack: {company} · {title}  (JD code `{jd}`)", f"Application dir: `{d}`", ""]
    for h, b in S:
        out += [f"## {h}", b, ""]
    text = "\n".join(out)
    if len(text) > HARD_CAP:
        text = text[:HARD_CAP - 20] + "\n…[pack cut]"
    print(text)


# ---------------------------------------------------------------- check
def classify(q):
    k = q.get("kind")
    if k in ("gap", "ask"):
        return k
    n = (q.get("notes") or "").lower()
    if n.startswith("ask-them"):
        return "ask"
    if n.startswith("gap"):
        return "gap"
    return k


def check_data(data):
    errs, warns = [], []
    con = db()
    concepts = {r[0] for r in con.execute("select code from concepts")}
    bullets = {r[0] for r in con.execute("select code from bullets")}
    jd0 = data[0].get("jd", "") if isinstance(data[0], dict) else ""
    cv_text = jd_text = ctext = ""
    cnames = []
    gaps = []
    try:
        _, _, _, d = app(jd0)
        cv_text = read(os.path.join(d, "03_cv.md"))
        jd_text = read(os.path.join(d, "jd.md")) + read(os.path.join(d, "01_jd.md"))
        # company figures (company_text(), never 02_company.md) are checked per sentence by truth.company_number_ok
        # for motivation/ask answers only; they are NOT added to any corpus
        ctext = company_text(jd0)
        cnames = truth.company_names((app(jd0)[1] or ""), ctext + "\n" + jd_text, (company_json(jd0) or {}).get("names") or ())
        gaps = gap_sections(lib("gaps", "--jd", jd0)[1])
    except SystemExit:
        errs.append(f"jd '{jd0}' not in applications")
    prof = re.sub(r"<!--.*?-->", "", read(PROFILE), flags=re.S)
    conf, conf_skipped, conf_dup = conf_items()
    if conf_dup:
        warns.append(f"candidate_confirmations.md repeats the CONF letter(s) {conf_dup}; items are merged under one key, cite with care")
    cj = (company_json(jd0) or {}) if jd0 else {}
    rep_ids = {r.get("id"): r for r in cj.get("interview_reports") or []}
    rep_used = set()
    gap_codes = {g[0] for g in gaps}
    gap_terms = {t.lower() for g in gaps for t in g[2]}
    gap_terms |= {x.lower() for g in gaps if g[3] != "tnc" for x in (g[1], g[0].replace("_", " "))}
    seen = set()
    for i, q in enumerate(data, 1):
        t = f"#{i}"
        if not isinstance(q, dict):
            errs.append(f"{t} not an object"); continue
        miss = [f for f in FIELDS if f not in q]
        if miss:
            errs.append(f"{t} missing fields {miss}"); continue
        if q["kind"] not in KINDS:
            errs.append(f"{t} bad kind {q['kind']!r}")
        if q["source"] not in SOURCES:
            errs.append(f"{t} bad source {q['source']!r}")
        if q["jd"] != jd0:
            errs.append(f"{t} jd differs from first row")
        if (q["jd"], q["question"]) in seen:
            errs.append(f"{t} duplicate question")
        seen.add((q["jd"], q["question"]))
        if not q["question"].strip() or not q["answer"].strip():
            errs.append(f"{t} empty question/answer"); continue
        cls = classify(q)
        codes_c, codes_b = csv(q["concepts"]), csv(q["bullets"])
        for c in codes_c:
            if c not in concepts:
                errs.append(f"{t} unknown concept {c}")
        for b in codes_b:
            if b not in bullets:
                errs.append(f"{t} unknown bullet {b}")
        for r in csv(q.get("reported")):
            if r not in rep_ids:
                errs.append(f"{t} reported {r} is not an interview_reports id in 02_company.json")
            elif cls == "ask":
                warns.append(f"{t} an 'ask' item cites reported {r}: reports are questions asked of the candidate")
            rep_used.add(r)
        words = len(q["answer"].split())
        if cls != "ask" and not (ANS_MIN <= words <= ANS_MAX):
            errs.append(f"{t} answer {words} words (need {ANS_MIN}-{ANS_MAX})")
        if cls in ("intro", "behavioural", "project") and not codes_b:
            errs.append(f"{t} {cls} question cites no bullet")
        if cls == "gap":
            blob = " ".join([q["question"], q["answer"], q.get("notes") or ""]).lower()
            if gap_terms and not any(re.search(r"(?<![a-z])" + re.escape(x) + r"(?![a-z])", blob) for x in gap_terms if x):
                errs.append(f"{t} gap answer names none of the gap terms")
            if gap_codes and not set(codes_c) & gap_codes:
                warns.append(f"{t} gap question targets {codes_c}, none in {sorted(gap_codes)}")
            if not re.search(r"\b(haven't|have not|not yet|no production|never|did not|have no|don't have|not something)\b", q["answer"], re.I):
                warns.append(f"{t} gap answer does not say plainly that the experience is missing")
        hard = "\n".join(corpus_for(con, b, cv_text) for b in codes_b)
        if cls in ("motivation", "ask"):
            hard += "\n" + prof
        if cls == "intro":   # a self-introduction summarises the submitted CV (profile, roles, years)
            hard += "\n" + cv_text + "\n" + prof
        if not csv(q.get("reported")):   # a reported question's wording comes from a public review: never evidence
            hard += "\n" + q["question"]
        e2, w2 = check_numbers(t, q["answer"], hard, q.get("notes"), conf, conf_skipped, ",".join(codes_b))
        if cls in ("motivation", "ask") and (ctext or jd_text):
            # JD and company figures pass only in a sentence about the company, never about the candidate
            e2 = truth.filter_company_numbers(t, q["answer"], e2, (ctext + "\n" + jd_text).strip(), cnames, "first")
        if (any(b.startswith("MC") for b in codes_b) or re.search(r"material\s*check", q["answer"], re.I)) \
                and truth.mc_phase_claims(q["answer"]):
            errs.append(f"{t} MaterialCheck: only phase 1 is in production (CONF-an); phases 2–3 are the plan")
        errs += e2
        warns += w2
    for r, rr in rep_ids.items():
        if r not in rep_used:
            warns.append(f"reported question {r} ({rr.get('stage')}) is not practised by any item: {rr.get('question', '')[:70]}")
    cnt = Counter(classify(q) for q in data if isinstance(q, dict))
    for label, lo, hi in MIX:
        got = cnt.get(label, 0)
        if not lo <= got <= hi:
            warns.append(f"mix: {label} {got} (target {lo}-{hi})")
    total = len(data)
    if not 15 <= total <= 27:
        warns.append(f"total {total} (target 15-22 plus 3 asks)")
    return cnt, warns, errs


def selftest():
    """Both repro cases from the round-2 review must be caught."""
    pad = " The detector work is part of the quality inspection platform and it was built over time with the team and reviewers on site."
    base = dict(jd="TOMRA", company="TOMRA Recycling", kind="behavioural", concepts="CV", bullets="PS4", source="generated", outcome="")
    cases = [
        ("retracted '200+ engineers' via CONF-e6", dict(base, question="t1", answer="Over 200+ engineers use the tool every day." + pad, notes="CONF-e6"), "ERROR"),
        ("lab figure '55% to 85%' via CONF-b5", dict(base, question="t2", answer="The comparison went from 55% to 85% on the regions." + pad, notes="CONF-b5"), "WARN"),
        ("range ref CONF-o1–o5 expands", dict(base, question="t3", answer="Plan phases 1 to 3 over a quarter." + pad, notes="CONF-o1–o5"), None),
    ]
    ok = True
    for name, row, want in cases:
        _, warns, errs = check_data([row])
        hits = [e for e in errs if "number" in e] if want == "ERROR" else [w for w in warns if "only by" in w]
        good = bool(hits) if want else not [e for e in errs if "number" in e and "300" in e]
        print(("PASS " if good else "FAIL ") + name + (f": {hits[0]}" if hits else ""))
        ok &= good
    ct = company_text("TOMRA")
    if ct:   # company-number rule shared with /outreach (truth.company_number_ok)
        for name, good, msg in truth.company_selftest(ct, "TOMRA Recycling", (company_json("TOMRA") or {}).get("names") or ()):
            print(("PASS " if good else "FAIL ") + "company: " + name + f": {msg[:90]}")
            ok &= good
        mot = dict(base, kind="motivation", bullets="", notes="", question="tm")
        for name, ans, want in [
                ("motivation: company 33x passes", "TOMRA reports up to 33× throughput for GAINnext UBC sorting.", False),
                ("motivation: 33 times laundered", "Like TOMRA, I reached 33 times the manual throughput on my own tools.", True)]:
            _, _, errs = check_data([dict(mot, question="Why this company? " + ("A" if "passes" in name else "B"), answer=ans + pad)])
            hits = [e for e in errs if "number" in e]
            good = bool(hits) == want
            print(("PASS " if good else "FAIL ") + name + (f": {hits[0][:90]}" if hits else ": accepted"))
            ok &= good
    sys.exit(0 if ok else 1)


def cmd_check(a):
    if a.selftest:
        selftest()
    try:
        data = json.load(open(a.json, encoding="utf-8"))
    except Exception as e:
        sys.exit(f"ERROR cannot read JSON: {e}")
    if not isinstance(data, list) or not data:
        sys.exit("ERROR top level must be a non-empty list")
    cnt, warns, errs = check_data(data)
    print(f"questions: {len(data)} · mix: " + ", ".join(f"{k}={v}" for k, v in sorted(cnt.items(), key=lambda x: str(x[0]))))
    for w in warns:
        print("WARN ", w)
    for e in errs:
        print("ERROR", e)
    print("RESULT:", "FAIL" if errs else "OK", f"({len(errs)} errors, {len(warns)} warnings)")
    sys.exit(1 if errs else 0)


# ---------------------------------------------------------------- render
def cmd_render(a):
    data = json.load(open(a.json, encoding="utf-8"))
    jd0, co = data[0]["jd"], data[0].get("company", "")
    cj = company_json(jd0) or {}
    reps = {r.get("id"): r for r in cj.get("interview_reports") or []}
    groups = {}
    for q in data:
        groups.setdefault(classify(q) or "other", []).append(q)
    titles = {"intro": "Opening: introduce yourself", "behavioural": "Behavioural (STAR)", "project": "Project deep-dives", "technical": "Technical",
              "system-design": "System design", "gap": "Gaps: be honest", "motivation": "Motivation, company, logistics",
              "ask": "Questions to ask them", "other": "Other"}
    out = [f"# Interview prep: {co} ({jd0})", "",
           f"{len(data)} items. Answers use only library facts; bullet codes show the evidence. Gap answers state the gap plainly. "
           "`TODO candidate:` marks a fact the library does not hold.", ""]
    n = 0
    for g, title in titles.items():
        qs = groups.get(g) or []
        if not qs:
            continue
        out += [f"## {title}", ""]
        for q in qs:
            n += 1
            out.append(f"### {n}. {q['question']}")
            meta = []
            if csv(q["bullets"]):
                meta.append("evidence: " + ", ".join(csv(q["bullets"])))
            if csv(q["concepts"]):
                meta.append("concepts: " + ", ".join(csv(q["concepts"])))
            for r in csv(q.get("reported")):
                rr = reps.get(r) or {}
                meta.append(f"reported {r} ({rr.get('stage', '?')}, {rr.get('date', '?')})")
            if meta:
                out.append("*" + " · ".join(meta) + "*")
            out.append("")
            out.append(("Why ask / what to listen for: " if g == "ask" else "") + q["answer"])
            note = re.sub(r"^(ask-them|gap)\s*[:\-]?\s*", "", q.get("notes") or "", flags=re.I)
            if note:
                out += ["", f"> {note}"]
            out.append("")
    text = "\n".join(out)
    path = a.out or os.path.join(os.path.dirname(os.path.abspath(a.json)), "10_interview.md")
    open(path, "w", encoding="utf-8").write(text)
    print(f"wrote {path} ({len(text)} chars)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("pack"); p.add_argument("--jd", required=True); p.set_defaults(f=cmd_pack)
    p = sp.add_parser("check"); p.add_argument("json", nargs="?"); p.add_argument("--selftest", action="store_true"); p.set_defaults(f=cmd_check)
    p = sp.add_parser("render"); p.add_argument("json"); p.add_argument("--out"); p.set_defaults(f=cmd_render)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
