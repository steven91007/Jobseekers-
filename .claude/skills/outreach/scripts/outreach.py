#!/usr/bin/env python3
"""outreach.py - context pack, check and record for /outreach (drafts only; nothing is ever sent).

  pack   --jd <Co> --kind K [--person .. --role .. --relation ..]   context (cap 8k chars)
  check  <draft.md> --jd <Co> --kind K [--cites VI1,PS1]            length + truth + dangerous-phrase check
  record --jd <Co> --kind K --person .. --channel .. --file <draft> [--via ..]  runs check first (refuses on FAIL), then
         `library_db.py outreach add --status draft` (existing draft of the same jd/kind/person/file is updated)

Kinds: referral-letter | connect-note | follow-up | thank-you.  Global: --db PATH (temp copy for tests).
"""
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "interview-prep", "scripts"))
import truth  # noqa: E402
from truth import read, check_numbers, conf_items, conf_refs, corpus_for, toks, numbers  # noqa: E402

ROOT = truth.ROOT
LIB = os.path.join(ROOT, ".claude", "skills", "tailor-cv", "scripts", "library_db.py")
PROFILE = os.path.join(ROOT, "cv", "profile", "candidate_profile.md")
KINDS = ("referral-letter", "connect-note", "follow-up", "thank-you")
# (unit, hard max, soft min, soft max)
LIMITS = {"referral-letter": ("words", 300, 200, 280), "connect-note": ("chars", 300, None, 300),
          "follow-up": ("words", 120, None, 120), "thank-you": ("words", 150, None, 150)}
DB_ARGS = []


def lib(*args):
    r = subprocess.run([sys.executable, LIB, *DB_ARGS, *args], capture_output=True, text=True, cwd=ROOT)
    return r.returncode, (r.stdout + ("" if r.returncode == 0 else r.stderr)).strip()


def cap(t, n):
    t = t.strip()
    return t if len(t) <= n else t[: n - 12].rstrip() + " …[cut]"


def app(jd):
    row = truth.db().execute("select jd,company,title,dir from applications where jd=? collate nocase", (jd,)).fetchone()
    if not row:
        sys.exit(f"unknown JD '{jd}' (library_db.py status)")
    d = row[3]
    if d and not os.path.isabs(d):
        d = os.path.join(ROOT, d) if os.path.isdir(os.path.join(ROOT, d)) else os.path.join(ROOT, "cv", d)
    return row[0], row[1], row[2], d


def country_of(jd):
    r = truth.db().execute("select country from applications where jd=? collate nocase", (jd,)).fetchone()
    return (r[0] or "") if r else ""


def region_phrase(jd):
    """CONF-am: 'Germany and Asia' for German companies, 'Europe and Asia' otherwise."""
    return "Germany and Asia" if country_of(jd).strip().lower() in ("germany", "deutschland", "de") else "Europe and Asia"


def picks(jd):
    return truth.db().execute("select kind,code,lead from application_picks where jd=? order by position", (jd,)).fetchall()


def profile_part(n):
    prof = re.sub(r"<!--.*?-->", "", read(PROFILE), flags=re.S)
    if not prof.strip():
        return "(MISSING cv/profile/candidate_profile.md)"
    keep = []
    for p in re.split(r"^## ", prof, flags=re.M)[1:]:
        title = p.split("\n", 1)[0]
        if title.startswith(("Identity", "Target countries", "Languages")):
            lines = [l.rstrip() for l in p.splitlines() if l.strip()]
            keep.append("### " + "\n".join(lines[:5] if title.startswith("Identity") else lines))
    return cap("\n".join(keep), n)


def find_prior(jd, kind, d, exclude=None):
    """Latest user-approved wording for this kind: -> (path, text) or (None, '')."""
    cands = []
    if kind == "referral-letter":
        cands.append(os.path.join(d, "09_referral_final.md"))
    import glob
    mine = sorted(glob.glob(os.path.join(d, f"11_outreach_{kind}_*.md")), key=os.path.getmtime, reverse=True)
    cands += [m for m in mine if exclude is None or os.path.abspath(m) != os.path.abspath(exclude)]
    rc, ol = lib("outreach", "list", "--jd", jd)
    if rc == 0:
        for line in reversed(ol.splitlines()):
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 9 and parts[4] == kind and parts[-1].startswith("/"):
                cands.append(parts[-1])
    for c in cands:
        if c and os.path.isfile(c) and (exclude is None or os.path.abspath(c) != os.path.abspath(exclude)):
            return c, read(c)
    return None, ""


def cmd_pack(a):
    jd, company, title, d = app(a.jd)
    con = truth.db()
    S = []
    S.append(("Request", f"kind: {a.kind} · limit: {LIMITS[a.kind][0]} ≤ {LIMITS[a.kind][1]}"
              + (" (aim 200-280 words)" if a.kind == "referral-letter" else "")
              + f"\nstakeholder region wording (CONF-am): \"{region_phrase(jd)}\" (country: {country_of(jd) or 'unknown'})"
              + f"\nperson: {a.person or '(unknown)'} · role: {a.role or '(unknown)'} · relation: {a.relation or '(none given: write without one, or TODO referrer: how you know Chi-yi)'}"))
    ov = read(os.path.join(d, "outreach_overrides.md")).strip()
    if ov:
        S.append(("STANDING USER WORDING for this application (outreach_overrides.md): follow it", cap(ov, 900)))
    pp, prior = find_prior(jd, a.kind, d)
    if prior:
        S.append((f"PRIOR (user-approved wording: keep unless SoT forbids) · {os.path.basename(pp)}", cap(prior, 2900)))
    S.append(("JD brief (01_jd.md)", cap(read(os.path.join(d, "01_jd.md")), 1100)))
    cards, bl = [], []
    for kind, code, lead in picks(jd):
        if kind == "card":
            p = con.execute("select name,metrics from projects where code=?", (code,)).fetchone()
            cards.append(f"- **{code}** {p[0]} | metrics: {p[1]}")
        elif kind == "bullet":
            b = con.execute("select project_code,body,metric,conf from bullets where code=?", (code,)).fetchone()
            if b:
                bl.append(f"- **{code}** (card {b[0]}) lead: {lead} | body: {b[1]} | metric: {b[2] or '-'}")
    rc, delta = lib("render", "--jd", jd, "--part", "delta")
    heads = [l for l in delta.splitlines() if l.strip() and not l.startswith("- ")] if rc == 0 else []
    S.append(("Recorded picks: scope line + card titles/contexts as submitted", cap("\n".join(heads), 900)))
    S.append(("Picked bullets (cite codes with --cites)", cap("\n".join(bl), 2000)))
    S.append(("Picked cards (metrics)", cap("\n".join(cards), 600)))
    S.append(("Candidate profile (identity, relocation, languages)", profile_part(800)))
    rc, ol = lib("outreach", "list", "--jd", jd)
    S.append(("Existing outreach rows for this JD", cap(ol, 500) if rc == 0 and ol else "(none)"))
    if a.kind == "thank-you":
        rc, qa = lib("qa", "list", "--jd", jd, "--source", "asked", "--full")
        S.append(("Asked interview questions (pick 1-2 topics)", cap(qa, 1200) if rc == 0 and qa else "(none recorded: keep the thank-you generic, no invented topics)"))
    out = [f"# Outreach context: {company} · {title} (JD `{jd}`)", f"Application dir: `{d}`", ""]
    for h, b in S:
        out += [f"## {h}", b, ""]
    text = "\n".join(out)
    print(text[:9000] if len(text) > 9000 else text)


# ---------------------------------------------------------------- check
# (regex, label, soft?, ok-if-planned-context?)  soft = a hit also found in the user's prior is a WARN, not an ERROR
DANGER = [
    (r"phases?\s+(?:2|two)\b|phases?\s+(?:2|two)\s*(?:and|&|–|-)\s*(?:3|three)", "phase 2/3 stated as delivered", True, True),
    (r"(?:200\s?\+|more than 200|over 200)", "200+ users (target users only)", False, False),
    (r"\b(?:managed|drove|headed|leading|led)\b(?:\W+\w+){0,6}?\W+(?:project|plan|initiative|rollout)\b", "'led/managed … project/plan/initiative' (CV: stakeholder management)", True, False),
    (r"\b(?:all|three|3)\s+phases\b.{0,40}\b(?:complete\w*|deliver\w*|ship\w*)", "all three phases delivered", False, True),
    (r"\b(?:second|third)\s+phase\b.{0,40}\b(?:deliver\w*|live|ship\w*|production)", "second/third phase delivered", False, True),
    (r"cross-functional", "cross-functional", True, False),
    (r"business stakeholders", "business stakeholders (CONF-e7: NPE and engineers only)", False, False),
    (r"user feedback", "user feedback (CONF-e1)", True, False),
]
MC_ALIASES = r"material\s?check|QA sign-off|document AI"
PROD_WORDS = r"in production|production-ready|deployed|live|in use|rolled out"


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def body_of(text):
    pat = r"^\s*(dear\b|hi\b|hello\b|best regards|kind regards|regards|sincerely|\[referrer|\[your name|subject:)"
    lines = [l for l in text.splitlines() if not (re.match(pat, l, re.I) and len(l) < 45)]
    return "\n".join(lines).strip()


def mc_production_hits(sent):
    """MaterialCheck-ish name within 6 words of production wording ('live extraction service' is another service)."""
    sent = re.sub(r"\blive(?=\s+(?:extraction\s+)?services?\b)", "", sent, flags=re.I)
    words = re.findall(r"[\w'-]+", sent)
    low = [w.lower() for w in words]
    hits = []
    for m in re.finditer(MC_ALIASES, sent, re.I):
        pre = len(re.findall(r"[\w'-]+", sent[: m.start()]))
        n = len(re.findall(r"[\w'-]+", m.group(0)))
        lo, hi = max(0, pre - 6), min(len(words), pre + n + 6)
        win = " ".join(low[lo:hi])
        if re.search(r"\b(?:" + PROD_WORDS + r")\b", win):
            hits.append(m.group(0))
    return hits


def diff_prior(prior, draft):
    ps, ds = sentences(prior), sentences(draft)
    import difflib
    dset = set(ds)
    info = []
    for p in ps:
        if p in dset or re.match(r"^(dear|best regards|\[)", p, re.I):
            continue
        best = max(ds, key=lambda x: difflib.SequenceMatcher(None, p, x).ratio(), default="")
        r = difflib.SequenceMatcher(None, p, best).ratio()
        if r >= 0.6:
            info.append(f"CHANGED ({r:.2f}): \"{p[:90]}\" -> \"{best[:90]}\"")
        else:
            info.append(f"DROPPED: \"{p[:110]}\"")
    return info


def card_corpora(jd, cv):
    con = truth.db()
    out, names = {}, {}
    for kind, code, _ in picks(jd):
        if kind == "bullet":
            b = con.execute("select project_code from bullets where code=?", (code,)).fetchone()
            if b:
                out.setdefault(b[0], "")
                out[b[0]] += "\n" + corpus_for(con, code, cv)
    for card in list(out):
        p = con.execute("select name from projects where code=?", (card,)).fetchone()
        names[card] = p[0] if p else card
    return out, names


def check_text(draft, jd, kind, cites=None, notes="", prior=""):
    """-> (n, unit, cites, errs, warns, info)"""
    _, company, title, d = app(jd)
    errs, warns = [], []
    body = body_of(draft)
    stripped = draft.strip()
    unit, hard, lo, hi = LIMITS[kind]
    n = len(stripped) if unit == "chars" else len(body.split())
    if n > hard:
        errs.append(f"{kind}: {n} {unit} (max {hard})")
    elif lo and n < lo - 40:
        errs.append(f"{kind}: {n} {unit} (min about {lo})")
    elif (lo and n < lo) or n > hi:
        warns.append(f"{kind}: {n} {unit} (target {lo or 0}-{hi})")
    cv = read(os.path.join(d, "03_cv.md"))
    cites = cites or [c for k, c, _ in picks(jd) if k == "bullet"]
    con = truth.db()
    hard_corpus = "\n".join(corpus_for(con, c, cv) for c in cites) + "\n" + cv + "\n" + profile_part(4000)
    conf, skipped, _ = conf_items()
    e2, w2 = check_numbers("draft", body, hard_corpus, notes, conf, skipped, ",".join(cites))
    errs += e2
    warns += w2
    low, plow = hard_corpus.lower(), prior.lower()
    conf_text = ""
    for ref in conf_refs(notes):
        if ref not in skipped:
            conf_text += " ".join(conf.get(ref) or []) .lower() + " "
    for sent in sentences(body):
        for pat, label, soft, planned_ok in DANGER:
            m = re.search(pat, sent, re.I)
            if not m:
                continue
            ph = m.group(0).lower()
            if ph in low or ph in conf_text or (label.startswith("user feedback") and "user feedback" in conf_text):
                continue
            if planned_ok and re.search(r"\b(planned|are the plan|is the plan|next|not yet|later|remain)", sent, re.I):
                continue
            if soft and prior and ph in plow:
                warns.append(f"user wrote this, confirm with candidate: {label}: \"{sent[:100]}\"")
            else:
                errs.append(f"dangerous upgrade: {label}: \"{sent[:100]}\"")
        if mc_production_hits(sent):
            errs.append(f"MaterialCheck is a POC, not production: \"{sent[:110]}\"")
        if re.search(r"stakeholder", sent, re.I) and re.search(r"Germany and Asia", sent) and region_phrase(jd) != "Germany and Asia":
            msg = f"stakeholder region 'Germany and Asia' is only for German companies (CONF-am); this application is {country_of(jd) or 'unknown'}: write 'Europe and Asia'"
            (warns if "germany and asia" in plow else errs).append(f"{msg}: \"{sent[:90]}\"")
    # cross-card attribution of unitless numbers
    cc, names = card_corpora(jd, cv)
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    for para in paras:
        card = next((c for c, nm in names.items() if re.match(r"^\W*" + re.escape(nm), para.strip())), None)
        if not card:
            continue
        own = numbers(cc[card])
        for tok in toks(para):
            if tok[1] == "" and tok not in own:
                others = [names[c] for c in cc if c != card and tok in numbers(cc[c])]
                if others:
                    warns.append(f"number {tok[0]} in the '{names[card]}' paragraph is a bullet fact of '{others[0]}': check misattribution")
    if kind == "referral-letter":
        if re.search(r"\bI (built|developed|led|owned|designed|deployed|implemented)\b", body):
            warns.append("referral letter should speak about the candidate in third person (referrer voice)")
        if not re.search(r"(?m)^\W*\*{0,2}[A-Z][^\n]{3,80}\*{0,2}[:—-]", body):
            warns.append("lead each project with its card title (e.g. 'Industrial Vision Inspection & ML Tooling: …')")
    elif re.search(r"\b(Chi-?yi|he|his)\b", body):
        warns.append(f"{kind} should be first person from the candidate")
    info = diff_prior(prior, draft) if prior else []
    return n, unit, cites, errs, warns, info


def selftest():
    base = "Hello, I applied for the role."
    cases = [
        ("managed the rollout", "Chi-yi managed the rollout of the project. " + base, "", "ERROR", "led/managed"),
        ("all three phases delivered", "All three phases were delivered. " + base, "", "ERROR", "all three phases"),
        ("third phase live", "The third phase is live in production. " + base, "", "ERROR", "second/third"),
        ("alias QA sign-off in production", "The QA sign-off document AI is deployed in production. " + base, "", "ERROR", "POC"),
        ("separate live service is fine", "MaterialCheck is a QA sign-off tool, and a separate live extraction service handles other documents reliably today. " + base, "", None, "POC"),
        ("user feedback ok with CONF-e1", "He refines the tools from user feedback. " + base, "CONF-e1", None, "user feedback"),
        ("business stakeholders stays error", "He works with business stakeholders. " + base, "CONF-e1", "ERROR", "business stakeholders"),
        ("cross-card number warns", "Industrial Vision Inspection & ML Tooling: the service handles about 100 documents per month.", "", "WARN", "misattribution"),
    ]
    ok = True
    for name, text, notes, want, key in cases:
        _, _, _, errs, warns, _ = check_text(text, "TOMRA", "follow-up", notes=notes)
        pool = errs if want == "ERROR" else warns if want == "WARN" else errs
        hit = [x for x in pool if key.lower() in x.lower()]
        good = bool(hit) if want else not hit
        print(("PASS " if good else "FAIL ") + name + (f": {hit[0][:90]}" if hit else ""))
        ok &= good
    sys.exit(0 if ok else 1)


def cmd_check(a):
    if a.selftest:
        selftest()
    jd, company, title, d = app(a.jd)
    draft = read(a.draft)
    if not draft.strip():
        sys.exit(f"ERROR empty or missing draft {a.draft}")
    prior = read(a.prior) if a.prior else ""
    cites = [c.strip() for c in (a.cites or "").split(",") if c.strip()]
    n, unit, cites, errs, warns, info = check_text(draft, jd, a.kind, cites, a.notes or "", prior)
    print(f"kind: {a.kind} · {n} {unit} · cites: {','.join(cites)}" + (f" · prior: {os.path.basename(a.prior)}" if a.prior else ""))
    for i in info:
        print("PRIOR", i)
    for w in warns:
        print("WARN ", w)
    for e in errs:
        print("ERROR", e)
    print("RESULT:", "FAIL" if errs else "OK", f"({len(errs)} errors, {len(warns)} warnings)")
    sys.exit(1 if errs else 0)


def cmd_record(a):
    f = os.path.abspath(a.file)
    if not os.path.isfile(f):
        sys.exit(f"no such draft {f}")
    jd, company, title, d = app(a.jd)
    prior = read(a.prior) if a.prior else ""
    cites = [c.strip() for c in (a.cites or "").split(",") if c.strip()]
    n, unit, cites, errs, warns, _ = check_text(read(f), jd, a.kind, cites, a.notes or "", prior)
    if errs:
        print("record refused: check FAILED")
        for e in errs:
            print("ERROR", e)
        sys.exit(1)
    notes = " · ".join(x for x in ((f"via: {a.via}" if a.via else ""), a.notes or "") if x)
    rows = truth.db().execute("select id,status,notes from outreach where jd=? collate nocase and kind=? and person=? and file=?",
                              (jd, a.kind, a.person, f)).fetchall() if _has_outreach() else []
    drafts = [r for r in rows if r[1] == "draft"]
    if drafts:
        rc, out = lib("outreach", "set", str(drafts[-1][0]), "--notes", notes or (drafts[-1][2] or "re-checked"))
        print(out + " (existing draft updated, no duplicate)")
        sys.exit(rc)
    rc, out = lib("outreach", "add", "--jd", a.jd, "--person", a.person, "--channel", a.channel, "--kind", a.kind,
                  "--status", "draft", "--file", f, *(["--role", a.role] if a.role else []), *(["--notes", notes] if notes else []))
    print(out)
    sys.exit(rc)


def _has_outreach():
    return bool(truth.db().execute("select 1 from sqlite_master where name='outreach'").fetchone())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("pack"); p.add_argument("--jd", required=True); p.add_argument("--kind", required=True, choices=KINDS)
    for x in ("person", "role", "relation"):
        p.add_argument("--" + x)
    p.set_defaults(f=cmd_pack)
    p = sp.add_parser("check"); p.add_argument("draft", nargs="?"); p.add_argument("--jd", default="TOMRA")
    p.add_argument("--kind", default="follow-up", choices=KINDS); p.add_argument("--cites"); p.add_argument("--notes", help="CONF ids the draft relies on, e.g. 'CONF-o2 CONF-o5'")
    p.add_argument("--prior", help="user-approved earlier wording: lists dropped/changed sentences, softens user-written hits to warnings")
    p.add_argument("--selftest", action="store_true")
    p.set_defaults(f=cmd_check)
    p = sp.add_parser("record"); p.add_argument("--jd", required=True); p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--person", required=True); p.add_argument("--channel", required=True, choices=("email", "linkedin", "referral", "other"))
    p.add_argument("--file", required=True); p.add_argument("--role"); p.add_argument("--notes"); p.add_argument("--via", help="referrer name, stored in notes")
    p.add_argument("--cites"); p.add_argument("--prior"); p.set_defaults(f=cmd_record)
    a = ap.parse_args()
    if a.db:
        os.environ["EXPERIENCE_DB"] = os.path.abspath(a.db)
        truth.DB_PATH = os.path.abspath(a.db)
        DB_ARGS[:] = ["--db", os.path.abspath(a.db)]
    a.f(a)


if __name__ == "__main__":
    main()
