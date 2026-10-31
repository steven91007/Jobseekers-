---
name: cover-letter
description: Write a cover letter (動機信) for one job from the candidate's .docx template — change only the job title, the experience paragraph (one CV project, naming the required JD concepts the CV lacks), the company/industry sentence and the destination country, build DOCX/PDF + keyword check, then Codex scores it (HR fit, personalisation, keyword complementarity, CV consistency) with one revision. Separate from /tailor-cv. Use when the user says "cover letter", "動機信", "motivation letter" or gives a JD for a letter.
---

# /cover-letter

Separate from `/tailor-cv`: own template, own scripts, own output folder. It may **read** a job's
`cv/applications/*/jd.md` as input, but never writes into `cv/applications/` and never edits the CV
library.

**Glossary** — `$TPL` `cv/template/cover_letter/CHIYI_HSU_COVER_LETTER.docx` · `$D`
`cv/cover_letters/<YYYYMMDD>_<Co>_<Role>/` · `$LIB` `cv/profile/library_seed.json` (SoT; query via tailor-cv `library_db.py`, never the legacy `bullet_library.md`) · `$CONF`
`cv/profile/candidate_confirmations.md` · `$APP` the job's tailor-cv dir (`$DB status` → Dir, usually
`cv/applications/<…>/v3_cards/`; holds `01_jd.md`, `03_cv.md`, `07_job_keywords.json`) · `$PLAN`
`$D/letter_plan.json` · **Codex** = `Agent(subagent_type: "codex:codex-rescue")`,
prompt starts `--fresh`, review-only, no Notion. `cv/` is gitignored — never commit.

## The four slots (everything else stays word-for-word)
| Slot | Where in `$TPL` | Rule |
|---|---|---|
| `title` | header line, "dedicated … who", "As a/an … with" | JD title, strip "(m/w/d)"-style tags; article a/an is fixed by the script. Over ~32 chars the header wraps → use the shortest title the JD itself uses (e.g. HelloFresh "Marketing Automation Engineer") |
| `company_line` | replaces the paragraph-3 opening "Your international orientation and diverse team fascinate me, …" | one sentence, **≤ 190 characters** (the original is 133; the page has ~1–2 spare lines): name the company, its industry/product, one concrete fact from the JD or company page (the plan's `company_facts`), and why that draws the candidate. It must use a **business noun from the JD** (what the company sells/does, e.g. "payment orchestration", "real-estate marketplace", "decision platform") — not only a tech word. It must lead into the next sentence ("As a <title> with a multicultural background, I have worked closely with colleagues across …"). No new metrics, no claims beyond `$LIB`/`$CONF`. |
| `experience` | paragraph 2, "As you will see from my CV, …" | Written **from `$PLAN`** (step 1). Keep the opening "As you will see from my CV, I have six years of industry experience building and deploying production AI systems, with a focus on …". Focus list (4–5 items) = the plan's `letter_should_name` concepts first, in the JD's exact spelling (`write`), then the CV's top Skills nouns. Achievement sentence = **one project** from the plan's `cv_cards` (the CV's picked cards — same project name, users, scale as the CV), built from that card's bullets; name the remaining `letter_should_name` concepts there where that card's bullets prove them (`evidence_on_cv`). Facts only from `$LIB` / `$CONF` and this job's `03_cv.md`; ≤ 2 metrics, each already on the CV or CONF-confirmed; MaterialCheck is a POC (never "in production"). End with the ownership sentence. ≤ 620 chars (page room). Each JD term once — no stuffing. |
| `country` | "plan to move to <country> on …" and the paragraph-3 regions phrase: Germany → "Germany and Asia"; the Netherlands / United Kingdom / Ireland → "Germany, the Netherlands, and Asia" (CONF-ak1, automatic) | country of the job location (remote roles: the country the JD requires) . The visa sentence follows automatically (CONF-i3): Germany / the Netherlands / Ireland → "I do not need visa sponsorship."; United Kingdom → "I will need visa sponsorship."; any other country → build stops, ask the candidate. |

Outside the slots, the template's claims (move date January 1st, 2027 — CONF-i4) are the candidate's wording — never change
them here; if Codex flags one, report it to the user.

## Steps
| # | Step | Owner | Output |
|---|---|---|---|
| 0 | Setup: JD via URL / file / text, or an existing `cv/applications/*/jd.md` (read-only) | Opus | `$D/jd.md` |
| 1 | Plan (`build_letter.sh plan`) → title + country by rule; `company_line` + `experience` via `/multi-writer letter` (Opus + Codex, Fable judge, merge) | script + multi-writer | `$PLAN`, `$D/mw/letter/`, `$D/slots.json` |
| 2 | Build + layout QC + keyword check (`check-ats`) | script | `.docx`, `.pdf`, `layout.json`, `letter.txt`, `letter_check.md` |
| 3 | Score | Codex | `$D/review.md` |
| 4 | Revise slots once (only if REVISE) → step 2 | Opus | — |
| 5 | Report | Opus | `$D/summary.md` (≤ 15 lines) |

**1 · Plan → slots**
```bash
bash SKILL_DIR/scripts/build_letter.sh plan --jd <Co> --out <abs $D>/letter_plan.json   # [--dir <abs $APP>]
```
Reads `$APP/07_job_keywords.json`, `03_cv.md`, `01_jd.md` + `jd.md` and the experience DB (read-only). Per JD
concept: weight, claim, which terms are literally on the CV (same whole-word rule as the CV's ATS check),
evidence (bullets/Skills on the CV vs elsewhere), JD spelling. **`letter_should_name`** (≤ 4, ranked):
required concepts that are covered/SoT-supported but have no required term on the CV → align's `summary`
rows (proven only via a tool name) → covered concepts missing a required JD spelling → (filler) preferred /
keyword concepts. Also `cv_cards` (the CV's picked cards + bullet codes; `library_body: false` = the CV
predates a library edit — follow the CV's wording) and `company_facts`. No `07_job_keywords.json` (job never
ran /tailor-cv) → write one into `$D` per tailor-cv `references/ats_keywords.md` from `jd.md` and pass
`--keywords <abs $D>/07_job_keywords.json` to `plan` and `check-ats`; say so in the report.
A `letter_should_name` entry whose claim is `SoT-supported` needs wording the evidence proves (no metric);
if none of the card's bullets proves it, drop it rather than stretch. Unsure of a fact → list it for the
candidate, never guess.
Then `python3 .claude/skills/multi-writer/scripts/mw.py prompt --block letter --app <abs $APP> --letter <abs $D>
--out <abs $D>/mw/letter` and multi-writer steps 2–5; the judge's `violations` on the company line (invented
product names, customer names, figures not in `company_facts`) decide — take the other writer's line or drop the
fact. Copy the merged slots into `slots.json`. (Experiment 2026-10-31: Opus 52/60, Codex 46, merge wins 2/3.)

**2 · Build**
```bash
bash SKILL_DIR/scripts/build_letter.sh build --template <abs $TPL> --slots <abs $D>/slots.json \
  --out <abs $D>/CoverLetter_AmoHsu_<Co>.docx --pdf
bash SKILL_DIR/scripts/build_letter.sh check --pdf <abs $D>/CoverLetter_AmoHsu_<Co>.pdf --json <abs $D>/layout.json
```
```bash
bash SKILL_DIR/scripts/build_letter.sh check-ats --jd <Co> --letter <abs $D>/CoverLetter_AmoHsu_<Co>.docx \
  --txt-out <abs $D>/letter.txt --md <abs $D>/letter_check.md --json <abs $D>/letter_check.json
```
`WARN slot …` = a slot did not match the template (template changed?) → stop and tell the user.
Layout FAIL → shorten `company_line` (1 page, header title + e-mail on one line).
`check-ats` (advisory, exit 0): table per JD term (required first) · on CV · in letter · concept · claim, and
required/all coverage CV-only / letter-only / union; `FLAG over-claim?` (a gap / do-not-claim term in the
letter — fine only where it describes the company) and `FLAG stuffing?` (a JD term ≥ 3×). Expect the
letter to add the plan's `letter_should_name` terms; a missing one → reword the slot once, never stuff.

**3 · Score (Codex)** — read `jd.md`, `slots.json`, `letter.txt`, `letter_plan.json`, `letter_check.md`,
`$APP/03_cv.md`, `$LIB` (`cv/profile/library_seed.json`), `$CONF`.
- **Personalisation** 0–100: company + industry named correctly, concrete JD/company fact, reads as written for this job.
- **HR fit** 0–100: title match, motivation credibility, location/relocation clarity, tone, length.
- **Keyword complementarity** 0–100: does the letter add required JD concepts the CV lacks
  (`letter_check.md` letter-only, the plan's `letter_should_name`) in the JD's spelling, naturally —
  penalise stuffing (term lists without a verb, a term ≥ 3×, a term the evidence does not prove).
- **CV consistency** 0–100: the `experience` project is one of the CV's cards with the same facts (name,
  users, scale); no metric that is not on the CV or CONF-confirmed; MaterialCheck stays a POC; no tool the
  SoT does not list; list conflicts (template text outside the slots: report only).
- **VERDICT** `READY` | `REVISE` + ≤ 3 edits, all inside the four slots.

Output ≤ 30 lines: `PERSONALISATION`, `HR`, `COMPLEMENTARITY`, `CONSISTENCY`, `VERDICT`, edits.

**4 · Revise** — apply in-slot edits that stay truthful; one round only, no second review; re-run step 2
(build + check + `check-ats`).
**5 · Report** — slots, scores, verdict, applied/rejected edits, consistency flags, required-term union
coverage CV → CV + letter (`letter_check.md`), concepts the letter names, paths. Reply 4–6 lines.
