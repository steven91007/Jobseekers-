---
name: tailor-cv
description: Tailor the CV to a JD from a private project-card library — scope line + 2 project cards (title + context + capability bullets whose bold lead is a JD duty phrase), JD-title headline + Summary opening, Germany contact line, JD-mapped Skills, Codex-written Summary, DOCX/PDF build (photo, 10 pt, one page) + layout QC, external ATS check (vendored Resume-Matcher), Codex HR/HM review, one Opus revision. Library mode adds/updates cards (candidate sets metric + business value; Notion = evidence). Use when the user gives a JD (URL, file, text) or says "tailor my cv", "客製化履歷", "library".
---

# /tailor-cv

**Glossary** — `$D` application dir · `$DB` `python3 SKILL_DIR/scripts/library_db.py` over
`cv/profile/experience.sqlite` (built from `cv/profile/library_seed.json`; `references/library_db_spec.md` + `_v2.md` + `_v3.md` (concept layer);
read-only export `cv/profile/bullet_library.generated.md`; Notion mirror "Personal experiences") · `$CONF`
`cv/profile/candidate_confirmations.md` · `$RULES` `references/writing_rules.md` · `$PROMPTS`
`references/codex_prompts.md` · `$TPL` `cv/template/CV_template_v3.md` (+ `CV_template_v3.docx`,
photo embedded) · **SoT** = `$DB` + `$CONF` (the seed is the editable form; `bullet_library.md` is legacy) ·
**Codex** = `Agent(subagent_type: "codex:codex-rescue")` with a `$PROMPTS` template filled in
(starts `--fresh`, review-only, no Notion).

**Querying the library** — never read the whole export; ask the DB: `$DB cards [--for X]` ·
`$DB card VI` · `$DB leads VI1` · `$DB skills --rows` · `$DB keywords --jd TOMRA` ·
`$DB match <abs $D>/07_job_keywords.json` (known terms → coverage + covering bullets/skills + spelling;
`NEW` terms → classify, then add to the seed) · `$DB render --picks "…" --crlf` (prints Skills rows + Delta
block in `03_cv.md` format) · `$DB record --jd … --picks "…"` · `$DB diff --jd …` ·
`$DB pick --jd <Co> --dir <abs $D>` (suggested pick string + rationale) · `$DB fit --picks "…"` (per-line
length check against the template's measured limits; exit 1 = a line is over) ·
`$DB keywords --coverage gap` · `$DB set-coverage "<term>" <coverage> [--note …]` · `$DB status` ·
`$DB resolve "<term>"` (term → concept, rule that fired; `NEW` = unknown) · `$DB concept AGENTIC` ·
`$DB gaps --jd <Co>` · `$DB align --jd <Co> [--picks "…"]` · `$DB set-concept "<term>" <CODE>` ·
`$DB sot-leads --jd <Co> [--per-bullet 2] [--json] [--write-from leads.json]` (missing SoT-supported ATS
terms → per-bullet term bundles; hand-written leads → seed after a contains-term + fit check).

**Principles** — SoT only; the candidate owns metrics + business value; Notion is evidence,
never a metric source. Missing fact → AskUserQuestion. No extra agents, no per-JD Notion,
short outputs. `cv/` is gitignored — never commit.

**Project cards** — the Delta block is 1 scope line + **2 cards** (3 only if the JD needs it, then
2 bullets each). Card = `**Title** — context` (what it is · who uses it · scale / business metric)
+ 2–3 capability bullets. **The bold lead is a JD duty phrase** (3–6 words, e.g. "ML training
pipelines & evaluation —"), chosen from the bullet's `leads:` list in `$LIB`; the body is verbatim.
Metrics live in the context line (business) and the bullet body (technical).

**Per-application diff** (rest of `$TPL` verbatim, incl. photo, real job titles, Mirle, Education):
| Item | Rule |
|---|---|
| Headline (L2) | `**<JD TITLE>  \|  <AREA>  •  <AREA>**`, caps, strip "(m/w/d)" |
| Contact (L3) | `Germany   \|   +49 XXX XXXXXXXX   \|   …` (template line, no relocation note) |
| Summary (L5) | `/multi-writer summary` (Opus + Codex → Fable judge → merge), last; opens `**<JD title> with 6 years of industry experience**`; built from the cards' context lines |
| Scope line | `$LIB` scope line verbatim |
| Cards | 2 `$LIB` cards: title variant for the JD, 2–3 bullets each, lead variant closest to the JD's task wording |
| Skills | 6 category rows (Languages & backend, AI — LLM & agents, AI — CV, MLOps, …) ordered for the JD; items only from that `$LIB` category |

## Apply mode
| # | Step | Owner | Output |
|---|---|---|---|
| 0 | Setup: JD via WebFetch/file/text; `$D` = `cv/applications/<YYYYMMDD>_<Co>_<Role>/` | Opus | `jd.md` |
| 1 | JD brief ≤ 15 lines: title, location/mode, top-5 criteria, 8–12 **duty phrases** (the JD's task wording), keywords, hard reqs, tags; job keywords JSON per `references/ats_keywords.md`, then `$DB normalize-keywords <abs $D>/07_job_keywords.json --write`, `$DB match`, `$DB gaps --jd <Co> --dir <abs $D>` | Opus | `01_jd.md`, `07_job_keywords.json` |
| 2 | `$DB pick --jd <Co> --dir <abs $D>` → `$DB align --jd <Co> --dir <abs $D> --picks "…"` → decide cards + bullets + leads; `$DB fit --picks "…"` passes; `$DB record … --headline "…" --country <C>` | Opus | pick string |
| 3 | `python3 SKILL_DIR/scripts/assemble_cv.py --jd <Co>` (recorded picks + headline + country; template + `$DB render`; refuses a line over the limit) | Opus | `03_cv.md` |
| 4 | Summary via `/multi-writer summary` | Opus + Codex, Fable judge | L5, `$D/mw/summary/` |
| 5 | Build + layout QC + external ATS check | script | `.docx`, `.pdf`, `06_layout.json`, `07_external_ats.{json,md}` |
| 6 | HR / HM / project-frame / lead review; ATS read from step 5 | Codex | `04_review.md` |
| 7 | Revise (1 round): review edits + `$DB sot-leads --jd <Co> --dir <abs $D> --per-bullet 2 --json` → hand-write duty-phrase leads for the bundles → user approves → `--write-from` → `init` → re-`pick`/`align` → step 3 → step 4 if cards/Skills changed → step 5 (incl. ATS re-run) | Opus | `09_sot_leads.json` |
| 8 | Report | Opus | `summary.md` (≤ 25 lines) |

**1 · Keywords** — `normalize-keywords` splits multi-concept phrases (one concept per term, JD
spelling, posting order, no duplicates; adds the unhyphenated form only when `jd.md` writes both).
Read its diff before `--write`; a split that changes meaning → fix the JSON by hand. Then `$DB match`
(each term → coverage + concept) and `$DB gaps --jd <Co> --dir <abs $D>` (JD concepts grouped
SoT-supported / gap / do not claim, with aliases + evidence). Unresolved terms → `$DB resolve "<term>"`
→ `$DB set-concept "<term>" <CODE>`; a new concept only if truly new (add it to the seed `concepts`, `init`).

**2 · Pick** — `$DB match` ranks bullets by concept score (JD weight × evidence strength × concept
idf + BM25; the literal hits stay alongside). `$DB pick --jd <Co> --dir <abs $D>` (deterministic
suggestion: archetype from `01_jd.md` → cards; bullets by concept score; lead = the variant naming a
concept the JD weights, then the closest duty phrase; variants polished so the most JD terms land on
the CV; 6 Skills rows by JD-term hits, JD spellings, items dropped until the row fits). Then
`$DB align --jd <Co> --dir <abs $D> --picks "…"`: per bullet × JD concept → `keep`/`use lead_ord n`
(apply), `spelling`, `summary` (→ Codex Summary hint: say the JD noun once), `NEW LEAD "…"` → show all
proposals to the user as one library-mode batch (approve/edit), append to the bullet's `leads` in the
seed, `$DB init`, re-run `align`. The writer decides: rank cards by the JD's lead responsibilities, cover the
top-5 criteria, map every bullet to a criterion + the duty phrase its lead mirrors (`$DB leads CODE`).
Never write leads or bodies here (a NEW LEAD goes into the library first, then is picked). Uncovered top-5 criterion → ask once → library mode or log as gap.
Missing **SoT-supported** ATS terms are handled in step 7 with `sot-leads` (after the first ATS run), not here.
`$DB fit --picks "…"` must PASS (bullets and card lines ≤ 2 lines, scope and Skills rows 1 line,
Delta block ≤ 19 lines; `tight` = within the model's error, check the PDF). Then `$DB record --jd <Co>
--company … --title … --dir <abs $D> --picks "…" --headline "…" --country <C>` and write the pick string
into `summary.md`.

**3 · Assemble** — `assemble_cv.py --jd <Co>` copies `$TPL` (CRLF), sets headline + contact, replaces
the Skills rows and the Delta block with `$DB render` output (verbatim) and keeps the current Summary
of `03_cv.md` (template line on the first run, until step 4). It runs `fit` first and writes nothing
when a line is over (`--force` only to inspect). `$DB diff --jd <Co>` must print `identical`.

**4 · Summary (multi-writer)** — `python3 .claude/skills/multi-writer/scripts/mw.py prompt --block summary
--app <abs $D> --out <abs $D>/mw/summary`, then the multi-writer steps 2–5 (Opus + Codex write, Fable judges
blind with HR/HM wording, merge only from the two texts). Insert the chosen `SUMMARY:` text; a length miss →
take the other writer's sentence, never hand-write new claims. Re-run step 4 when a card, bullet or claim
changes; a lead swap alone needs no re-run. (Experiment 2026-10-31: Opus 52/60, Codex 44, merge wins 3/3.)

**5 · Build**
```bash
bash SKILL_DIR/scripts/build_cv.sh patch --template-docx <abs cv/template/CV_template_v3.docx> \
  --template-md <abs $TPL> --md <abs $D>/03_cv.md --out <abs $D>/CV_<FirstLast>_<Co>.docx --pdf
bash SKILL_DIR/scripts/build_cv.sh check --pdf <abs pdf> --docx <abs docx> --md <abs $D>/03_cv.md \
  --pages 1 --json <abs $D>/06_layout.json
bash SKILL_DIR/scripts/build_cv.sh ats --dir <abs $D>          # → 07_external_ats.{json,md}
$DB ingest-ats --jd <Co> --dir <abs $D>                         # keyword map learns this run
$DB sot-leads --jd <Co> --dir <abs $D> --per-bullet 2            # which missing terms a lead could carry (step 7)
```
**ATS check** (vendored Resume-Matcher modules, `scripts/ext/resume_matcher/NOTICE.md`): parse
scores (PDF/DOCX extraction + layout, content) and keyword/skills/title/sections/dates score against
`07_job_keywords.json`. Targets: PDF parse ≥ 85, DOCX ≥ 90, every non-pass check explained (photo
and the Education/Certifications block are template matters); keyword match has no threshold —
instead every missing term is classified SoT-supported (→ fix wording, step 7) or gap (→ report).
`07_external_ats.md` groups the missing terms by library status (covered elsewhere → which bullet /
Skills item carries it · SoT-supported · gap · do not claim · unclassified; `[CODE]` = concept). `ingest-ats`
adds the run to the keyword map: found terms → covered; new terms go through `resolve` (a rule-1/2 hit
inherits the concept's claim, a fuzzy 3/4 hit at most SoT-supported); unresolved missing terms → `gap` +
`auto: unclassified` with candidate concepts printed → `$DB set-concept "<term>" <CODE>`; the writer
reclassifies coverage with `$DB set-coverage "<term>" gap|sot-supported|"do not claim" --note "…"`.
`patch` WARN → fix line. FAIL → shorter Summary (step 4) / headline / context line; 3-line bullet
or card line → other variant or library mode. Locked docx → `_preview`. v3 renders one page (95% with the
default cards); a spill to page 2 means a line must be shortened.

**6 · Review (Codex)** — fill `$PROMPTS` §Review (`{D}`, `{COMPANY}`, `{COUNTRY}`, `{INDUSTRY}`,
`{JD_TITLE}`), send verbatim. Save the reply (≤ 60 lines) as `04_review.md`. Codex reads
`07_external_ats.md` for the ATS section and classifies the missing terms; it never recounts.

**7 · Revise (Opus)** — apply truthful, in-diff edits only: card/bullet = `$LIB` ID swap, lead =
`leads:` variant, Skills/headline = `$LIB` items; unsupported keywords → gaps; missing must-have →
library mode. **SoT-supported missing ATS terms** go through `sot-leads`: `$DB sot-leads --jd <Co> --dir
<abs $D> --per-bullet 2 --json` lists, per picked bullet, the missing SoT terms its concepts prove and the
projected term count. Write one duty-phrase lead per bundle in the JD's spelling (never the mechanical
draft — "Reliable, reliability & implementation" is keyword stuffing; "Model selection for latency, cost &
reliability" is a lead), save as `<abs $D>/09_sot_leads.json` = `{"PS1": ["lead", …]}`, show the batch to the
user (library mode: candidate approves), then `$DB sot-leads … --write-from <abs $D>/09_sot_leads.json`
(refuses a lead that carries none of its bundle terms or is over the line limit) → `$DB init` → re-run
`pick`/`align` so the new lead ords are chosen → step 3. Expect +5–10 terms where the SoT bucket is large
(Scout24 32 → 41/70); if the bucket is ≤ 3 terms, skip it. No second review. Re-run step 5 (build + layout
+ ATS) and record before/after ATS numbers. Log applied/rejected edits (+ reason).

**8 · Report** — `$DB record --jd <Co> --company … --title … --dir <abs $D> --picks "…"` (approved
CV = the recorded picks; `$DB picks <Co>` replays them), then `summary.md`: cards + bullets with the
criterion/duty phrase each serves, HR/HM/ATS + verdict, edits, gaps, layout, paths. `$DB status`
prints the applications table (ATS, keywords, layout, HR/HM, verdict); `$DB notion-payload
applications --jd <Co>` gives the Notion page body (main agent creates it, then `$DB set-notion-id
applications <Co> <id>`). Reply 5–8 lines.

## Library mode — `/tailor-cv library [project]`
Trigger: new project, changed metric, new lead variant, or gap from step 2/7.
1. **Ask** (AskUserQuestion): what it is, who uses it, scale + business metric (→ context line);
   the capability the bullet should prove; user wording wins.
2. **Evidence**: `Skill("Notion:find", "<keywords>")` → fetch 2–5 pages; conflict with user metric → show both, ask.
3. **Draft**: card title variants (≤ 2), context ≤ 230 chars, 2–4 capability bullets ≤ 225 chars
   each with 2–3 `leads:` variants (JD duty phrases, 3–6 words), tags. Each bullet goes through
   `/multi-writer bullet` (`mw.py prompt --block bullet --app <abs $D> --card CODE --capability "…"`):
   Opus body + Codex verbatim JD lead, Fable-judged — the block where merging gains most (experiment 2026-10-31).
4. **Approve** → add the card / bullet / lead / skill / keyword rows to `cv/profile/library_seed.json`
   (codes stay stable), `$DB init` (keeps recorded picks + Notion ids), `$DB selftest`, `$DB export-md`;
   new statements → `$CONF` (dated, next ID). Mirror to Notion: `$DB notion-payload <table> --only-new` (tables incl. `concepts` → "Concepts" database)
   → `notion-create-pages` into the matching database, then `$DB set-notion-id <table> <code> <id>`.
