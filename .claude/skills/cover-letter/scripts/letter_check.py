"""Advisory keyword check: CV + cover letter against the JD terms (same whole-word rule as the
external ATS check). Stdlib only; run with WSL `python3`. Always exits 0.

    python3 letter_check.py --jd Scout24 --letter <letter dir>/letter.txt|.docx [--dir <app dir>] \
                            [--keywords <07_job_keywords.json>] [--txt-out <letter dir>/letter.txt] \
                            [--json <letter dir>/letter_check.json] [--md <letter dir>/letter_check.md]

Table per JD term (required first, then preferred, keyword): type · on CV · in letter · concept · claim.
Summary: required-term coverage CV-only / letter-only / both / union, and the same over all terms.
Flags: a term the letter says whose claim is gap / do not claim (over-claim); a JD term the letter
repeats ≥ 3× (stuffing).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import letter_plan as P  # noqa: E402

ORDER = {"required": 0, "preferred": 1, "keyword": 2}


def check(jd: str, letter: Path, app_dir: Path | None = None, keywords: Path | None = None) -> dict:
    con = P.L.connect()
    app_dir = app_dir or P.find_app_dir(con, jd)
    if app_dir is None:
        raise SystemExit(f"no application dir for {jd!r} (pass --dir)")
    kw_path = keywords or app_dir / "07_job_keywords.json"
    job = json.loads(kw_path.read_text(encoding="utf-8-sig"))
    cv_text = P.cv_scan_text(app_dir / "03_cv.md")
    text = P.read_letter(letter)
    lt = text.lower()
    rows = P.term_rows(con, job, cv_text, P.L.concept_index(con))
    for r in rows:
        r["in_letter"] = P.keyword_in_text(r["term"], lt)
        r["letter_count"] = len(re.findall(rf"(?<!\w){re.escape(r['term'].lower())}(?!\w)", lt))
    rows.sort(key=lambda r: ORDER[r["type"]])
    cov = P.coverage(rows, lt)
    role = (job.get("role") or "").lower()
    flags = [f"over-claim? '{r['term']}' is in the letter but its claim is {r['claim']} "
             "(fine only where it describes the company, e.g. the company_line)"
             for r in rows if r["in_letter"] and not r["on_cv"] and r["claim"] in P.NO_CLAIM]
    # the template repeats the job title 3×; stuffing = any other JD term ≥ 3×
    flags += [f"stuffing? '{r['term']}' appears {r['letter_count']}× in the letter"
              for r in rows if r["letter_count"] >= 3 and len(r["term"]) > 3 and r["term"].lower() not in role]
    return {"jd": jd, "letter": str(letter), "keywords_file": str(kw_path), "coverage": cov, "flags": flags,
            "added_by_letter": [r["term"] for r in rows if r["in_letter"] and not r["on_cv"]],
            "rows": rows, "text": text}


def markdown(res: dict) -> str:
    yn = {True: "yes", False: "·"}
    out = [f"# Letter keyword check — {res['jd']}", "",
           f"Keywords: `{Path(res['keywords_file']).name}` · whole-word, case-insensitive, no synonyms "
           "(external ATS rule) · advisory", "",
           "| Term | Type | On CV | In letter | Concept | Claim |", "|---|---|---|---|---|---|"]
    for r in res["rows"]:
        out.append(f"| {r['term']} | {r['type']} | {yn[r['on_cv']]} | {yn[r['in_letter']]} | "
                   f"{r['concept'] or 'NEW'} | {r['claim']} |")
    out += ["", "| Scope | Terms | CV | Letter | CV-only | Letter-only | Both | Union |", "|---|---|---|---|---|---|---|---|"]
    for name in ("required", "all"):
        c = res["coverage"][name]
        out.append(f"| {name} | {c['n']} | {c['cv']} | {c['letter']} | {c['cv_only']} | {c['letter_only']} | "
                   f"{c['both']} | **{c['union']}** |")
    out += ["", "Added by the letter (not on the CV): " + (" · ".join(res["added_by_letter"]) or "none")]
    out += [f"- FLAG {f}" for f in res["flags"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jd", required=True)
    ap.add_argument("--letter", required=True, type=Path)
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--keywords", type=Path)
    ap.add_argument("--txt-out", type=Path, help="write the letter's plain text (from a .docx) here")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--md", type=Path)
    a = ap.parse_args(argv)
    try:
        res = check(a.jd, a.letter.resolve(), a.dir.resolve() if a.dir else None,
                    a.keywords.resolve() if a.keywords else None)
    except SystemExit as e:   # advisory: report and exit 0
        print(f"letter_check: {e}", file=sys.stderr)
        return 0
    if a.txt_out:
        a.txt_out.write_text(res["text"] + "\n", encoding="utf-8")
    md = markdown(res)
    if a.md:
        a.md.write_text(md, encoding="utf-8")
    if a.json:
        a.json.write_text(json.dumps({k: v for k, v in res.items() if k != "text"}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
