"""Multi-writer harness: the same prompt to two (or more) writers, blind scoring by Fable.

Production (one block, one job):
    mw.py prompt --block summary|letter|bullet|qa --app <abs app dir> --out <abs work dir>
                 [--letter <abs letter dir>] [--card CODE --capability "…"] [--questions q.json]
                 [--company X --title Y]     # default: parsed from <app>/01_jd.md line 1 "# Co · Title"
    mw.py pack   --dir <abs work dir>        # shuffles out_<writer>.md (default writers opus, codex) → judge_packet.md
    mw.py result --dir <abs work dir>        # judge.json + mapping → ranking with names, violations, merge note

Experiment (blocks × jds × writers):
    mw.py prep   --exp <dir> --config <config.json>   mw.py gemini --exp <dir>   mw.py pack --exp <dir>
    mw.py score  --exp <dir>  → report.md

Writers are subagents driven by the main agent (Opus writes out_opus.md; Codex's reply is saved as
out_codex.md); this script never calls them. The optional Gemini runner exists for experiments only.
Everything private (JD, CV, SoT, outputs) lives under cv/ (gitignored).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RULES = ROOT / ".claude/skills/tailor-cv/references/writing_rules.md"
CONF = ROOT / "cv/profile/candidate_confirmations.md"
BANNED = ["leveraged", "spearheaded", "robust", "seamless", "cutting-edge", "state-of-the-art", "empowering",
          "driving", "passionate", "synergy", "holistic", "impactful", "solutions"]
WRITERS = ["gemini", "codex", "opus"]   # overridden by config "writers" (prep writes it to <exp>/writers.json)
GEMINI_MODELS = ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-3.1-flash-lite"]

COMMON = """# Writing task — {block} for {company} ({jd_title})

You are writing one block of a job application for the candidate below. Use ONLY the facts in the
source-of-truth sections (CV, candidate confirmations). Anything not there is unverified and must not
appear. Follow the writing rules. Reply with the output block only, in the exact format asked.

{task}

---
## Source of truth 1 — the tailored CV for this job (03_cv.md)
{cv}

## Source of truth 2 — candidate confirmations (facts the candidate stated; CONF ids)
{conf}

## Job description brief (01_jd.md)
{brief}

## Full job description (jd.md)
{jd}

## Writing rules
{rules}
"""

TASKS = {
    "summary": """## Task: CV Summary (line 5, PROFESSIONAL PROFILE)
Write the Summary for this CV. The Delta block is a scope line + project cards (bold title — context,
then bullets whose bold lead is a JD duty phrase). Build the Summary from the cards' context lines and
the scope line, not from individual bullets.
Rules: 400–470 characters including the ** markers; opens exactly `**{jd_title} with 6 years of
industry experience**` followed by a relative clause ("who …"); sentence 1 = role + business outcome of
card 1; sentence 2 = card 2's outcome + ownership (design → launch → maintenance → incident response);
sentence 3 = how the candidate works with users / international teams; optional sentence 4 = one
engineering judgement. ≤ 3 metrics, all already on the CV or in the confirmations ("6 years" does not
count). Mirror the JD's lead responsibilities in its own words; no tool lists; no project that is not on the CV.
Reply ONLY:
SUMMARY: <text>
CHARS: <count including ** markers>
SOURCES: one line per clause → CV card / bullet / CONF id""",

    "letter": """## Task: two cover-letter slots
The candidate's cover letter template is fixed except for these slots. Write both.
1. `company_line` — ONE sentence, ≤ 190 characters. It replaces the paragraph-3 opening and must lead
   into the next (fixed) sentence "As a {jd_title} with a multicultural background, I have worked closely
   with colleagues across Germany and Asia …". Name the company, its industry/product with a business noun
   from the JD (what the company sells/does), one concrete fact from the JD/company facts below, and why
   that draws the candidate. No new metrics.
2. `experience` — paragraph 2, ≤ 620 characters. Must start exactly: "As you will see from my CV, I have
   six years of industry experience building and deploying production AI systems, with a focus on ".
   Focus list (4–5 items) = the plan's `letter_should_name` concepts first, in the JD's exact spelling
   (`write`), then the CV's top Skills nouns. Achievement sentence = ONE project from the plan's `cv_cards`
   (same project name, users, scale as the CV), built from that card's bullets; name the remaining
   `letter_should_name` concepts there only where that card's bullets prove them. ≤ 2 metrics, each already
   on the CV or CONF-confirmed; MaterialCheck is a POC (never "in production"). End with an ownership
   sentence. Each JD term once — no stuffing.
Letter plan (from the keyword check of CV vs JD):
{plan}
Reply ONLY with JSON: {{"company_line": "...", "experience": "..."}}""",

    "bullet": """## Task: one new project-card bullet (library mode)
Card facts (what exists; do NOT copy any bullet already on the CV):
{card}
Capability the new bullet must prove, for this JD: {capability}
Write ONE capability bullet in the CV's format: `**<JD duty phrase> —** <what was built> ; <decision + why,
or outcome>`. The bold lead is the job's language (3–6 words, taken from the JD's own task wording —
quote-able from jd.md), not a result or tool phrase. Body: plain verbs, ≤ 3 numbers, ≤ 1 parenthetical,
facts only from the card facts / CV / confirmations; never invent a rationale. Whole bullet ≤ 225 characters
including the ** markers. Then give 2 alternative leads (3–6 words each, also JD duty phrases).
Reply ONLY:
BULLET: <the bullet>
CHARS: <count>
LEAD_ALT_1: <lead>
LEAD_ALT_2: <lead>
SOURCES: card fact / CV bullet / CONF id per clause""",

    "qa": """## Task: three application-form answers
Answer the three questions an online application form for this job asks. First person, plain English,
facts only from the CV / confirmations; no metric that is not there; MaterialCheck is a POC. The company
and role facts come from the JD. Stay within the word limits.
Q1 (≤ 120 words): Why do you want to work at {company}, and why this role?
Q2 (≤ 150 words): Describe a project you owned end to end — the problem, what you built, and the outcome.
Q3 (≤ 120 words): What would you focus on in your first 90 days in this role?
Reply ONLY:
Q1: <answer>
Q2: <answer>
Q3: <answer>""",
}

JUDGE = """# Blind review — {block} for {company} ({jd_title})

Three candidates (A, B, C) wrote the same block from the same inputs. You do not know who wrote which.
Score each independently, then rank. Be strict about truth: the source of truth is the CV and the
candidate confirmations below; a claim not supported there is a violation (quote it).
Then read each candidate twice more, in role:
- as an **HR recruiter** at {company} doing a 10-second screen — is the wording plain, does it say
  "this person already does this job" in the JD's own words, any jargon or phrasing that would make
  you hesitate? (hr_wording)
- as the **hiring manager** for this opening — does the wording sound like an engineer who did the work
  (concrete decisions, trade-offs, scale) or like marketing copy / buzzwords? (hm_wording)

## The task they were given
{task}

## Candidate A
{A}

## Candidate B
{B}

## Candidate C
{C}

## Source of truth 1 — the tailored CV (03_cv.md)
{cv}

## Source of truth 2 — candidate confirmations
{conf}

## Job description brief
{brief}

## Writing rules
{rules}

## Reply ONLY with this JSON (no prose outside it)
{{
 "scores": {{
   "A": {{"truth": 0-10, "jd_fit": 0-10, "specificity": 0-10, "writing": 0-10, "hr_wording": 0-10, "hm_wording": 0-10,
         "worst_phrase": "the one phrase HR or HM would most object to, and why", "format_ok": true/false,
         "violations": ["quoted claim → why"], "note": "one line"}},
   "B": {{...}}, "C": {{...}}
 }},
 "rank": ["best", "middle", "worst"],
 "distinctness": {{"level": "low|medium|high", "what_differs": "one line: are these materially different choices (facts picked, structure, angle) or near-paraphrases?"}},
 "would_merge": "one line: would combining elements of two candidates beat the best single one? which elements?"
}}
Scoring guide — truth: 10 = every claim traceable, 0 = invented facts; jd_fit: mirrors the JD's lead
responsibilities in its words; specificity: concrete who/what/scale over generic phrasing; writing: voice,
banned words, length limits, format compliance (a length/format breach caps writing at 5); hr_wording:
plain, screen-able, title/duties recognisable in 10 seconds; hm_wording: credible engineering voice,
decisions and trade-offs, no buzzwords or empty superlatives.
"""


def read(p: Path) -> str:
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n") if p.exists() else ""


def strip_md(s: str) -> str:
    return re.sub(r"\*\*", "", s)


# ---------------------------------------------------------------- prompt building
DEFAULT_QUESTIONS = [
    ("Q1", 120, "Why do you want to work at {company}, and why this role?"),
    ("Q2", 150, "Describe a project you owned end to end — the problem, what you built, and the outcome."),
    ("Q3", 120, "What would you focus on in your first 90 days in this role?"),
]


def job_head(app: Path) -> tuple[str, str]:
    """(company, jd_title): the tailor-cv DB row whose dir matches `app`, else '# Co · Title' in 01_jd.md."""
    db = ROOT / "cv/profile/experience.sqlite"
    if db.exists():
        import sqlite3
        rel = str(app.resolve().relative_to(ROOT)).replace("\\", "/") if app.resolve().is_relative_to(ROOT) else str(app)
        for company, title, d in sqlite3.connect(db).execute("SELECT company, title, dir FROM applications"):
            if d and (rel.endswith(d.replace("\\", "/").rstrip("/")) or d.replace("\\", "/").rstrip("/").endswith(rel)):
                return company, re.sub(r"\s*\((?:[mwfdx]/?)+\)\s*$", "", title).strip()
    first = read(app / "01_jd.md").splitlines()[0] if read(app / "01_jd.md") else ""
    m = re.match(r"#\s*(?:JD brief\s*[—-]\s*)?(.+?)\s*[·•]\s*(.+)", first)
    if not m:
        return "", ""
    return m.group(1).strip(), re.sub(r"\s*\((?:[mwfdx]/?)+\)\s*$", "", m.group(2)).strip()


def card_facts(code: str) -> str:
    out = subprocess.run([sys.executable, str(ROOT / ".claude/skills/tailor-cv/scripts/library_db.py"), "card", code],
                         capture_output=True, text=True).stdout
    return "\n".join(l for l in out.splitlines() if not re.match(r"\s+- [A-Z]+\d", l))  # facts, no bullet lines


def build_prompt(block: str, app: Path, company: str, jd_title: str, letter_dir: Path | None = None,
                 card: str = "", capability: str = "", questions: list | None = None) -> tuple[str, str]:
    """→ (prompt.md text, task text). The task text is reused in the judge packet."""
    plan = ""
    if letter_dir:
        lp = json.loads(read(letter_dir / "letter_plan.json") or "{}")
        plan = json.dumps({k: lp.get(k) for k in ("letter_should_name", "cv_cards", "company_facts")}, ensure_ascii=False, indent=1)
    task = TASKS[block]
    if block == "qa":
        qs = questions or DEFAULT_QUESTIONS
        qlines = "\n".join(f"{k} (≤ {n} words): {q}" for k, n, q in qs)
        rlines = "\n".join(f"{k}: <answer>" for k, _, _ in qs)
        task = re.sub(r"Q1 \(≤ 120 words\).*?Q3: <answer>", qlines + "\nReply ONLY:\n" + rlines, task, flags=re.S)
    task = task.format(jd_title=jd_title, company=company, plan=plan, card=card_facts(card) if card else "",
                       capability=capability)
    prompt = COMMON.format(block=block, company=company, jd_title=jd_title, task=task, cv=read(app / "03_cv.md"),
                           conf=read(CONF), brief=read(app / "01_jd.md"), jd=read(app / "jd.md"), rules=read(RULES))
    return prompt, task


def write_prompt(d: Path, block: str, app: Path, company: str, jd_title: str, **kw) -> None:
    d.mkdir(parents=True, exist_ok=True)
    prompt, task = build_prompt(block, app, company, jd_title, **kw)
    (d / "prompt.md").write_text(prompt, encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"block": block, "company": company, "jd_title": jd_title,
                                             "app_dir": str(app), "task": task}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"prompt {d}: {len(prompt)} chars")


def prompt_cmd(a) -> None:
    company, title = job_head(a.app)
    company, title = a.company or company, a.title or title
    if not (company and title):
        raise SystemExit("could not read '# Company · Title' from 01_jd.md — pass --company/--title")
    qs = [tuple(x) for x in json.loads(read(a.questions))] if a.questions else None
    (a.out).mkdir(parents=True, exist_ok=True)
    (a.out / "writers.json").write_text(json.dumps(["opus", "codex"]), encoding="utf-8")
    write_prompt(a.out, a.block, a.app, company, title, letter_dir=a.letter, card=a.card or "",
                 capability=a.capability or "", questions=qs)


# ---------------------------------------------------------------- prep (experiment)
def prep(exp: Path, config: Path) -> None:
    cfg = json.loads(config.read_text(encoding="utf-8"))
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "writers.json").write_text(json.dumps(cfg.get("writers", WRITERS)), encoding="utf-8")
    for jd, c in cfg["jds"].items():
        app = ROOT / c["app_dir"]
        for block in cfg["blocks"]:
            write_prompt(exp / block / jd, block, app, c["company"], c["jd_title"],
                         letter_dir=ROOT / c["letter_dir"] if c.get("letter_dir") else None,
                         card=c.get("card", ""), capability=c.get("capability", ""))


# ---------------------------------------------------------------- gemini
def run_gemini(prompt: str, log: Path | None = None) -> str:
    last = ""
    for model in GEMINI_MODELS:
        for attempt in range(3):
            r = subprocess.run(["gemini", "--skip-trust", "-m", model, "-p", prompt], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=240)
            out = "\n".join(l for l in r.stdout.splitlines()
                            if not l.startswith(("Warning:", "Ripgrep", "Loaded cached", "Flushing")))
            if r.returncode == 0 and out.strip():
                if log:
                    log.write_text(f"model={model} attempt={attempt}\n", encoding="utf-8")
                return out.strip()
            last = (r.stderr or r.stdout)[-400:]
            time.sleep(8 * (attempt + 1))
    raise SystemExit(f"gemini failed on all models: {last}")


def gemini(exp: Path, block: str | None, jd: str | None) -> None:
    for p in sorted(exp.glob("*/*/prompt.md")):
        if block and p.parts[-3] != block or jd and p.parts[-2] != jd:
            continue
        out = p.with_name("out_gemini.md")
        if out.exists():
            continue
        t0 = time.time()
        out.write_text(run_gemini(read(p), p.with_name("gemini.log")), encoding="utf-8")
        print(f"gemini {p.parts[-3]}/{p.parts[-2]}: {len(read(out))} chars, {time.time() - t0:.0f}s")


# ---------------------------------------------------------------- pack
def writers_of(exp: Path) -> list[str]:
    global WRITERS
    if (exp / "writers.json").exists():
        WRITERS = json.loads(read(exp / "writers.json"))
    return WRITERS


def pack_dir(d: Path) -> None:
    meta = json.loads(read(d / "meta.json"))
    outs = {w: read(d / f"out_{w}.md") for w in WRITERS}
    if not all(outs.values()):
        print(f"skip {d}: missing {[w for w, v in outs.items() if not v]}")
        return
    order = WRITERS[:]
    random.Random(f"{d}/{len(order)}").shuffle(order)
    letters = "ABC"[: len(order)]
    mapping = dict(zip(letters, order))
    app = Path(meta["app_dir"]) if Path(meta["app_dir"]).is_absolute() else ROOT / meta["app_dir"]
    cands = {L: outs[mapping[L]] for L in letters}
    packet = JUDGE.format(block=meta["block"], company=meta["company"], jd_title=meta["jd_title"], task=meta["task"],
                          A=cands.get("A", ""), B=cands.get("B", ""), C=cands.get("C", "(no candidate C — ignore)"),
                          cv=read(app / "03_cv.md"), conf=read(CONF), brief=read(app / "01_jd.md"), rules=read(RULES))
    if len(order) == 2:
        packet = packet.replace("Three candidates (A, B, C)", "Two candidates (A, B)").replace(
            '"B": {{...}}, "C": {{...}}', '"B": {...}').replace('"rank": ["best", "middle", "worst"]', '"rank": ["best", "worst"]')
    (d / "judge_packet.md").write_text(packet, encoding="utf-8")
    (d / "mapping.json").write_text(json.dumps(mapping), encoding="utf-8")
    print(f"pack {d}: {mapping}")


def pack(exp: Path | None, one_dir: Path | None) -> None:
    if one_dir:
        writers_of(one_dir)
        pack_dir(one_dir)
        return
    writers_of(exp)
    for m in sorted(exp.glob("*/*/meta.json")):
        pack_dir(m.parent)


def result(d: Path) -> None:
    """Ranking with writer names, violations and the merge note for one work dir."""
    writers_of(d)
    judge = json.loads(re.search(r"\{.*\}", read(d / "judge.json"), re.S).group(0))
    mapping = json.loads(read(d / "mapping.json"))
    print(f"rank: {' > '.join(mapping[x] for x in judge['rank'])}")
    for L, w in mapping.items():
        s = judge["scores"][L]
        total = sum(s.get(k, 0) for k in ("truth", "jd_fit", "specificity", "writing", "hr_wording", "hm_wording"))
        print(f"\n{w} ({L}): total {total}/60 · truth {s['truth']} · HR {s.get('hr_wording')} · HM {s.get('hm_wording')} · format_ok {s.get('format_ok')}")
        for v in s.get("violations", []):
            print(f"  ! {v}")
        print(f"  worst phrase: {s.get('worst_phrase', '')}")
    print(f"\nmerge: {judge.get('would_merge', '')}")
    for L, w in mapping.items():
        print(f"\n--- {w} ---\n{read(d / f'out_{w}.md').strip()}")


# ---------------------------------------------------------------- score
def toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9€%→.+-]+", strip_md(s).lower()))


def jaccard(a: str, b: str) -> float:
    A, B = toks(a), toks(b)
    return len(A & B) / max(1, len(A | B))


def body_len(block: str, text: str) -> dict:
    """Length of the constrained part(s) per block, for the mechanical limit check."""
    if block == "summary":
        m = re.search(r"SUMMARY:\s*(.+)", text)
        return {"summary": len(m.group(1).strip()) if m else -1, "limit": "400-470"}
    if block == "bullet":
        m = re.search(r"BULLET:\s*(.+)", text)
        return {"bullet": len(m.group(1).strip()) if m else -1, "limit": "<=225"}
    if block == "letter":
        try:
            j = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            return {"company_line": len(j.get("company_line", "")), "experience": len(j.get("experience", "")),
                    "limit": "<=190 / <=620"}
        except Exception:
            return {"company_line": -1, "experience": -1, "limit": "<=190 / <=620"}
    if block == "qa":
        words = {k: len(re.sub(r"Q\d:\s*", "", v).split()) for k, v in re.findall(r"(Q\d):\s*(.+?)(?=\nQ\d:|\Z)", text, re.S)}
        return {**words, "limit": "120/150/120 words"}
    return {}


def score(exp: Path) -> None:
    writers_of(exp)
    rows, lines = [], ["# Multi-writer experiment — report", ""]
    for m in sorted(exp.glob("*/*/meta.json")):
        d = m.parent
        block, jd = d.parts[-2], d.parts[-1]
        jp, mp = d / "judge.json", d / "mapping.json"
        if not (jp.exists() and mp.exists()):
            continue
        judge = json.loads(re.search(r"\{.*\}", read(jp), re.S).group(0))
        mapping = json.loads(read(mp))
        outs = {w: read(d / f"out_{w}.md") for w in WRITERS}
        sims = {f"{a}~{b}": round(jaccard(outs[a], outs[b]), 2) for i, a in enumerate(WRITERS) for b in WRITERS[i + 1:]}
        rank = [mapping[x] for x in judge["rank"]]
        for letter, w in mapping.items():
            s = judge["scores"][letter]
            total = s["truth"] + s["jd_fit"] + s["specificity"] + s["writing"] + s.get("hr_wording", 0) + s.get("hm_wording", 0)
            banned = [b for b in BANNED if re.search(rf"\b{re.escape(b)}\b", outs[w], re.I)]
            rows.append({"block": block, "jd": jd, "writer": w, "truth": s["truth"], "jd_fit": s["jd_fit"],
                         "specificity": s["specificity"], "writing": s["writing"], "hr": s.get("hr_wording"),
                         "hm": s.get("hm_wording"), "worst_phrase": s.get("worst_phrase", ""), "total": total,
                         "format_ok": s.get("format_ok"), "violations": len(s.get("violations", [])),
                         "rank": rank.index(w) + 1, "banned": banned, "len": body_len(block, outs[w]),
                         "distinct": judge["distinctness"]["level"], "merge": judge.get("would_merge", ""), "sims": sims})
    (exp / "scores.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    blocks = sorted({r["block"] for r in rows})
    lines.append("## Per block (3 JDs × 3 writers)\n")
    lines.append("| block | writer | mean total /60 | mean truth | mean HR wording | mean HM wording | violations | wins | mean rank | format ok |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    verdicts = []
    for b in blocks:
        br = [r for r in rows if r["block"] == b]
        per = {}
        for w in WRITERS:
            wr = [r for r in br if r["writer"] == w]
            if not wr:
                continue
            per[w] = dict(total=sum(r["total"] for r in wr) / len(wr), truth=sum(r["truth"] for r in wr) / len(wr),
                          hr=sum(r["hr"] or 0 for r in wr) / len(wr), hm=sum(r["hm"] or 0 for r in wr) / len(wr),
                          viol=sum(r["violations"] for r in wr), wins=sum(r["rank"] == 1 for r in wr),
                          rank=sum(r["rank"] for r in wr) / len(wr), fmt=sum(bool(r["format_ok"]) for r in wr))
            p = per[w]
            lines.append(f"| {b} | {w} | {p['total']:.1f} | {p['truth']:.1f} | {p['hr']:.1f} | {p['hm']:.1f} | {p['viol']} | {p['wins']} | {p['rank']:.2f} | {p['fmt']}/{len(wr)} |")
        # spread per JD, winners, similarity, distinctness
        jds = sorted({r["jd"] for r in br})
        spreads, winners = [], []
        for j in jds:
            jr = [r for r in br if r["jd"] == j]
            spreads.append(max(r["total"] for r in jr) - min(r["total"] for r in jr))
            winners.append(next(r["writer"] for r in jr if r["rank"] == 1))
        sims = [v for r in br[::len(WRITERS)] for v in r["sims"].values()]
        distinct = [r["distinct"] for r in br[::len(WRITERS)]]
        merges = [r["merge"] for r in br[::len(WRITERS)]]
        spread = sum(spreads) / len(spreads)
        sim = sum(sims) / len(sims)
        n_win = len(set(winners))
        best = max(per, key=lambda w: per[w]["total"])
        n_merge = sum(m.lower().startswith(("yes", "slightly", "marginally")) for m in merges)
        # several writers pay off only when the best writer changes between JDs, or the judge says a
        # merge beats the best single output in most JDs; a large spread alone just means one writer is weak
        need = "YES" if n_win >= 2 else ("MERGE" if n_merge * 2 > len(merges) else "NO")
        verdicts.append((b, spread, winners, sim, distinct, best, need, n_merge, len(merges)))
    lines.append("\n## Does the block need several writers?\n")
    lines.append("| block | mean score spread (best−worst, /60) | winners per JD | mean Jaccard similarity | judge distinctness | judge: merge beats best | best single writer | several writers worth it |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for b, spread, winners, sim, distinct, best, need, n_merge, n in verdicts:
        lines.append(f"| {b} | {spread:.1f} | {', '.join(winners)} | {sim:.2f} | {'/'.join(distinct)} | {n_merge}/{n} | {best} | **{need}** |")
    lines.append("\nRule: YES = the best writer changes between JDs (keep several writers and pick); MERGE = one writer wins every "
                 "time but the judge says a merge beats it in most JDs (second writer as a source of parts / a check); NO = one writer suffices. "
                 "A large spread alone only shows that a writer is weak.")
    lines.append("\n## Per JD detail\n")
    lines.append("| block | jd | writer | truth | jd_fit | spec | writing | HR | HM | total | rank | violations | banned | lengths | worst phrase |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(f"| {r['block']} | {r['jd']} | {r['writer']} | {r['truth']} | {r['jd_fit']} | {r['specificity']} | "
                     f"{r['writing']} | {r['hr']} | {r['hm']} | {r['total']} | {r['rank']} | {r['violations']} | {','.join(r['banned']) or '-'} | {r['len']} | {r['worst_phrase']} |")
    (exp / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[: 8 + 3 * len(WRITERS) * len(blocks) + 12]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep"); p.add_argument("--exp", required=True, type=Path); p.add_argument("--config", required=True, type=Path)
    g = sub.add_parser("gemini"); g.add_argument("--exp", required=True, type=Path); g.add_argument("--block"); g.add_argument("--jd")
    k = sub.add_parser("pack"); k.add_argument("--exp", type=Path); k.add_argument("--dir", type=Path)
    s = sub.add_parser("score"); s.add_argument("--exp", required=True, type=Path)
    pr = sub.add_parser("prompt")
    pr.add_argument("--block", required=True, choices=sorted(TASKS)); pr.add_argument("--app", required=True, type=Path)
    pr.add_argument("--out", required=True, type=Path); pr.add_argument("--letter", type=Path)
    pr.add_argument("--card"); pr.add_argument("--capability"); pr.add_argument("--questions", type=Path)
    pr.add_argument("--company"); pr.add_argument("--title")
    rs = sub.add_parser("result"); rs.add_argument("--dir", required=True, type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "prep":
        prep(a.exp, a.config)
    elif a.cmd == "gemini":
        gemini(a.exp, a.block, a.jd)
    elif a.cmd == "pack":
        if not (a.exp or a.dir):
            ap.error("pack needs --exp or --dir")
        pack(a.exp, a.dir)
    elif a.cmd == "score":
        score(a.exp)
    elif a.cmd == "prompt":
        prompt_cmd(a)
    else:
        result(a.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
