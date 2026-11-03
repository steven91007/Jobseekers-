"""Step 3 of /tailor-cv: assemble 03_cv.md from the v3 template + library picks.

    python3 assemble_cv.py --dir <abs $D> --headline "<JD TITLE>  |  <AREA>  •  <AREA>" \
        --country Germany --picks "scope=…;card=…;bullet=…;skill_row=…" [--summary "<line 5>"] [--force]
    python3 assemble_cv.py --jd <Co> [--dir <abs $D>] [--force]

Copies cv/template/CV_template_v3.md (CRLF), sets the headline (L2) and the contact line (L3,
"Germany | phone | …"; --country is recorded in the DB only), replaces the Skills rows and the Delta block with
`library_db.py render --picks … --part all` (verbatim), and keeps the template Summary unless
--summary is given. Everything else in the template stays byte-identical, as the skill requires.

--jd reads picks, headline, country and dir from the DB (`library_db.py record`) and keeps the
current Summary line of <dir>/03_cv.md if there is one. Before writing, `library_db.py fit` checks
every rendered line against the template's measured limits; a line over the limit stops the write
(exit 1) unless --force.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
TPL = ROOT / "cv" / "template" / "CV_template_v3.md"
# The CV contact line (L3: location | phone | email | links) is private: it lives in the gitignored profile, never in
# this public repo. One line, exactly as it should appear on the CV (hyperlinks as in build_cv.py LINK_RE).
CONTACT_FILE = ROOT / "cv" / "profile" / "contact_line.txt"


def contact_line() -> str:
    try:
        line = CONTACT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        sys.exit(f"missing {CONTACT_FILE}: put the CV contact line there (gitignored), e.g. "
                 "'Germany  |  +49 …  |  name@example.com  |  [LinkedIn](https://…)'")
    if not line or "\n" in line:
        sys.exit(f"{CONTACT_FILE} must hold exactly one non-empty line")
    return line


def render(picks: str) -> tuple[list[str], list[str]]:
    out = subprocess.run([sys.executable, str(HERE / "library_db.py"), "render", "--picks", picks, "--part", "all"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    skills = [l for l in out if l.startswith("**") and ":**" in l]   # card titles have "** — ", never ":**"
    delta = [l for l in out if l not in skills and l.strip()]
    return skills, delta


def assemble(app_dir: Path, headline: str, country: str, picks: str, summary: str | None) -> Path:
    lines = TPL.read_bytes().decode("utf-8").split("\r\n")
    lines[1] = f"**{headline}**"
    lines[2] = contact_line()  # candidate is based in Germany; country is kept in the DB, not on the CV
    if summary:
        lines[4] = summary
    skills, delta = render(picks)
    # Skills rows: the contiguous **Label:** block after TECHNICAL SKILLS
    s0 = next(i for i, l in enumerate(lines) if l.startswith("**TECHNICAL SKILLS**")) + 1
    s1 = s0
    while lines[s1].startswith("**") and ":**" in lines[s1]:
        s1 += 1
    lines[s0:s1] = skills
    # Delta block: scope line … up to the next role heading
    d0 = next(i for i, l in enumerate(lines) if l.startswith("Owns the team"))
    d1 = next(i for i, l in enumerate(lines) if l.startswith("**AI Algorithm Engineer**"))
    lines[d0:d1] = delta
    out = app_dir / "03_cv.md"
    out.write_bytes("\r\n".join(lines).encode("utf-8"))
    return out


def from_db(jd: str) -> dict[str, str | None]:
    sys.path.insert(0, str(HERE))
    import library_db as L
    con = L.connect()
    try:
        row = con.execute("SELECT * FROM applications WHERE jd=?", (jd,)).fetchone()
        if not row:
            sys.exit(f"error: no recorded application {jd!r} (library_db.py record first)")
        return {"dir": str(L.app_path(row["dir"])), "headline": row["headline"], "country": row["country"],
                "picks": L.picks_to_spec(L.recorded_picks(con, jd))}
    finally:
        con.close()


def fit(picks: str) -> bool:
    r = subprocess.run([sys.executable, str(HERE / "library_db.py"), "fit", "--picks", picks],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stdout + r.stderr, end="")
    return r.returncode == 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jd", help="recorded application: picks, headline, country and dir from the DB")
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--headline")
    ap.add_argument("--country")
    ap.add_argument("--picks")
    ap.add_argument("--summary")
    ap.add_argument("--force", action="store_true", help="write even when fit reports a line over the limit")
    a = ap.parse_args()
    if a.jd:
        rec = from_db(a.jd)
        a.dir = a.dir or Path(rec["dir"])
        a.headline, a.country, a.picks = a.headline or rec["headline"], a.country or rec["country"], a.picks or rec["picks"]
        cur = a.dir / "03_cv.md"
        if a.summary is None and cur.exists():
            a.summary = cur.read_bytes().decode("utf-8").split("\r\n")[4]
    missing = [k for k in ("dir", "headline", "country", "picks") if not getattr(a, k)]
    if missing:
        ap.error("missing " + ", ".join("--" + k for k in missing) + (" (not recorded for --jd)" if a.jd else ""))
    if not fit(a.picks) and not a.force:
        sys.exit("not written: a line is over the template limit (other lead/context variant, or --force)")
    out = assemble(a.dir, a.headline, a.country, a.picks, a.summary)
    n_b = sum(1 for l in out.read_bytes().decode("utf-8").split("\r\n") if l.startswith("- **"))
    print(f"wrote {out} ({n_b} bullets)")


if __name__ == "__main__":
    main()
