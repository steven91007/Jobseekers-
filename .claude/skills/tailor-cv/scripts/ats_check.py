"""External ATS check for a tailored CV, using Resume-Matcher's deterministic modules
(vendored under scripts/ext/resume_matcher — see NOTICE.md there). No LLM, no network.

Called through `build_cv.py ats` (Windows venv via build_cv.sh):

    build_cv.py ats --dir <abs $D> [--keywords <abs $D>/07_job_keywords.json]

Inputs : $D/03_cv.md, $D/CV_*.pdf, $D/CV_*.docx, $D/07_job_keywords.json
         (keywords JSON written by the writer from the JD — references/ats_keywords.md)
Outputs: $D/07_external_ats.json (raw) and $D/07_external_ats.md (summary for the review)

What it measures
- ats_parse checks: text layer, garbled glyphs, text coverage, multi-column, tables, text boxes,
  header/footer content, images, page count, contact info, section headings, dates
  → extraction+layout score and content score (100 minus severity penalties).
- keyword match: whole-word, case-insensitive, no synonyms, over required_skills +
  preferred_skills + keywords; scanned text = summary, work entries, education, projects and
  Skills *items* (not the row labels, not the headline).
- compute_ats_score: keyword 40 / skills 20 / title 15 / sections 15 / dates 10.
- when cv/profile/experience.sqlite exists, 07_external_ats.md also groups the missing terms by
  their keyword-map status (library_db.py; no change to the score).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

EXT = Path(__file__).resolve().parent / "ext" / "resume_matcher"
LIBRARY_DB = Path(__file__).resolve().parents[4] / "cv" / "profile" / "experience.sqlite"
sys.path.insert(0, str(EXT))

from app.services.ats import compute_ats_score  # noqa: E402
from app.services.ats_parse.checks import run_checks, score  # noqa: E402
from app.services.ats_parse.extract import extract_docx, extract_pdf  # noqa: E402
from app.services.text_match import keyword_in_text  # noqa: E402

BOLD = re.compile(r"\*\*(.+?)\*\*")


def strip_md(s: str) -> str:
    return BOLD.sub(r"\1", s).strip()


def resume_from_md(md: Path) -> dict[str, Any]:
    """03_cv.md → the resume dict shape Resume-Matcher's parser would produce."""
    lines = [l.rstrip("\r") for l in md.read_text(encoding="utf-8-sig").splitlines()]
    name = strip_md(lines[0]).split("  ")[0]
    title = strip_md(lines[1])
    contact = lines[2]
    email = re.search(r"[\w.+-]+@[\w.-]+", contact)
    phone = re.search(r"\+?\d[\d ]{8,}", contact)
    summary = strip_md(lines[4])

    work: list[dict[str, Any]] = []
    i = next(k for k, l in enumerate(lines) if l.startswith("**PROFESSIONAL EXPERIENCE**")) + 1
    while i < len(lines) and lines[i]:
        l = lines[i]
        if "\t" in l and l.startswith("**"):
            head, years = l.split("\t")
            t, company = [strip_md(x) for x in head.split("  |  ")]
            work.append({"id": len(work) + 1, "title": t, "company": company, "years": years.strip(),
                         "description": [], "descriptionStyles": []})
        else:
            body = strip_md(l[2:] if l.startswith("- ") else l)
            work[-1]["description"].append(body)
            work[-1]["descriptionStyles"].append("bullet" if l.startswith("- ") else "plain")
        i += 1

    # every `**Label:** items` row in the Skills section counts, including a single-item row (no " · ")
    skills: list[str] = []
    s0 = next((k for k, l in enumerate(lines) if l.startswith("**TECHNICAL SKILLS**")), -1)
    s1 = next((k for k, l in enumerate(lines) if l.startswith("**PROFESSIONAL EXPERIENCE**")), len(lines))
    for l in lines[s0 + 1:s1] if s0 >= 0 else lines:
        m = re.match(r"^\*\*([^*]+):\*\*\s*(.+)$", l)
        if m:
            skills += [s.strip() for s in m.group(2).split("·") if s.strip()]

    edu: list[dict[str, Any]] = []
    j = next(k for k, l in enumerate(lines) if l.startswith("**EDUCATION**")) + 1
    while j + 1 < len(lines) and lines[j].startswith("**") and "  ·  " in lines[j]:
        deg, yrs = [strip_md(x) for x in lines[j].split("  ·  ")]
        edu.append({"id": len(edu) + 1, "degree": deg, "institution": lines[j + 1].strip(), "years": yrs})
        j += 2

    langs: list[str] = []
    for l in lines:
        if l.startswith("**LANGUAGES**"):
            langs = [s.strip() for s in strip_md(l).replace("LANGUAGES", "").split("·")]
    return {
        "personalInfo": {"name": name, "title": title, "email": email.group(0) if email else "",
                         "phone": phone.group(0).strip() if phone else "",
                         "location": contact.split("|")[0].strip()},
        "summary": summary,
        "workExperience": work,
        "education": edu,
        "personalProjects": [],
        "additional": {"technicalSkills": skills, "languages": langs, "certifications": [], "awards": []},
    }


# --- copied from Resume-Matcher app/services/refiner.py (calculate_keyword_match + helpers) ---
def job_keyword_terms(jd_keywords: dict[str, Any]) -> list[str]:
    terms: dict[str, None] = {}
    for field in ("required_skills", "preferred_skills", "keywords"):
        for term in jd_keywords.get(field) or []:
            if isinstance(term, str):
                terms.setdefault(term, None)
    return list(terms)


def _extract_all_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    if data.get("summary"):
        parts.append(str(data["summary"]))
    for exp in data.get("workExperience", []):
        parts += [str(exp.get("title", "")), str(exp.get("company", ""))]
        parts.extend(str(d) for d in exp.get("description", []))
    for edu in data.get("education", []):
        parts += [str(edu.get("degree", "")), str(edu.get("institution", ""))]
    for proj in data.get("personalProjects", []):
        parts.append(str(proj.get("name", "")))
        parts.extend(str(d) for d in proj.get("description", []))
    parts.extend(str(s) for s in data.get("additional", {}).get("technicalSkills", []))
    return " ".join(parts)


def calculate_keyword_match(resume: dict[str, Any], jd: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    resume_text = _extract_all_text(resume).lower()
    terms = job_keyword_terms(jd)
    if not terms:
        return 0.0, [], []
    found = [kw for kw in terms if keyword_in_text(kw, resume_text)]
    missing = [kw for kw in terms if kw not in found]
    return len(found) / len(terms) * 100, found, missing
# ---------------------------------------------------------------------------------------------


def repeated_terms(app_dir: Path, job: dict[str, Any], resume: dict[str, Any], min_count: int = 3) -> list[dict[str, Any]]:
    """JD terms repeated ≥ min_count times should also appear in the Summary and the Skills items
    (resumematcher.fyi, "Dissect the job description"). Needs $D/jd.md."""
    jd_path = app_dir / "jd.md"
    if not jd_path.exists():
        return []
    jd_text = jd_path.read_text(encoding="utf-8-sig").lower()
    summary = str(resume.get("summary", "")).lower()
    skills = " ".join(resume.get("additional", {}).get("technicalSkills", [])).lower()
    rows = []
    for term in job_keyword_terms(job):
        n = len(re.findall(rf"(?<!\w){re.escape(term.lower())}(?!\w)", jd_text))
        if n >= min_count:
            rows.append({"term": term, "jd_count": n,
                         "in_summary": keyword_in_text(term, summary),
                         "in_skills": keyword_in_text(term, skills)})
    return sorted(rows, key=lambda r: -r["jd_count"])


def parse_check(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    doc = extract_pdf(content) if path.suffix.lower() == ".pdf" else extract_docx(content)
    checks = run_checks(doc, {"locale": "en", "template": None, "cv_mode": False})
    return {
        "file": path.name,
        "pages": doc.page_count,
        "images": doc.images,
        "extraction_layout_score": score(checks, {"extraction", "layout"}),
        "content_score": score(checks, {"content"}),
        "checks": [{"id": c.id, "category": c.category, "severity": c.severity, "status": c.status,
                    "params": c.params} for c in checks],
        "text_preview": doc.text[:600],
    }


def library_status(missing: list[str], app_dir: Path, db: Path = LIBRARY_DB) -> list[str]:
    """Missing terms grouped by the keyword map in the experience library (read-only)."""
    if not db.exists() or not missing:
        return []
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        here = next((a["jd"] for a in con.execute("SELECT jd, dir FROM applications")
                     if Path(a["dir"].replace("\\", "/")).parts[-2:] == app_dir.resolve().parts[-2:]), None)
        rows = {r["term"].casefold(): r for r in con.execute("SELECT * FROM keywords")}
        groups: dict[str, list[str]] = {k: [] for k in ("covered elsewhere", "SoT-supported wording", "gap",
                                                         "do not claim", "unclassified", "other")}
        for term in missing:
            k = rows.get(term.casefold())
            cov = (k["coverage"] or "").casefold() if k else ""
            # spec v3 §3.6: the concept code next to the term (DBs before v3 have no concept_code column)
            code = k["concept_code"] if k is not None and "concept_code" in k.keys() and k["concept_code"] else None
            term = f"{term} [{code}]" if code else term
            if k is None or (k["notes"] or "").startswith("auto: unclassified"):
                groups["unclassified"].append(term)
            elif cov == "covered":
                carry = [r[0] for r in con.execute("SELECT bullet_code FROM keyword_bullets WHERE term=?", (k["term"],))]
                carry += [r[0] for r in con.execute("SELECT skill_name FROM keyword_skills WHERE term=?", (k["term"],))]
                jds = [r[0] for r in con.execute("SELECT jd FROM keyword_jds WHERE term=? AND jd IS NOT ?",
                                                 (k["term"], here))]
                groups["covered elsewhere"].append(
                    f"{term} ({', '.join(carry) or 'Summary/headline wording'}; {', '.join(jds) or '-'})")
            elif cov in ("sot-supported", "sot supported", "supported"):
                groups["SoT-supported wording"].append(term + (f" ({k['notes']})" if k["notes"] else ""))
            elif cov in ("gap", "do not claim"):
                groups[cov].append(term + (f" ({k['notes']})" if k["notes"] else ""))
            else:
                groups["other"].append(f"{term} [{k['coverage']}]")
    finally:
        con.close()
    out = ["", "## Missing terms by library status (experience.sqlite keyword map; score unchanged)",
           "covered elsewhere = the library carries it (bullet / Skills item; JDs it was found in) → wording or "
           "pick fix · unclassified = new or auto-ingested → `library_db.py set-coverage` · [CODE] = concept "
           "(`library_db.py concept CODE`, `gaps --jd <Co>`)"]
    out += [f"- **{name}** ({len(terms)}): " + " · ".join(terms) for name, terms in groups.items() if terms]
    return out


def _md_report(report: dict[str, Any], keywords_name: str, app_dir: Path | None = None) -> str:
    out = ["# External ATS check — Resume-Matcher modules (local, deterministic)", ""]
    out.append(f"Keywords: `{keywords_name}` · raw: `07_external_ats.json` · scanned text = Summary, "
               "experience, education, Skills items (not row labels, not the headline); whole-word, no synonyms.")
    out += ["", "## Parse check", "| File | Extraction + layout | Content | Non-pass checks |", "|---|---|---|---|"]
    for pc in report["parse_checks"]:
        flagged = [f"{c['id']} {c['status'].upper()} ({c['severity']}; {json.dumps(c['params'], ensure_ascii=False)})"
                   for c in pc["checks"] if c["status"] != "pass"]
        out.append(f"| {pc['file']} | **{pc['extraction_layout_score']}** | {pc['content_score']} | "
                   f"{'; '.join(flagged) or 'none'} |")
    a, km = report["ats_score"], report["keyword_match"]
    sub = a.get("sub_scores", {})
    out += ["", "## Score (keyword 40 / skills 20 / title 15 / sections 15 / dates 10)",
            f"**Overall {a.get('overall_score')}** — " + " · ".join(f"{k} {v}" for k, v in sub.items()),
            "", f"Keyword match {km['percent']}% ({len(km['found'])}/{km['n_terms']})",
            "", "Found: " + " · ".join(km["found"]),
            "", "Missing: " + " · ".join(km["missing"]),
            "", "Tool recommendations:"] + [f"- {r}" for r in a.get("recommendations", [])]
    if report.get("repeated_terms"):
        out += ["", "## JD terms repeated ≥ 3× (should also sit in the Summary and the Skills items)",
                "| Term | JD count | in Summary | in Skills |", "|---|---|---|---|"]
        out += [f"| {r['term']} | {r['jd_count']} | {'yes' if r['in_summary'] else '**no**'} | "
                f"{'yes' if r['in_skills'] else '**no**'} |" for r in report["repeated_terms"]]
    if app_dir is not None:
        out += library_status(km["missing"], app_dir)
    out += ["", "Next: the writer classifies each missing term as *SoT-supported wording* (fix via library "
            "wording / lead variant) or *gap*; the Codex review reads this file instead of recounting."]
    return "\n".join(out) + "\n"


def run(app_dir: Path, keywords: Path | None = None) -> dict[str, Any]:
    keywords = keywords or app_dir / "07_job_keywords.json"
    job = json.loads(keywords.read_text(encoding="utf-8-sig"))
    resume = resume_from_md(app_dir / "03_cv.md")
    pct, found, missing = calculate_keyword_match(resume, job)
    files = sorted(app_dir.glob("CV_*.pdf")) + sorted(app_dir.glob("CV_*.docx"))
    report = {
        "tool": "srbhr/Resume-Matcher ats.py + ats_parse (vendored, deterministic)",
        "parse_checks": [parse_check(p) for p in files],
        "keyword_match": {"percent": round(pct, 1), "found": found, "missing": missing,
                          "n_terms": len(found) + len(missing)},
        "ats_score": compute_ats_score(resume, job, pct, missing, []),
        "repeated_terms": repeated_terms(app_dir, job, resume),
        "resume_json_used": resume,
    }
    (app_dir / "07_external_ats.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = _md_report(report, keywords.name, app_dir)
    (app_dir / "07_external_ats.md").write_text(md, encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(md)
    return report


if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
