"""Render-level quality control for a built CV PDF (PyMuPDF; Windows venv).

Called through `build_cv.py check`. Every check reads the rendered PDF, so a PASS
means the layout was actually inspected — never report a layout as validated
without running this.

Status per check: PASS / WARN / FAIL. Overall status = worst check.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

A4 = (595.3, 841.9)
BODY_PT, HEADING_PT = 10.0, 11.0
TITLE_MIN_PT = 14.0          # the name line may be larger
UTIL_RANGE = (0.85, 0.98)    # target ≈ 90% page-height utilisation
HEADING_RE = re.compile(r"^\*\*([A-Z][A-Z &/]+)\*\*$|^([A-Z][A-Z &/]{3,})(?:\s|$)")


def _lines(page):
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                s["text"] = "".join(c["c"] for c in s["chars"])
            spans = [s for s in l["spans"] if s["text"].strip()]
            if not spans:
                continue
            # right edge = last visible glyph; Word's trailing space may sit past the margin
            ink = [c["bbox"][2] for s in spans for c in s["chars"] if c["c"].strip()]
            x0, y0, _, y1 = l["bbox"]
            out.append({"text": "".join(s["text"] for s in l["spans"]).strip(),
                        "bbox": (x0, y0, max(ink), y1), "spans": spans})
    return out


def _md_headings(md_text: str) -> list[str]:
    heads = []
    for raw in md_text.replace("\r\n", "\n").split("\n"):
        s = re.sub(r"^#{1,6}\s+", "", re.sub(r"<!--.*?-->", "", raw).strip())   # "## Section" → "Section"
        m = re.match(r"^\*\*([A-Z][A-Z &/]+)\*\*$", s) or re.match(r"^([A-Z][A-Z &/]{3,}?)(?:\s{2,}|\s\||$)", s)
        if m and len(m.group(1).split()) <= 4:
            heads.append(m.group(1).strip())
        for extra in re.findall(r"\|\s*([A-Z][A-Z &/]{3,})$", s):   # "... | CERTIFICATIONS"
            heads.append(extra.strip())
    return heads


def check(pdf: Path, docx: Path | None = None, md: Path | None = None,
          expected_pages: int = 1, role_marker: str = "Delta") -> dict:
    import pymupdf  # imported lazily so build/patch work without it

    doc = pymupdf.open(str(pdf))
    checks: list[dict] = []
    add = lambda name, status, detail: checks.append({"check": name, "status": status, "detail": detail})

    # margins from the docx when available
    top = bottom = 33.0
    left = right = 36.0
    if docx:
        from docx import Document
        sec = Document(str(docx)).sections[0]
        top, bottom, left, right = (sec.top_margin.pt, sec.bottom_margin.pt,
                                    sec.left_margin.pt, sec.right_margin.pt)

    add("page_count", "PASS" if len(doc) == expected_pages else "FAIL",
        f"{len(doc)} page(s), expected {expected_pages}")
    page = doc[0]
    w, h = page.rect.width, page.rect.height
    add("page_size", "PASS" if abs(w - A4[0]) < 3 and abs(h - A4[1]) < 3 else "FAIL",
        f"{w:.1f}×{h:.1f} pt (A4 = 595×842)")

    lines = [l for p in doc for l in _lines(p)]
    sizes: dict[float, int] = {}
    odd = []
    for l in lines:
        for s in l["spans"]:
            sz = round(s["size"], 1)
            sizes[sz] = sizes.get(sz, 0) + len(s["text"])
            if sz not in (BODY_PT, HEADING_PT) and sz < TITLE_MIN_PT:
                odd.append(f"{sz}pt: {s['text'][:40]!r}")
    add("font_sizes", "PASS" if not odd else ("FAIL" if any(float(o.split('pt')[0]) < 9.5 for o in odd) else "WARN"),
        {"chars_by_size": dict(sorted(sizes.items())), "unexpected": odd[:10]})

    # clipping / margins (all pages)
    clipped = []
    for pi, p in enumerate(doc):
        pw, ph = p.rect.width, p.rect.height
        for l in _lines(p):
            x0, y0, x1, y1 = l["bbox"]
            # +4 on the right: Word hangs trailing punctuation a few points past the margin
            if x0 < left - 2 or x1 > pw - right + 4 or y1 > ph - bottom + 2 or y0 < top - 8:
                clipped.append(f"p{pi+1} {l['text'][:50]!r} bbox={[round(v) for v in l['bbox']]}")
    add("clipping_margins", "PASS" if not clipped else "FAIL", clipped[:10] or "all text inside margins")

    # page-height utilisation of the last page
    last = _lines(doc[-1])
    max_y = max((l["bbox"][3] for l in last), default=top)
    usable = h - top - bottom
    util = (max_y - top) / usable if usable else 0
    add("page_utilization", "PASS" if UTIL_RANGE[0] <= util <= UTIL_RANGE[1] else "WARN",
        f"{util:.0%} of usable height (target {UTIL_RANGE[0]:.0%}–{UTIL_RANGE[1]:.0%})")

    # reading order of section headings
    page_text = "\n".join(p.get_text() for p in doc)
    if md:
        heads = _md_headings(md.read_text(encoding="utf-8-sig"))
        pos, order_ok, missing = -1, True, []
        for hd in heads:
            i = page_text.find(hd)
            if i < 0:
                missing.append(hd)
            elif i < pos:
                order_ok = False
            else:
                pos = i
        add("reading_order", "PASS" if order_ok and not missing else "FAIL",
            {"headings": heads, "missing_in_text": missing, "in_order": order_ok})

    # profile line count + bullet geometry for the target role
    texts = [l["text"] for l in lines]
    def idx(pred, start=0):
        return next((i for i in range(start, len(texts)) if pred(texts[i])), None)
    ps = idx(lambda t: t.upper().startswith(("PROFESSIONAL PROFILE", "PROFILE", "SUMMARY", "PROFESSIONAL SUMMARY")))
    if ps is None:
        add("profile_lines", "FAIL", "Profile/Summary heading not found in the rendered text")
    else:
        pe = idx(lambda t: t.isupper() and len(t) > 4, ps + 1)
        n = (pe or ps + 1) - ps - 1
        add("profile_lines", "PASS" if 3 <= n <= 4 else "WARN", f"{n} rendered line(s) (target ≈ 4)")
    rs = idx(lambda t: role_marker in t and "|" in t)   # the role heading, not a Profile mention
    if rs is None:
        add("role_bullets", "FAIL", f"role heading containing {role_marker!r} not found in the rendered text")
    else:
        # Delta block = scope line (italic, 1 line) + project cards (bold title + " — " context,
        # ≤ 2 lines) each followed by 2–3 bullets (≤ 2 lines); 5–6 bullets in total.
        bullets, cards, cur = [], [], None
        date_only = re.compile(r"^[A-Z][a-z]{2} (19|20)\d\d\s*[–-]\s*(Present|[A-Z][a-z]{2} (19|20)\d\d)$")
        for i in range(rs + 1, len(lines)):
            t = texts[i]
            font = " ".join(s["font"] for s in lines[i]["spans"][:2])   # card marker may be its own (non-bold) span
            if date_only.match(t):          # right-aligned date of the role heading
                continue
            if t.isupper() and len(t) > 4:  # next section heading
                break
            if "|" in t and "Bold" in font and "—" not in t:   # bold "Role  |  Company" = next block
                break
            if t.startswith("•"):
                cur = {"lead": t[1:45].strip(), "lines": 1, "x_bullet": round(lines[i]["bbox"][0], 1), "x_cont": []}
                bullets.append(cur)
            elif "Bold" in font and "—" in t:   # card title line
                cur = {"title": t.split("—")[0].strip()[:45], "lines": 1}
                cards.append(cur)
            elif cur is None:                   # scope line right after the role heading
                cur = {"title": "(scope)", "lines": 1}
                cards.append(cur)
            else:                               # continuation line
                cur["lines"] += 1
                if "x_cont" in cur:
                    cur["x_cont"].append(round(lines[i]["bbox"][0], 1))
        xb = {b["x_bullet"] for b in bullets}
        xc = {x for b in bullets for x in b["x_cont"]}
        over = [b for b in bullets if b["lines"] > 2] + [c for c in cards if c["lines"] > 2]
        n_cards = len([c for c in cards if c["title"] != "(scope)"])
        add("role_bullets",
            "FAIL" if not bullets or over or not 5 <= len(bullets) <= 6 or n_cards not in (0, 2, 3)
            else "PASS" if len(xb) <= 1 and len(xc) <= 1 else "WARN",
            {"bullets": len(bullets), "lines_per_bullet": [b["lines"] for b in bullets],
             "cards": [(c["title"], c["lines"]) for c in cards],
             "bullet_x": sorted(xb), "continuation_x": sorted(xc)})

    # each "Label: item · item" Skills row on one rendered line
    ss = idx(lambda t: t.upper().startswith(("TECHNICAL SKILLS", "SKILLS")))
    if ss is not None:
        rows, wrapped = 0, []
        for t in texts[ss + 1:]:
            if t.isupper() and len(t) > 4:
                break
            if re.match(r"^[A-Z][^:·]{1,40}:\s", t):
                rows += 1
            else:
                wrapped.append(t[:40])
        if rows:
            add("skills_rows", "PASS" if not wrapped else "FAIL",
                {"rows": rows, "wrapped_continuations": wrapped})

    # right-aligned dates on role lines (column alignment)
    date_x = [round(l["bbox"][2]) for l in lines
              if l["bbox"][0] > w / 2 and re.search(r"(19|20)\d\d\s*[–-]\s*(Present|[A-Z][a-z]{2} (19|20)\d\d)$", l["text"])]
    if date_x:
        add("date_alignment", "PASS" if max(date_x) - min(date_x) <= 3 else "WARN", f"right edges {date_x}")

    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    status = max((c["status"] for c in checks), key=order.get, default="FAIL")
    return {"pdf": str(pdf), "status": status, "checks": checks}


def report(result: dict, json_out: Path | None = None) -> int:
    import sys
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to a legacy code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for c in result["checks"]:
        print(f"[{c['status']:4}] {c['check']}: {json.dumps(c['detail'], ensure_ascii=False) if not isinstance(c['detail'], str) else c['detail']}")
    print(f"LAYOUT: {result['status']}")
    if json_out:
        json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["status"] != "FAIL" else 1
