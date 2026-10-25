---
name: tailor-cv
description: Tailor the CV to a JD from a private bullet library — pick 5 library bullets, JD-title headline + Summary opening, "Open to relocate to <country>", JD-mapped Skills, Codex-written Summary, DOCX/PDF build + layout QC, Codex HR/HM/ATS review, one Opus revision. Library mode adds/updates bullets (candidate sets metric + business value; Notion = evidence). Use when the user gives a JD (URL, file, text) or says "tailor my cv", "客製化履歷", "library".
argument-hint: "<JD url | jd.md | text> | library [project] [--no-pdf]"
---

# /tailor-cv

**Glossary** — `$D` application dir · `$LIB` `cv/profile/bullet_library.md` · `$CONF`
`cv/profile/candidate_confirmations.md` · `$RULES` `references/writing_rules.md` ·
`$TPL` `cv/template/CV_template.md` (+ `.docx`) · **SoT** = `$LIB` + `$CONF` ·
**Codex** = `Agent(subagent_type: "codex:codex-rescue")`, prompt starts `--fresh`, review-only, no Notion.

**Principles** — SoT only; the candidate owns metrics + business value; Notion is evidence,
never a metric source. Missing fact → AskUserQuestion. No extra agents, no per-JD Notion,
short outputs. `cv/` is gitignored — never commit.

**Per-application diff** (rest of `$TPL` verbatim, incl. real job titles):
| Item | Rule |
|---|---|
| Headline (L2) | `**<JD TITLE>  \|  <AREA>  •  <AREA>**`, caps, strip "(m/w/d)" |
| Contact (L3) | `Taiwan  ·  Open to relocate to <country>   \|   …` |
| Summary (L5) | Codex, last; opens `**<JD title> with 6 years of industry experience**` |
| Delta bullets | 5 `$LIB` IDs, verbatim |
| Skills | 6 category rows (Languages & backend, AI — LLM & agents, AI — CV, MLOps, …) ordered for the JD; items only from that `$LIB` category |

## Apply mode
| # | Step | Owner | Output |
|---|---|---|---|
| 0 | Setup: JD via WebFetch/file/text; `$D` = `cv/applications/<YYYYMMDD>_<Co>_<Role>/` | Opus | `jd.md` |
| 1 | JD brief ≤ 15 lines: title, location/mode, top-5 criteria, 6–10 keywords, hard reqs, tags | Opus | `01_jd.md` |
| 2 | Pick 5 bullets (Preset first, if any) | Opus | — |
| 3 | Copy `$TPL` (CRLF), apply diff except Summary | Opus | `03_cv.md` |
| 4 | Summary | Codex | L5 |
| 5 | Build + layout QC | script | `.docx`, `.pdf`, `06_layout.json` |
| 6 | HR / HM / ATS review | Codex | `04_review.md` |
| 7 | Revise (1 round) → step 4 if bullets/Skills changed → step 5 | Opus | — |
| 8 | Report | Opus | `summary.md` (≤ 25 lines) |

**2 · Pick** — rank by the JD's lead responsibilities (what the employer values), not
keyword counts. Prefer one bullet per project; business value beats engineering detail.
Uncovered top-5 criterion → ask once → library mode or log as gap. Never write bullets here.
Approved CV → save as a Preset in `$LIB`.

**4 · Summary (Codex)** — read `jd.md`, `01_jd.md`, `03_cv.md`, SoT, `$RULES`. 400–470 chars
incl. `**`, opening + relative clause, ≤ 3 metrics from "Summary facts", business value
first, JD lead responsibilities, no tool lists, consistent with bullets. Reply `SUMMARY:` /
`CHARS:` / clause → source. Insert verbatim; one SendMessage retry on length, fact or
over-claim — never hand-edit. **Re-run step 4 when a bullet is swapped or a claim changes; a scope/number detail that the Summary doesn't use needs no re-run.**

**5 · Build**
```bash
bash SKILL_DIR/scripts/build_cv.sh patch --template-docx <abs .docx> --template-md <abs $TPL> \
  --md <abs $D>/03_cv.md --out <abs $D>/CV_<FirstLast>_<Co>.docx --pdf
bash SKILL_DIR/scripts/build_cv.sh check --pdf <abs pdf> --docx <abs docx> --json <abs $D>/06_layout.json
```
`patch` WARN → fix line. FAIL → shorter Summary (step 4) / headline; 3-line bullet → other
variant or library mode. Locked docx → `_preview`.

**6 · Review (Codex)** — read `jd.md`, `01_jd.md`, `03_cv.md`, `06_layout.json`, SoT, `$RULES`.
- **HR** 0–100: 10-second role fit, why-this-job, visible outcomes, location/language, rejection risks.
- **HM** 0–100: depth on top-5 criteria, business value, credibility, ownership, interview probes.
- **ATS** %: 15–25 exact JD keywords (required first), found/missing; each missing → SoT-supported or gap.
- **VERDICT** `READY` | `REVISE` + ≤ 7 edits (bullets = `$LIB` ID swap; Skills/headline = `$LIB` items).

Output ≤ 60 lines: `HR`, `HM`, `ATS`, `VERDICT`, keyword table, 3 strengths, 3 risks, edits.

**7 · Revise (Opus)** — apply truthful, in-diff edits only; unsupported keywords → gaps;
missing must-have → library mode. No second review. Log applied/rejected edits (+ reason).

**8 · Report** — bullets + criterion each serves, HR/HM/ATS + verdict, edits, gaps, layout,
paths. Reply 5–8 lines.

## Library mode — `/tailor-cv library [project]`
Trigger: new project, changed metric, or gap from step 2/7.
1. **Ask** (AskUserQuestion): business value (users, decision/time/money, scale) + metric; user wording wins.
2. **Evidence**: `Skill("Notion:find", "<keywords>")` → fetch 2–5 pages; conflict with user metric → show both, ask.
3. **Draft** 1–2 variants: ≤ 215 chars, bold lead 4–8 words, value/metric in lead, tags.
4. **Approve** → save to `$LIB` (value, evidence, CONF IDs); new statements → `$CONF` (dated, next ID).
