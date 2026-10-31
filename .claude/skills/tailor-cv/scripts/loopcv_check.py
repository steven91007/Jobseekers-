"""Second-opinion keyword check on LoopCV's free Resume Keywords Checker, driven with Playwright.

    build_cv.py loopcv --dir <abs $D>          (Windows venv; Chromium via `playwright install chromium`)

The page (https://www.loopcv.pro/tools/resume-keywords-checker/) is pure client-side JavaScript:
no upload, no account, nothing leaves the browser. We paste the raw JD (the "## Raw job
description" part of jd.md) and the text a person would copy out of the built PDF, click
"Analyze Keywords" and read the result. Its algorithm (from the page bundle): JD 1/2/3-grams
minus a stop list, substring presence in the resume text, score = found / (found + missing),
lists capped at 30 found / 20 missing — crude, so use it as a sanity check, not a verdict.
Output: $D/08_loopcv.json and $D/08_loopcv.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

URL = "https://www.loopcv.pro/tools/resume-keywords-checker/"


def jd_text(app_dir: Path) -> str:
    md = (app_dir / "jd.md").read_text(encoding="utf-8-sig")
    i = md.find("## Raw job description")
    body = md[i + len("## Raw job description"):] if i >= 0 else md
    return re.sub(r"^#+\s*", "", body, flags=re.M).strip()


def pdf_text(app_dir: Path) -> str:
    import pymupdf
    pdf = sorted(app_dir.glob("CV_*.pdf"))[0]
    return "\n".join(p.get_text() for p in pymupdf.open(str(pdf)))


def run(app_dir: Path, headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright

    jd, cv = jd_text(app_dir), pdf_text(app_dir)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        page.fill("#jobDesc", jd)
        page.fill("#resume", cv)
        page.click("button.btn-primary:not([disabled])")
        page.wait_for_selector(".score-card", timeout=30_000)
        score_text = page.inner_text(".score-card")
        found = page.locator(".keyword-tag--found").all_inner_texts()
        missing = page.locator(".keyword-tag--missing").all_inner_texts()
        browser.close()
    m = re.search(r"(\d{1,3})\s*%", score_text)
    result = {
        "tool": "loopcv.pro resume-keywords-checker (client-side n-gram match, via Playwright)",
        "score_percent": int(m.group(1)) if m else None,
        "score_card_text": " ".join(score_text.split()),
        "found": [f.strip() for f in found],
        "missing": [f.strip() for f in missing],
    }
    (app_dir / "08_loopcv.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# LoopCV keyword check (second opinion)", "",
          f"**Score {result['score_percent']}%** — {result['score_card_text']}", "",
          f"Found ({len(result['found'])}, capped at 30): " + " · ".join(result["found"]), "",
          f"Missing ({len(result['missing'])}, capped at 20): " + " · ".join(result["missing"]), "",
          "Note: n-gram substring matching with a stop list; many 'missing' items are JD phrasing "
          "fragments, not skills. Treat only real tools/skills in the missing list as signal."]
    (app_dir / "08_loopcv.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(md))
    return result


if __name__ == "__main__":
    run(Path(sys.argv[1]))
