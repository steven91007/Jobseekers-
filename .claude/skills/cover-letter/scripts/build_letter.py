"""Fill the cover-letter .docx template with per-job slots and export a PDF.

Runs under the project's Windows venv (python-docx, docx2pdf, PyMuPDF). From WSL call it
through build_letter.sh, which converts POSIX paths to Windows paths.

    build_letter.py build --template T.docx --slots slots.json --out Letter.docx [--pdf]
    build_letter.py check --pdf Letter.pdf [--json out.json]

slots.json:
    {"title": "Senior AI Engineer",                       # JD title, (m/w/d) stripped
     "company_line": "Mapbox's work on ... fascinates me, ...",   # replaces the paragraph-3 opening
     "country": "Germany",                               # replaces the country in the move sentence
     "experience": "As you will see from my CV, I have six years ..."}   # replaces paragraph 2

Only these four slots change; every other word, run and format of the template stays as is.
"""
import argparse
import json
import re
import sys
from pathlib import Path

TEMPLATE_TITLE = "Agentic AI Engineer"
P3_OPENING = ("Your international orientation and diverse team fascinate me, since I have always "
              "been interested in working in diverse environments.")
P2_START = "As you will see from my CV,"
MOVE_RE = re.compile(r"(plan to move to )([A-Z][A-Za-z ]+?)( on )")
VISA_LINE = "I do not need visa sponsorship."
# candidate's visa position per destination (CONF-i3); other countries must be confirmed first
VISA = {"Germany": VISA_LINE, "the Netherlands": VISA_LINE, "Netherlands": VISA_LINE, "Ireland": VISA_LINE,
        "the United Kingdom": "I will need visa sponsorship.", "United Kingdom": "I will need visa sponsorship."}
HEADER_GAP = 30  # spaces between title and e-mail in the template header line


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def build(template: Path, slots: dict, out: Path) -> list[str]:
    from docx import Document

    title, line, country = slots["title"].strip(), slots["company_line"].strip(), slots["country"].strip()
    doc = Document(str(template))
    if country not in VISA:
        raise SystemExit(f"no confirmed visa position for {country!r} — ask the candidate (CONF-i3)")
    exp = slots["experience"].strip()
    if not exp.startswith(P2_START):
        raise SystemExit(f"slot experience must start with {P2_START!r}")
    done = {"header": 0, "title": 0, "opening": 0, "country": 0, "experience": 0, "visa": 0}
    for p in doc.paragraphs:
        runs = p.runs
        if runs and runs[0].text == TEMPLATE_TITLE and len(runs) > 1 and "@" in runs[1].text:
            # header: keep the e-mail roughly where it was by trading spaces for title length
            gap = max(6, HEADER_GAP - (len(title) - len(TEMPLATE_TITLE)))
            runs[0].text = title
            runs[1].text = " " * gap + runs[1].text.strip()
            done["header"] += 1
            continue
        for r in runs:
            t = r.text
            if t.startswith(P2_START):
                r.text = exp + " "
                done["experience"] += 1
                continue
            if f"As an {TEMPLATE_TITLE}" in t:
                t = t.replace(f"As an {TEMPLATE_TITLE}", f"As {_article(title)} {title}")
                done["title"] += 1
            if f"dedicated {TEMPLATE_TITLE}" in t:
                t = t.replace(f"dedicated {TEMPLATE_TITLE}", f"dedicated {title}")
                done["title"] += 1
            if P3_OPENING in t:
                t = t.replace(P3_OPENING, line)
                done["opening"] += 1
            if MOVE_RE.search(t):
                t = MOVE_RE.sub(lambda m: m.group(1) + country + m.group(3), t, count=1)
                done["country"] += 1
            if VISA_LINE in t:
                t = t.replace(VISA_LINE, VISA[country])
                done["visa"] += 1
            r.text = t
    expected = {"header": 1, "title": 2, "opening": 1, "country": 1, "experience": 1, "visa": 1}
    problems = [f"slot {k}: replaced {done[k]}×, expected {v}×" for k, v in expected.items() if done[k] != v]
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    print(f"letter -> {out}")
    return problems


def to_pdf(docx_path: Path) -> Path:
    from docx2pdf import convert  # Word COM automation

    pdf_path = docx_path.with_suffix(".pdf")
    convert(str(docx_path), str(pdf_path))
    print(f"pdf    -> {pdf_path} (Word)")
    return pdf_path


def check(pdf: Path) -> dict:
    import pymupdf

    d = pymupdf.open(str(pdf))
    page = d[0]
    r = page.rect
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(s["text"] for s in l["spans"]).strip()
            if txt.strip("​"):
                lines.append((l["bbox"], txt))
    res = []
    res.append(("page_count", d.page_count == 1, f"{d.page_count} page(s), expected 1"))
    clip = [t for (x0, y0, x1, y1), t in lines if x1 > r.width - 36 or y1 > r.height - 36]
    res.append(("clipping_margins", not clip, clip or "all text inside margins"))
    # the header line starts at the left margin with the title and ends with the e-mail
    mail = [(bb, t) for bb, t in lines if "@" in t]
    head = bool(mail) and mail[0][0][0] < 100 and not mail[0][1].startswith(mail[0][1].split()[-1])
    res.append(("header_one_line", head, "title and e-mail on the same line" if head else "e-mail wrapped or missing"))
    out = {"status": "PASS" if all(ok for _, ok, _ in res) else "FAIL",
           "checks": [{"check": n, "status": "PASS" if ok else "FAIL", "detail": det} for n, ok, det in res]}
    for c in out["checks"]:
        print(f"[{c['status']}] {c['check']}: {c['detail']}")
    print(f"LAYOUT: {out['status']}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--template", required=True, type=Path)
    b.add_argument("--slots", required=True, type=Path)
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--pdf", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("--pdf", required=True, type=Path)
    c.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "build":
        slots = json.loads(args.slots.read_text(encoding="utf-8"))
        problems = build(args.template, slots, args.out)
        for p in problems:
            print(f"WARN {p}", file=sys.stderr)
        if args.pdf:
            to_pdf(args.out)
        return 1 if problems else 0
    out = check(args.pdf)
    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if out["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
