"""Letter plan: which JD concepts the cover letter should name so it complements the CV's keyword
coverage instead of repeating it. Stdlib only; run with WSL `python3`. Read-only on the CV, the
application dir and the experience DB.

    python3 letter_plan.py --jd Scout24 [--dir <app dir>] [--keywords <07_job_keywords.json>] \
                           [--out <letter dir>/letter_plan.json]

Inputs : <app dir>/07_job_keywords.json (or --keywords), <app dir>/03_cv.md, <app dir>/01_jd.md + jd.md,
         cv/profile/experience.sqlite (concept layer, tailor-cv library_db.py — imported, never written)
Output : plan JSON (stdout or --out) + a short text summary on stderr.

Matching rule = the external ATS check (tailor-cv ats_check.py): whole word, case-insensitive, no
synonyms, over the CV's Summary, experience lines, education and Skills *items* (not the headline,
not the row labels, not LANGUAGES).

"letter should name" (≤ 4), concepts the JD weights `required` whose claim is covered or SoT-supported
(a term the keyword map marks gap / do not claim never counts), in three tiers, proven-on-CV evidence first:
  1. none of the concept's required JD terms is literally on the CV;
  2. align's `summary` rows (recorded picks prove the concept only via a tool name);
  3. the concept is on the CV, but one of its required JD spellings is not.
Each entry carries the exact JD spelling to write (`write`) and the other missing spellings (`also`).
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
TAILOR = ROOT / ".claude" / "skills" / "tailor-cv" / "scripts"
sys.path.insert(0, str(TAILOR))
sys.path.insert(0, str(TAILOR / "ext" / "resume_matcher"))

import library_db as L  # noqa: E402  (read-only use: connect / resolve / concept_index / align)

try:  # same rule the ATS check uses (vendored Resume-Matcher, pure stdlib)
    from app.services.text_match import keyword_in_text  # noqa: E402
except Exception:  # pragma: no cover - port of the same rule
    def keyword_in_text(keyword: str, text: str) -> bool:
        k = keyword.strip().lower()
        return bool(k) and re.search(rf"(?<!\w){re.escape(k)}(?!\w)", text.lower()) is not None

TYPES = (("required_skills", "required", 3), ("preferred_skills", "preferred", 2), ("keywords", "keyword", 1))
BOLD = re.compile(r"\*\*(.+?)\*\*")
NO_CLAIM = ("gap", "do not claim")
CAP = 4
SKIP = {"LANG_LOC"}   # language / location terms: the template's own lines carry them


def claimable(claim: str | None) -> bool:
    """covered or SoT-supported (the DB writes "SoT-supported" and "SoT-supported wording")."""
    c = (claim or "").casefold()
    return c == "covered" or c.startswith("sot")


# ---------------------------------------------------------------- CV text (port of ats_check.py)
def strip_md(s: str) -> str:
    return BOLD.sub(r"\1", s).strip()


def cv_scan_text(md: Path) -> str:
    """The text the ATS keyword match scans: Summary, experience (titles, companies, lines),
    education, Skills items. Port of ats_check.resume_from_md + _extract_all_text."""
    lines = [l.rstrip("\r") for l in md.read_text(encoding="utf-8-sig").splitlines()]
    parts = [strip_md(lines[4])] if len(lines) > 4 else []
    i = next((k for k, l in enumerate(lines) if l.startswith("**PROFESSIONAL EXPERIENCE**")), len(lines)) + 1
    while i < len(lines) and lines[i]:
        l = lines[i]
        if "\t" in l and l.startswith("**"):
            head = l.split("\t")[0]
            parts += [strip_md(x) for x in head.split("  |  ")]
        else:
            parts.append(strip_md(l[2:] if l.startswith("- ") else l))
        i += 1
    s0 = next((k for k, l in enumerate(lines) if l.startswith("**TECHNICAL SKILLS**")), -1)
    s1 = next((k for k, l in enumerate(lines) if l.startswith("**PROFESSIONAL EXPERIENCE**")), len(lines))
    skills = []
    for l in lines[s0 + 1:s1] if s0 >= 0 else []:
        m = re.match(r"^\*\*([^*]+):\*\*\s*(.+)$", l)
        if m:
            skills += [x.strip() for x in m.group(2).split("·") if x.strip()]
    if not skills:   # pre-v3 layout: Skills rows sit after the experience block
        for l in lines:
            m = re.match(r"^\*\*([^*]+):\*\*\s*(.+)$", l)
            if m and "LANGUAGES" not in m.group(1):
                skills += [x.strip() for x in m.group(2).split("·") if x.strip()]
    j = next((k for k, l in enumerate(lines) if l.startswith("**EDUCATION**")), len(lines)) + 1
    while j + 1 < len(lines) and lines[j].startswith("**") and "  ·  " in lines[j]:
        parts += [strip_md(lines[j].split("  ·  ")[0]), lines[j + 1].strip()]
        j += 2
    return " ".join(parts + skills).lower()


def cv_delta(md: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """The Delta block as cards [{line, title, bullets:[{lead, body}]}] and the Skills items."""
    lines = [l.rstrip("\r") for l in md.read_text(encoding="utf-8-sig").splitlines()]
    cards: list[dict[str, Any]] = []
    mode, skills = None, []
    for l in lines:
        if l == "**TECHNICAL SKILLS**":
            mode = "skills"
            continue
        if l == "**PROFESSIONAL EXPERIENCE**":
            mode = None
            continue
        if L.ROLE_RE.match(l):
            mode = "delta" if "Delta Electronics" in l else None
            continue
        if re.match(r"\*\*[A-Z ,&]+\*\*", l) and strip_md(l).split("  ")[0].isupper():
            mode = None   # next section header (EDUCATION, LANGUAGES …)
        if mode is None and re.match(r"^\*\*([^*]+):\*\*\s*(.+)$", l) and "LANGUAGES" not in l:
            skills += [x.strip() for x in re.match(r"^\*\*([^*]+):\*\*\s*(.+)$", l).group(2).split("·") if x.strip()]
            continue
        if mode == "skills":
            m = re.match(r"^\*\*([^*]+):\*\*\s*(.+)$", l)
            if m:
                skills += [x.strip() for x in m.group(2).split("·") if x.strip()]
            elif l.startswith("**") and l.endswith("**"):
                mode = None
        elif mode == "delta" and l:
            if l.startswith("- "):
                m = re.match(r"^- \*\*(.+?) —\*\* (.*)$", l)
                lead, body = (m.group(1), m.group(2)) if m else ("", strip_md(l[2:]))
                if not cards:   # pre-v3 layout: flat Delta bullets, no project cards
                    cards.append({"title": "(flat Delta bullets, no card)", "context": "", "bullets": []})
                cards[-1]["bullets"].append({"lead": lead, "body": body})
            elif l.startswith("**"):
                m = re.match(r"^\*\*(.+?)\*\* — (.*)$", l)
                cards.append({"title": m.group(1) if m else strip_md(l), "context": m.group(2) if m else "",
                              "bullets": []})
    return cards, skills


# ---------------------------------------------------------------- locating the application
def find_app_dir(con: sqlite3.Connection, jd: str) -> Path | None:
    row = con.execute("SELECT dir FROM applications WHERE jd = ? COLLATE NOCASE", (jd,)).fetchone()
    if row and (L.app_path(row["dir"]) / "03_cv.md").exists():
        return L.app_path(row["dir"])
    for d in sorted((ROOT / "cv" / "applications").glob(f"*_{jd}_*"), reverse=True):
        for cand in (d / "v3_cards", d):
            if (cand / "03_cv.md").exists():
                return cand
    return None


def recorded_jd(con: sqlite3.Connection, jd: str) -> str | None:
    row = con.execute("SELECT jd FROM applications WHERE jd = ? COLLATE NOCASE", (jd,)).fetchone()
    return row["jd"] if row else None


def read_letter(path: Path) -> str:
    """Plain text of a letter: .txt as is, .docx via word/document.xml (stdlib)."""
    if path.suffix.lower() == ".docx":
        xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
        paras = []
        for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
            t = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p))
            if t.strip():
                paras.append(t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                             .replace("&quot;", '"').replace("&apos;", "'"))
        return "\n".join(paras)
    return path.read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------- per-term table
def term_rows(con: sqlite3.Connection, job: dict[str, Any], cv_text: str, idx: dict[str, Any]) -> list[dict[str, Any]]:
    kw = {r["term"].casefold(): r for r in con.execute("SELECT * FROM keywords")}
    rows, seen = [], set()
    for field, typ, w in TYPES:
        for t in job.get(field) or []:
            if not isinstance(t, str) or t.casefold() in seen:
                continue
            seen.add(t.casefold())
            r = L.resolve_term(t, idx["aliases"])
            code = r["code"]
            c = idx["concepts"].get(code) if code else None
            k = kw.get(t.casefold())
            term_cov = (k["coverage"] if k else "") or ""
            claim = c["claim"] if c else "unresolved"
            # a term the keyword map marks gap / do not claim stays unclaimable even under a covered concept
            eff = term_cov.casefold() if term_cov.casefold() in NO_CLAIM else claim
            rows.append({"term": t, "type": typ, "w": w, "on_cv": keyword_in_text(t, cv_text),
                         "concept": code, "concept_name": c["name"] if c else None,
                         "concept_kind": c["kind"] if c else None,
                         "excluded": bool(code and code in idx["excluded"]),
                         "claim": eff, "term_coverage": term_cov or None,
                         "spelling": (k["spelling"] if k and k["spelling"] else t),
                         "note": (k["notes"] if k and k["notes"] else None)})
    return rows


def coverage(rows: list[dict[str, Any]], letter_text: str | None = None) -> dict[str, Any]:
    out = {}
    for name, sel in (("required", [r for r in rows if r["type"] == "required"]), ("all", rows)):
        cv = {r["term"] for r in sel if r["on_cv"]}
        lt = {r["term"] for r in sel if letter_text is not None and keyword_in_text(r["term"], letter_text)}
        out[name] = {"n": len(sel), "cv": len(cv), "letter": len(lt), "cv_only": len(cv - lt),
                     "letter_only": len(lt - cv), "both": len(cv & lt), "union": len(cv | lt)}
    return out


# ---------------------------------------------------------------- plan
def build_plan(jd: str, app_dir: Path | None = None, keywords: Path | None = None) -> dict[str, Any]:
    con = L.connect()
    app_dir = app_dir or find_app_dir(con, jd)
    if app_dir is None or not (app_dir / "03_cv.md").exists():
        raise SystemExit(f"no application dir with 03_cv.md for {jd!r} (pass --dir)")
    kw_path = keywords or app_dir / "07_job_keywords.json"
    if not kw_path.exists():
        raise SystemExit(f"{kw_path} missing — write the JD keywords (tailor-cv references/ats_keywords.md) "
                         "into the letter dir and pass --keywords")
    job = json.loads(kw_path.read_text(encoding="utf-8-sig"))
    cv_md = app_dir / "03_cv.md"
    cv_text = cv_scan_text(cv_md)
    idx = L.concept_index(con)
    rows = term_rows(con, job, cv_text, idx)

    # CV picked cards → bullet codes (body match against the library), Skills items
    cards, skill_items = cv_delta(cv_md)
    bodies = {r["code"]: r["body"] for r in con.execute("SELECT code, body FROM bullets")}
    for card in cards:
        for b in card["bullets"]:
            exact = next((c for c, body in bodies.items() if body.strip() == b["body"].strip()), None)
            b["library_body"] = exact is not None
            if exact is None:   # body edited in the library since this CV was built → closest body ≥ 0.6
                best = max(bodies.items(), key=lambda kv: difflib.SequenceMatcher(None, kv[1], b["body"]).ratio())
                exact = best[0] if difflib.SequenceMatcher(None, best[1], b["body"]).ratio() >= 0.6 else None
            b["code"] = exact   # library_body false = the CV's wording is older; the letter follows the CV
    on_cv_bullets = {b["code"] for card in cards for b in card["bullets"] if b["code"]}
    skills_lc = " · ".join(skill_items).lower()

    # concepts the JD names
    concepts: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r["concept"] or r["excluded"]:
            continue
        c = concepts.setdefault(r["concept"], {"code": r["concept"], "name": r["concept_name"],
                                               "kind": r["concept_kind"], "w": 0, "type": None,
                                               "claim": idx["concepts"][r["concept"]]["claim"],
                                               "terms": [], "on_cv_terms": [], "missing_terms": []})
        if r["w"] > c["w"]:
            c["w"], c["type"] = r["w"], r["type"]
        c["terms"].append(r["term"])
        (c["on_cv_terms"] if r["on_cv"] else c["missing_terms"]).append(
            {"term": r["term"], "type": r["type"], "claim": r["claim"]})
    for code, c in concepts.items():
        ev = [dict(e) for e in con.execute("SELECT * FROM concept_evidence WHERE concept_code=?", (code,))]
        c["evidence_on_cv"] = [f"{e['kind']} {e['code']} ({e['strength']})" for e in ev
                               if (e["kind"] == "bullet" and e["code"] in on_cv_bullets)
                               or (e["kind"] == "skill" and keyword_in_text(e["code"], skills_lc))]
        c["evidence_other"] = [f"{e['kind']} {e['code']} ({e['strength']})" for e in ev
                               if f"{e['kind']} {e['code']} ({e['strength']})" not in c["evidence_on_cv"]]
        c["direct_on_cv"] = any(s.endswith("(direct)") for s in c["evidence_on_cv"])

    # align summary rows (recorded picks only)
    summary_rows: list[dict[str, Any]] = []
    rjd = recorded_jd(con, jd)
    if rjd:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                al = L.align(con, app_dir, L.recorded_picks(con, rjd))
            summary_rows = [x for x in al["rows"] if x["action"] == "summary"]
        except Exception as e:  # advisory only
            summary_rows = [{"error": str(e)}]

    term_note = {r["term"]: r["note"] for r in rows}   # keyword-map wording hints + CONF ids

    def claimable_missing(c: dict[str, Any], typ: str = "required") -> list[str]:
        return [m["term"] for m in c["missing_terms"] if m["type"] == typ and claimable(m["claim"])]

    def entry(c: dict[str, Any], write: str, also: list[str], why: str) -> dict[str, Any]:
        return {"code": c["code"], "name": c["name"], "write": write, "also": also, "claim": c["claim"], "why": why,
                "term_notes": {t: term_note[t] for t in [write, *also] if term_note.get(t)},
                "evidence_on_cv": c["evidence_on_cv"], "evidence_other": c["evidence_other"][:4]}

    def order(c: dict[str, Any]) -> tuple:
        return (not c["direct_on_cv"], not c["evidence_on_cv"], c["claim"] != "covered",
                -len(claimable_missing(c)), c["code"])

    cands = [c for c in concepts.values()
             if c["w"] == 3 and claimable(c["claim"]) and claimable_missing(c) and c["code"] not in SKIP]
    absent = sorted((c for c in cands if not any(t["type"] == "required" for t in c["on_cv_terms"])), key=order)
    partial = sorted((c for c in cands if c not in absent), key=order)
    names: list[dict[str, Any]] = []
    for c in absent:   # tier 1: no required term of the concept on the CV
        miss = claimable_missing(c)
        names.append(entry(c, miss[0], miss[1:], "required, SoT-claimable, no required term of it on the CV"))
    term_claim = {r["term"]: r["claim"] for r in rows}
    for s in summary_rows:   # tier 2: align summary (proven by the picked lines via a tool name only)
        c = concepts.get(s.get("concept", ""))
        if c and c["code"] not in {n["code"] for n in names} and claimable(term_claim.get(s["jd"])) \
                and c["code"] not in SKIP:
            names.append(entry(c, s["jd"], [], "align summary: the picked lines prove it only via a tool name"))
    for c in partial:   # tier 3: concept on the CV, but a claimable required spelling of it is missing
        miss = claimable_missing(c)
        names.append(entry(c, miss[0], miss[1:], "concept on the CV (" + ", ".join(t["term"] for t in c["on_cv_terms"])
                           + "), required JD spelling missing"))
    if len(names) < CAP:   # tier 4 (only to fill the cap): preferred, then keyword concepts absent from the CV
        for typ in ("preferred", "keyword"):
            for c in sorted(concepts.values(), key=order):
                miss = claimable_missing(c, typ)
                if (miss and claimable(c["claim"]) and c["code"] not in SKIP and not c["on_cv_terms"]
                        and c["code"] not in {n["code"] for n in names}):
                    names.append(entry(c, miss[0], miss[1:], f"{typ} term, concept absent from the CV (filler tier)"))
    names = names[:CAP]

    # company / industry facts
    facts: list[str] = []
    company = job.get("company") or jd
    brief = app_dir / "01_jd.md"
    pat = re.compile(rf"{re.escape(company.split()[0])}|compan|industr|product|mission|customer|platform|team|about",
                     re.I)
    if brief.exists():
        bl = [l.strip() for l in brief.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        facts += [l[:400] for l in bl[:3] if not l.startswith("# ")] or bl[:1]
        facts += [l[:400] for l in bl[3:] if pat.search(l) and l[:400] not in facts][:4]
    jdf = app_dir / "jd.md"
    if jdf.exists():
        jl = [l.strip() for l in jdf.read_text(encoding="utf-8-sig").splitlines() if len(l.strip()) > 40]
        facts += [f"jd.md: {l[:300]}" for l in jl if re.search(re.escape(company.split()[0]), l, re.I)][:5]

    plan = {
        "jd": jd, "company": company, "role": job.get("role"), "app_dir": str(app_dir.relative_to(ROOT)),
        "cv": str(cv_md.relative_to(ROOT)), "keywords_file": str(kw_path.resolve().relative_to(ROOT)),
        "keywords_note": job.get("_note"),
        "coverage_cv": coverage(rows)["required"] | {"all_terms": coverage(rows)["all"]},
        "letter_should_name": names,
        "cv_cards": cards,
        "recorded_picks": L.picks_to_spec(L.recorded_picks(con, rjd)) if rjd else None,
        "company_facts": facts,
        "concepts": sorted(concepts.values(), key=lambda c: (-c["w"], c["code"])),
        "terms": rows,
        "align_summary_rows": summary_rows,
    }
    return plan


def print_summary(p: dict[str, Any], f=sys.stderr) -> None:
    cv = p["coverage_cv"]
    print(f"{p['jd']} · {p['role']} · CV {p['cv']}", file=f)
    print(f"required terms on CV {cv['cv']}/{cv['n']} · all terms {cv['all_terms']['cv']}/{cv['all_terms']['n']}", file=f)
    print("letter should name:", file=f)
    for n in p["letter_should_name"]:
        print(f"  - {n['code']} → write {n['write']!r}" + (f" (also {', '.join(n['also'])})" if n["also"] else "")
              + f" · {n['claim']} · on CV: {', '.join(n['evidence_on_cv']) or '-'}", file=f)
    if not p["letter_should_name"]:
        print("  (none — every required claimable concept is already on the CV)", file=f)
    print("CV cards: " + " | ".join(f"{c['title']} [{', '.join(b['code'] or '?' for b in c['bullets'])}]"
                                    for c in p["cv_cards"]), file=f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jd", required=True)
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--keywords", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    plan = build_plan(a.jd, a.dir.resolve() if a.dir else None, a.keywords.resolve() if a.keywords else None)
    js = json.dumps(plan, ensure_ascii=False, indent=2)
    if a.out:
        a.out.write_text(js + "\n", encoding="utf-8")
        print(f"plan -> {a.out}", file=sys.stderr)
    else:
        print(js)
    print_summary(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
