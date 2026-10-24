"""Deterministic checks for the HR & Hiring Manager Review Gate.

Pure standard library — runs under WSL `python3 -I`, no venv needed.

    review_tools.py gate      --review 04_review_r1.md --cv 03_cv.md [--threshold 85] [--max-bullets 5]
    review_tools.py ledger    --evidence 02_evidence_map.md [--cv 03_cv.md]
    review_tools.py compare   --before 04_review_r1.md --after 05_final_gate.md
    review_tools.py recommend --review 05_final_gate.md --cv 03_cv.md --layout 06_layout.json [--threshold 85]

`gate` validates the review against the rubric's output format (a malformed review
fails — it never passes silently), recomputes the weighted score, counts Delta
bullets, reads the claim audit / scope / writing-issue sections and derives the verdict
the rules require. The derived verdict wins over the reviewer's own.

Exit codes: gate → 0 only for PASS; recommend → 0 only for "Ready to Apply";
ledger → 1 when errors are present, otherwise 0; compare → 0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STATUSES = ("Verified", "Template", "User-reported", "Inferred", "Missing Evidence", "Contradicted")
SEVERITIES = ("high", "medium", "low")
# Logistics (location, work permit, package) are hard requirements, scored separately — not here.
CATEGORIES = ("Core technical competencies", "Relevant professional experience",
              "Production engineering and MLOps", "Senior ownership and collaboration",
              "Measurable outcomes and evidence quality", "Clarity and readability")
WEIGHT_HEADER = ("category", "weight", "points", "evidence", "weaknesses", "improvement")
CLAIM_HEADER = ("#", "claim", "status", "severity", "note")
LEDGER_HEADER = ("id", "claim", "status", "source", "measurement", "notes")  # evidence_rules.md
REQUIRED_SECTIONS = ("Weighted job fit", "Claim audit", "Writing issues", "Out-of-scope edits")
NONE_VALUES = {"none", "(none)", "- none"}
RETIRED_RE = re.compile(r"\(removed\)|\(retired\)|no longer claimed", re.I)  # ledger history rows


def is_blocking(status: str, severity: str) -> bool:
    """evidence_rules.md: Contradicted and Missing Evidence never ship; Inferred only if low."""
    return status in ("Contradicted", "Missing Evidence") or (status == "Inferred" and severity != "low")


# ----------------------------------------------------------------- parsing ---

def sections(text: str) -> dict[str, list[str]]:
    """Map exact `## <title>` headings to their bodies (a list, to detect duplicates)."""
    out: dict[str, list[str]] = {}
    parts = re.split(r"^##[ \t]+(.+?)[ \t]*$", text, flags=re.M)
    for i in range(1, len(parts), 2):
        out.setdefault(parts[i].strip(), []).append(parts[i + 1].strip())
    return out


def _section(text: str, title: str, errors: list[str] | None = None) -> str:
    found = sections(text).get(title, [])
    if errors is not None:
        if not found:
            near = [t for t in sections(text) if t.lower().startswith(title.lower())]
            errors.append(f"section '## {title}' missing" + (f" (found '## {near[0]}' — heading must be exact)" if near else ""))
        elif len(found) > 1:
            errors.append(f"section '## {title}' appears {len(found)} times")
    return found[0] if found else ""


def _is_none(body: str) -> bool:
    return body.strip().casefold() in NONE_VALUES


def _table(body: str, header: tuple[str, ...], name: str, errors: list[str]) -> list[list[str]]:
    """Validate a Markdown table's header and column count; return its data rows."""
    lines = [l.strip() for l in body.splitlines() if l.strip().startswith("|")]
    if not lines:
        errors.append(f"{name}: table missing")
        return []
    # split on unescaped pipes only ("Engineer \\| Delta" is one cell), then unescape
    cells = lambda l: [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", l.strip().strip("|"))]
    head = [c.lower() for c in cells(lines[0])]
    if len(head) != len(header) or any(not h.startswith(want) for h, want in zip(head, header)):
        errors.append(f"{name}: header {cells(lines[0])} != expected {list(header)}")
        return []
    rows = []
    for l in lines[1:]:
        if set(l.replace("|", "").strip()) <= set("-: "):
            continue  # separator row
        r = cells(l)
        if len(r) != len(header):
            errors.append(f"{name}: row has {len(r)} columns, expected {len(header)} (unescaped '|'?): {l[:80]}")
            continue
        rows.append(r)
    return rows


def _num(cell: str) -> float | None:
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(?:/\s*\d+(?:\.\d+)?)?\s*", cell)
    return float(m.group(1)) if m else None


def parse_weighted(text: str, errors: list[str] | None = None) -> dict:
    errors = [] if errors is None else errors
    rows = _table(_section(text, "Weighted job fit", errors), WEIGHT_HEADER, "Weighted job fit", errors)
    cats, seen = [], []
    for r in rows:
        name, w, p = r[0], _num(r[1]), _num(r[2])
        if name.lower().startswith("total"):
            continue
        if name not in CATEGORIES:
            errors.append(f"Weighted job fit: unknown category '{name}'")
            continue
        if name in seen:
            errors.append(f"Weighted job fit: category '{name}' repeated")
            continue
        seen.append(name)
        if w is None or p is None:
            errors.append(f"Weighted job fit: non-numeric weight/points in '{name}'")
            continue
        if not 0 <= p <= w:
            errors.append(f"Weighted job fit: {name}: points {p} outside 0..{w}")
        cats.append({"category": name, "weight": w, "points": p})
    missing = [c for c in CATEGORIES if c not in seen]
    if rows and missing:
        errors.append(f"Weighted job fit: missing categories {missing}")
    wsum = sum(c["weight"] for c in cats)
    if cats and abs(wsum - 100) > 1e-6:
        errors.append(f"Weighted job fit: weights sum to {wsum:g}, not 100")
    total = round(sum(c["points"] for c in cats), 1)
    m = re.search(r"^JOB FIT SCORE:[ \t]*(\d+(?:\.\d+)?)[ \t]*/[ \t]*100[ \t]*$", text, re.M)
    stated = float(m.group(1)) if m else None
    if stated is None:
        errors.append("header line 'JOB FIT SCORE: <n>/100' missing or malformed")
    elif abs(stated - total) > 0.6:
        errors.append(f"stated JOB FIT SCORE {stated:g} != recomputed {total:g}")
    return {"categories": cats, "total": total, "stated": stated, "errors": errors}


def parse_claims(text: str, errors: list[str] | None = None) -> list[dict]:
    errors = [] if errors is None else errors
    out = []
    for r in _table(_section(text, "Claim audit", errors), CLAIM_HEADER, "Claim audit", errors):
        status = next((s for s in STATUSES if s.lower() == r[2].lower()), None)
        severity = r[3].lower()
        if status is None:
            errors.append(f"Claim audit: unknown status '{r[2]}' (allowed: {', '.join(STATUSES)})")
            continue
        if severity not in SEVERITIES:
            errors.append(f"Claim audit: unknown severity '{r[3]}' (allowed: high, medium, low)")
            continue
        out.append({"claim": r[1], "status": status, "severity": severity, "note": r[4]})
    if not out and not any(e.startswith("Claim audit") or "Claim audit" in e for e in errors):
        errors.append("Claim audit: no claims audited")
    return out


def parse_hard(text: str) -> str:
    m = re.search(r"^HARD REQUIREMENTS:[ \t]*(MET|AT RISK|NOT MET)[ \t]*$", text, re.M)
    return m.group(1) if m else "UNKNOWN"


def writing_issues(text: str, errors: list[str]) -> dict:
    body = _section(text, "Writing issues", errors)
    counts = {s: 0 for s in SEVERITIES}
    if not body or _is_none(body):
        return counts
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^-\s*\[(high|medium|low)\]", s, re.I)
        if m:
            counts[m.group(1).lower()] += 1
        elif s.startswith("-"):
            errors.append(f"Writing issues: item without a [high|medium|low] tag: {s[:70]}")
    return counts


def out_of_scope(text: str, errors: list[str]) -> list[str]:
    body = _section(text, "Out-of-scope edits", errors)
    if not body or _is_none(body):
        return []
    return [l.strip() for l in body.splitlines() if l.strip()]


ROLE_DATE_RE = re.compile(r"\b(19|20)\d\d\b")
LEAD_RE = re.compile(r"^-\s*\*\*(.+?)\*\*")
COMMENT_RE = re.compile(r"<!--.*?-->")


def delta_bullets(cv_text: str, marker: str = "Delta") -> list[str] | None:
    """Bullets under the role heading (`<role> | <marker> ... <year>`); None if not found."""
    lines = cv_text.replace("\r\n", "\n").split("\n")
    start = next((i for i, l in enumerate(lines)
                  if marker in l and "|" in l and ROLE_DATE_RE.search(l) and not l.lstrip().startswith("-")), None)
    if start is None:
        return None
    out = []
    for l in lines[start + 1:]:
        s = l.strip()
        if s.startswith("- "):
            out.append(COMMENT_RE.sub("", s).strip())
        elif s:  # next role heading / section
            break
    return out


def count_delta_bullets(cv_text: str, marker: str = "Delta") -> int:
    b = delta_bullets(cv_text, marker)
    return -1 if b is None else len(b)


def profile_line(cv_text: str) -> str:
    lines = cv_text.replace("\r\n", "\n").split("\n")
    i = next((k for k, l in enumerate(lines) if re.sub(r"[*#]", "", l).strip().upper()
              in ("PROFESSIONAL PROFILE", "PROFILE", "SUMMARY", "PROFESSIONAL SUMMARY")), None)
    return COMMENT_RE.sub("", lines[i + 1]).strip() if i is not None and i + 1 < len(lines) else ""


def _norm_txt(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.replace("**", "").lower()).strip()


# ------------------------------------------------------------------ checks ---

def gate(review: str, cv: str, threshold: float, max_bullets: int) -> dict:
    fmt: list[str] = []
    w = parse_weighted(review, fmt)
    claims = parse_claims(review, fmt)
    wi = writing_issues(review, fmt)
    oos = out_of_scope(review, fmt)
    hard = parse_hard(review)
    if hard == "UNKNOWN":
        fmt.append("header line 'HARD REQUIREMENTS: MET | AT RISK | NOT MET' missing or malformed")
    counts = {s: sum(1 for c in claims if c["status"] == s) for s in STATUSES}
    blocking = [c for c in claims if is_blocking(c["status"], c["severity"])]
    bullets = count_delta_bullets(cv)
    rows_txt = [f" {_norm_txt(c['claim'])} " for c in claims]
    leads = []
    for b in delta_bullets(cv) or []:
        m = LEAD_RE.match(b)
        lead = _norm_txt(m.group(1)) if m else _norm_txt(b)[:40]
        leads.append(" ".join(lead.split()[:6]))    # match whole words, not substrings
    for words in leads:
        if words and not any(f" {words} " in r for r in rows_txt):
            fmt.append(f"Claim audit: Delta bullet not audited: {words[:60]}")
    for r in rows_txt:                               # one row may not cover two bullets
        hit = [w for w in leads if w and f" {w} " in r]
        if len(hit) > 1:
            fmt.append(f"Claim audit: one row covers {len(hit)} bullets ({', '.join(h[:20] for h in hit)}) — audit each bullet separately")

    failures = {  # anything here except evidence → REVISE / Major Revision
        "format": fmt,
        "score": [f"score {w['total']:g} < threshold {threshold:g}"] if w["total"] < threshold else [],
        "scope": ["out-of-scope edits present"] if oos else [],
        "bullets": (["Delta role heading not found in the CV"] if bullets < 0 else
                    [f"{bullets} Delta bullets > max {max_bullets}"] if bullets > max_bullets else []),
        "writing": [f"{wi['high']} high-severity writing issue(s)"] if wi["high"] else [],
        "evidence": [f"{c['status']}/{c['severity']}: {c['claim'][:70]}" for c in blocking],
    }
    non_evidence = [r for k, v in failures.items() if k != "evidence" for r in v]
    derived = "PASS" if not any(failures.values()) else ("NEEDS EVIDENCE" if not non_evidence else "REVISE")
    m = re.search(r"^VERDICT:[ \t]*(PASS|REVISE|NEEDS EVIDENCE)[ \t]*$", review, re.M)
    stated = m.group(1) if m else None
    return {"score": w["total"], "stated_score": w["stated"], "categories": w["categories"],
            "claim_counts": counts, "template_only": counts["Template"],
            "unverifiable": counts["Inferred"] + counts["Missing Evidence"] + counts["Contradicted"],
            "blocking_claims": blocking, "delta_bullets": bullets, "hard_requirements": hard,
            "writing_issues": wi, "failures": failures,
            "reasons": [r for v in failures.values() for r in v],
            "derived_verdict": derived, "stated_verdict": stated,
            "verdict_mismatch": bool(stated and stated != derived)}


NUM_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def ledger(evidence: str, cv: str | None = None) -> dict:
    errs: list[str] = []
    rows = _table(_section(evidence, "Claim ledger", errs), LEDGER_HEADER, "Claim ledger", errs)
    counts = {s: 0 for s in STATUSES}
    retired = {s: 0 for s in STATUSES}   # claims no longer in the CV (kept as history)
    unknown = []
    for r in rows:
        st = next((s for s in STATUSES if s.lower() == r[2].lower()), None)
        if not st:
            unknown.append(r[:3])
        elif RETIRED_RE.search(" ".join(r)):
            retired[st] += 1
        else:
            counts[st] += 1
    live_unverifiable = counts["Inferred"] + counts["Missing Evidence"] + counts["Contradicted"]
    out = {"rows": len(rows), "counts": counts, "retired": retired,
           "live_unverifiable": live_unverifiable, "unknown_status_rows": unknown, "errors": errs}
    if cv is not None:
        # every number in the Profile and the Delta bullets must appear in a live ledger row
        # only the Claim and Measurement-scope cells count — not Source URLs or Notes
        live_text = " ".join(f"{r[1]} {r[4]}" for r in rows
                             if any(r[2].lower() == s.lower() for s in STATUSES) and not RETIRED_RE.search(r[5]))
        live_nums = set(NUM_RE.findall(live_text))
        checked = [profile_line(cv)] + (delta_bullets(cv) or [])
        missing = sorted({n for line in checked for n in NUM_RE.findall(line.replace("**", ""))} - live_nums,
                         key=lambda x: float(x))
        out["cv_numbers_missing_from_ledger"] = missing
        if missing:
            errs.append(f"CV numbers with no live ledger row: {missing}")
    return out


def compare(before: str, after: str) -> dict:
    b, a = parse_weighted(before), parse_weighted(after)
    bc = {c["category"]: c["points"] for c in b["categories"]}
    rows = [{"category": c["category"], "before": bc.get(c["category"]), "after": c["points"],
             "delta": None if bc.get(c["category"]) is None else round(c["points"] - bc[c["category"]], 1)}
            for c in a["categories"]]
    return {"before": b["total"], "after": a["total"], "delta": round(a["total"] - b["total"], 1),
            "categories": rows, "errors": b["errors"] + a["errors"]}


def recommend(review: str, cv: str, layout: dict, threshold: float, max_bullets: int) -> dict:
    g = gate(review, cv, threshold, max_bullets)
    f = g["failures"]
    lay = layout.get("status", "UNVERIFIED")
    major = (f["format"] or f["score"] or f["scope"] or f["bullets"] or f["writing"]
             or g["hard_requirements"] == "NOT MET" or lay == "FAIL")
    if major:
        rec = "Major Revision Required"
    elif f["evidence"] or g["hard_requirements"] in ("AT RISK", "UNKNOWN") or lay != "PASS":
        rec = "Needs Evidence"
    else:
        rec = "Ready to Apply"
    return {**g, "layout_status": lay, "recommendation": rec}


# -------------------------------------------------------------------- main ---

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate"); g.add_argument("--review", required=True); g.add_argument("--cv", required=True)
    g.add_argument("--threshold", type=float, default=85); g.add_argument("--max-bullets", type=int, default=5)
    l = sub.add_parser("ledger"); l.add_argument("--evidence", required=True); l.add_argument("--cv")
    c = sub.add_parser("compare"); c.add_argument("--before", required=True); c.add_argument("--after", required=True)
    r = sub.add_parser("recommend"); r.add_argument("--review", required=True); r.add_argument("--cv", required=True)
    r.add_argument("--layout", required=True, help="JSON written by build_cv.py check --json")
    r.add_argument("--threshold", type=float, default=85); r.add_argument("--max-bullets", type=int, default=5)
    a = ap.parse_args(argv)
    rd = lambda p: Path(p).read_text(encoding="utf-8-sig")
    if a.cmd == "gate":
        out = gate(rd(a.review), rd(a.cv), a.threshold, a.max_bullets)
    elif a.cmd == "ledger":
        out = ledger(rd(a.evidence), rd(a.cv) if a.cv else None)
    elif a.cmd == "compare":
        out = compare(rd(a.before), rd(a.after))
    else:
        out = recommend(rd(a.review), rd(a.cv), json.loads(rd(a.layout)), a.threshold, a.max_bullets)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.cmd == "recommend":
        return 0 if out["recommendation"] == "Ready to Apply" else 1
    if a.cmd == "gate":
        return 0 if out["derived_verdict"] == "PASS" else 1
    if a.cmd == "ledger":
        return 1 if out["errors"] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
