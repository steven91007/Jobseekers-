"""Convert between the CV Markdown subset and .docx / .pdf.

Runs under the project's Windows venv (python-docx, docx2pdf). From WSL call it
through build_cv.sh, which converts POSIX paths to Windows paths.

    build_cv.py extract --docx CV_template.docx --out CV_template.md
    build_cv.py build   --md 03_cv.md [--template CV_template.docx] --out CV.docx [--pdf]
    build_cv.py patch   --template-docx T.docx --template-md T.md --md 03_cv.md --out CV.docx [--pdf]
    build_cv.py check   --pdf CV.pdf [--docx CV.docx] [--md 03_cv.md] [--pages 1] [--json out.json]

Markdown subset (see references/writing_rules.md):
    # H1            name (title block)
    lines after H1  contact block, until the first blank line
    ## H2 / ### H3  headings
    - / * bullets   List Bullet
    plain text      Normal
    **bold** *it*   inline runs
    ---             ignored
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt

INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")
COMMENT_RE = re.compile(r"<!--.*?-->")  # R# traceability notes from the writer


# ---------------------------------------------------------------- extract ---

def _md_text(paragraph) -> str:
    """Return paragraph text with adjacent runs grouped by effective bold state."""
    out: list[str] = []
    for r in paragraph.runs:
        if not r.text:
            continue
        bold = bool(r.bold) or (r.bold is None and bool(r.style and r.style.font.bold))
        if out and out[-1][0] == bold:
            out[-1] = (bold, out[-1][1] + r.text)
        else:
            out.append((bold, r.text))
    parts = []
    for bold, t in out:
        if bold and t.strip():
            lead, body, trail = t[: len(t) - len(t.lstrip())], t.strip(), t[len(t.rstrip()):]
            parts.append(f"{lead}**{body}**{trail}")
        else:
            parts.append(t)
    return "".join(parts).strip()

def extract(docx_path: Path, out_path: Path) -> None:
    """Dump a .docx to the Markdown subset, so the writer can see the skeleton."""
    doc = Document(str(docx_path))
    lines: list[str] = []
    for p in doc.paragraphs:
        text = _md_text(p)
        if not text:
            lines.append("")
            continue
        style = (p.style.name or "").lower()
        if style.startswith("title") or style == "heading 1":
            lines.append(f"# {text}")
        elif style == "heading 2":
            lines.append(f"## {text}")
        elif style == "heading 3":
            lines.append(f"### {text}")
        elif "list" in style or text.startswith(("•", "-", "–")):
            lines.append(f"- {text.lstrip('•-– ').strip()}")
        else:
            lines.append(text)
    # Append table cell paragraphs, one per line, with bold markers (so `patch` can
    # rewrite them in place, e.g. the Skills table). Merged cells are emitted once.
    for p in _table_paragraphs(doc):
        if p is None:
            lines.append("")
            continue
        text = _md_text(p)
        if text:
            lines.append(text)
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(f"extracted -> {out_path}")


# ------------------------------------------------------------------ build ---

def _add_runs(paragraph, text: str) -> None:
    for chunk in INLINE_RE.split(text):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            paragraph.add_run(chunk[2:-2]).bold = True
        elif chunk.startswith("*") and chunk.endswith("*"):
            paragraph.add_run(chunk[1:-1]).italic = True
        else:
            paragraph.add_run(chunk)


def _style_or(doc, name: str, fallback: str = "Normal") -> str:
    names = {s.name for s in doc.styles}
    return name if name in names else fallback


def _clear_body(doc) -> None:
    body = doc.element.body
    for child in list(body):
        if child.tag.endswith("sectPr"):  # keep page setup / margins
            continue
        body.remove(child)


def build(md_path: Path, out_path: Path, template: Path | None) -> None:
    if template and template.exists():
        doc = Document(str(template))
        _clear_body(doc)
    else:
        doc = Document()
        doc.styles["Normal"].font.size = Pt(10.5)

    title_style = _style_or(doc, "Title", _style_or(doc, "Heading 1"))
    h2 = _style_or(doc, "Heading 2")
    h3 = _style_or(doc, "Heading 3")
    bullet = _style_or(doc, "List Bullet")

    in_contact = False
    for raw in md_path.read_text(encoding="utf-8").splitlines():
        stripped = COMMENT_RE.sub("", raw).strip()
        if stripped == "---":
            continue
        if not stripped:
            in_contact = False
            continue
        if stripped.startswith("# "):
            doc.add_paragraph(stripped[2:].strip(), style=title_style)
            in_contact = True
        elif stripped.startswith("## "):
            doc.add_paragraph(stripped[3:].strip(), style=h2)
        elif stripped.startswith("### "):
            doc.add_paragraph(stripped[4:].strip(), style=h3)
        elif stripped.startswith(("- ", "* ")):
            _add_runs(doc.add_paragraph(style=bullet), stripped[2:].strip())
        else:
            p = doc.add_paragraph()
            _add_runs(p, stripped)
            if in_contact:
                p.paragraph_format.space_after = Pt(0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    print(f"docx -> {out_path}")


def to_pdf(docx_path: Path) -> Path:
    pdf_path = docx_path.with_suffix(".pdf")
    if os.name == "nt":
        try:
            from docx2pdf import convert  # Word COM automation

            convert(str(docx_path), str(pdf_path))
            print(f"pdf  -> {pdf_path} (Word)")
            return pdf_path
        except Exception as exc:  # noqa: BLE001 - fall through to LibreOffice
            print(f"docx2pdf failed ({exc}); trying LibreOffice", file=sys.stderr)
    soffice = shutil.which("soffice") or shutil.which("soffice.exe") or (
        r"C:\Program Files\LibreOffice\program\soffice.exe"
    )
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir",
         str(docx_path.parent), str(docx_path)],
        check=True, timeout=180,
    )
    print(f"pdf  -> {pdf_path} (LibreOffice)")
    return pdf_path


# ------------------------------------------------------------------ patch ---

def _norm(s: str) -> str:
    s = s.replace("**", "").replace("\t", " ")
    return re.sub(r"\s+", " ", s).strip().lstrip("•-– ").strip()


BULLET_PREFIX_RE = re.compile(r"^[•\-–]\s*")


def _set_text(paragraph, text: str) -> None:
    """Replace a paragraph's text from a Markdown line, keeping the template's run
    formatting: the literal bullet prefix ("•  ") keeps its run, `**bold**` chunks
    reuse the paragraph's bold run format, the rest reuses its regular run format."""
    import copy

    runs = [r for r in paragraph.runs if r.text]
    text = BULLET_PREFIX_RE.sub("", text.strip())
    if not runs:
        _add_runs(paragraph, text)
        return

    m = BULLET_PREFIX_RE.match(paragraph.text)
    prefix = m.group(0) if m else ""
    bold_tpl = next((r for r in runs if r.bold), None)
    plain_tpl = next((r for r in runs if not r.bold and not BULLET_PREFIX_RE.fullmatch(r.text)), None)
    plain_tpl = plain_tpl or runs[-1]

    def clone(tpl, t: str):
        el = copy.deepcopy(tpl._element)
        for child in list(el):
            if child.tag.endswith("}t") or child.tag.endswith("}tab") or child.tag.endswith("}br"):
                el.remove(child)
        paragraph._element.append(el)
        from docx.text.run import Run
        r = Run(el, paragraph)
        r.text = t
        return r

    for r in paragraph.runs:  # drop old runs, then rebuild
        r._element.getparent().remove(r._element)
    if prefix:
        clone(runs[0], prefix)
    for chunk in INLINE_RE.split(text):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            r = clone(bold_tpl or plain_tpl, chunk[2:-2])
            r.bold = True
        else:
            r = clone(plain_tpl, chunk.lstrip("*").rstrip("*") if chunk.startswith("*") else chunk)
            if bold_tpl is not None and plain_tpl is bold_tpl:
                r.bold = False


def _table_paragraphs(doc):
    """Paragraphs inside tables in document order; `None` marks a table boundary."""
    for table in doc.tables:
        yield None
        seen = []  # keep the elements alive: id() of freed lxml proxies gets reused
        for row in table.rows:
            for cell in row.cells:
                if any(cell._tc is tc for tc in seen):  # merged cell repeated across the row
                    continue
                seen.append(cell._tc)
                yield from cell.paragraphs


def _all_paragraphs(doc):
    """Body paragraphs followed by table-cell paragraphs (same order as `extract`)."""
    yield from doc.paragraphs
    yield from (p for p in _table_paragraphs(doc) if p is not None)


def _find_para(doc, norm_text: str):
    for p in _all_paragraphs(doc):
        if _norm(p.text) == norm_text:
            return p
    return None


def patch(template_docx: Path, template_md: Path, cv_md: Path, out_path: Path) -> None:
    """Apply the md-level changes (template_md -> cv_md) onto the original .docx.

    Leaves untouched paragraphs, tables and styles in place. Matches changed lines
    to body paragraphs by normalized text. Text edits can change pagination.
    """
    import copy
    import difflib

    def lines(p: Path) -> list[str]:
        return [COMMENT_RE.sub("", l).strip() for l in p.read_text(encoding="utf-8").splitlines()]

    old_raw, new_raw = lines(template_md), lines(cv_md)
    # match on normalised text (no **, bullets, tabs), but write the raw Markdown
    # line so `**bold**` chunks reach _set_text
    old, new = [_norm(l) for l in old_raw], [_norm(l) for l in new_raw]
    doc = Document(str(template_docx))
    paras = list(_all_paragraphs(doc))  # body + table-cell paragraphs (e.g. Skills rows)
    by_text: dict[str, list] = {}
    for p in paras:
        by_text.setdefault(_norm(p.text), []).append(p)

    replaced = inserted = deleted = 0
    missing: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        olds = [l for l in old[i1:i2] if l]
        news = [l for l in new_raw[j1:j2] if _norm(l)]
        # pairwise replace, then insert extras after the last touched paragraph
        # (or, for a pure insert, after the paragraph matching the previous md line),
        # or delete surplus old paragraphs
        anchor = None
        if not olds and news:
            prev = next((l for l in reversed(new[:j1]) if l), None)
            anchor = _find_para(doc, prev) if prev else None
        for k, o in enumerate(olds):
            cands = by_text.get(o)
            if not cands:
                missing.append(o)
                continue
            p = cands.pop(0)
            if k < len(news):
                _set_text(p, news[k]); replaced += 1; anchor = p
            else:
                p._element.getparent().remove(p._element); deleted += 1
        for n in news[len(olds):]:
            if anchor is None:
                missing.append(f"(no anchor for insert) {n}")
                continue
            # a bullet inserted after a non-bullet line (e.g. the role heading) takes its
            # formatting from the next bullet paragraph, not from the anchor
            src = anchor
            if BULLET_PREFIX_RE.match(n.strip()) and not BULLET_PREFIX_RE.match(anchor.text):
                pos = next((i for i, p in enumerate(paras) if p._element is anchor._element), None)
                after = paras[pos + 1:] if pos is not None else []
                src = next((p for p in after if BULLET_PREFIX_RE.match(p.text)), anchor)
            el = copy.deepcopy(src._element)
            anchor._element.addnext(el)
            from docx.text.paragraph import Paragraph
            anchor = Paragraph(el, anchor._parent)
            _set_text(anchor, n); inserted += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    print(f"patched -> {out_path}  (replaced {replaced}, inserted {inserted}, deleted {deleted})")
    if missing:
        print("WARNING: could not locate these template lines in the docx (left unchanged):", file=sys.stderr)
        for m in missing:
            print("  - " + m[:120], file=sys.stderr)


# ------------------------------------------------------------------- main ---

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="docx -> markdown skeleton")
    ex.add_argument("--docx", required=True, type=Path)
    ex.add_argument("--out", required=True, type=Path)

    bd = sub.add_parser("build", help="markdown -> docx [-> pdf]")
    bd.add_argument("--md", required=True, type=Path)
    bd.add_argument("--out", required=True, type=Path, help="output .docx path")
    bd.add_argument("--template", type=Path, help="base .docx whose styles/page setup are reused")
    bd.add_argument("--pdf", action="store_true")

    pt = sub.add_parser("patch", help="apply Markdown changes to body paragraphs in the original docx")
    pt.add_argument("--template-docx", required=True, type=Path)
    pt.add_argument("--template-md", required=True, type=Path)
    pt.add_argument("--md", required=True, type=Path, help="tailored 03_cv.md")
    pt.add_argument("--out", required=True, type=Path, help="output .docx path")
    pt.add_argument("--pdf", action="store_true")

    ck = sub.add_parser("check", help="render-level layout QC of a built PDF (PyMuPDF)")
    ck.add_argument("--pdf", required=True, type=Path)
    ck.add_argument("--docx", type=Path, help="built .docx (for margins)")
    ck.add_argument("--md", type=Path, help="03_cv.md (for section reading order)")
    ck.add_argument("--pages", type=int, default=1, help="expected page count")
    ck.add_argument("--role", default="Delta", help="text identifying the tailored role line")
    ck.add_argument("--json", type=Path, help="write the result as JSON")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import layout_check
        return layout_check.report(
            layout_check.check(args.pdf, args.docx, args.md, args.pages, args.role), args.json)
    if args.cmd == "extract":
        extract(args.docx, args.out)
    elif args.cmd == "patch":
        patch(args.template_docx, args.template_md, args.md, args.out)
        if args.pdf:
            to_pdf(args.out)
    else:
        build(args.md, args.out, args.template)
        if args.pdf:
            to_pdf(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
