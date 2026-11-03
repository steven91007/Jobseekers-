# Experience library DB — v2 addendum (2026-10-31)

Lessons from the first multi-JD run (TOMRA + Scout24, Mapbox, Taktile, Ixopay, AIVenture; see each
`cv/applications/*/v3_cards/summary.md`). Everything below extends `library_db.py`,
`assemble_cv.py` and `ats_check.py` in `.claude/skills/tailor-cv/scripts/`; the v1 spec
(`library_db_spec.md`) stays valid. Stdlib only, WSL `python3` 3.12. Do not change the existing
CLI semantics or the seed's existing records; add.

## 1. `normalize-keywords <07_job_keywords.json> [--write]`
Problem: the JD-analysis agent emits multi-concept phrases ("reliable, scalable applications in
production", "implementation, evaluation, deployment, and operation", "system-design"), which no CV
can match literally. Rule from `references/ats_keywords.md`: one concept per term, JD spelling.
- Split on `, ` and ` and ` / ` or ` when the phrase has ≥ 3 words and the parts are nouns/noun
  phrases (heuristic is fine: split, strip, drop parts ≤ 2 chars or in a small stop list).
- Keep the original phrase too when it is ≤ 3 words (e.g. "tool calling" stays; "prompt and
  context engineering" → "prompt engineering", "context engineering").
- Hyphen variants: add the unhyphenated form as a second term only when the JD itself uses both.
- Dedupe across required/preferred/keywords (first occurrence wins), keep posting order.
- Print a diff (before → after per field) and the new count; `--write` saves in place and appends
  to `_note`. Target: Scout24's raw file (33/11/16 = 60) normalises to ≥ 70 single-concept terms.

## 2. `ingest-ats --jd <Co> --dir <abs $D>`
After `build_cv.sh ats`, pull the run into the keyword map so the next JD benefits:
- Read `07_job_keywords.json` (terms, types: required/preferred/keyword) and
  `07_external_ats.json` (`keyword_match.found/missing`, `repeated_terms`).
- Upsert `keywords` rows: term (JD spelling), type, `freq` from repeated_terms, add `<Co>` to
  `keyword_jds`. Coverage: `covered` if in found; if missing and the row already exists keep its
  coverage; else set coverage `gap` and `notes = "auto: unclassified (<Co> <date>)"` so the writer
  can reclassify later (`set-coverage <term> <coverage> [--note]` — new tiny subcommand).
- Link covered terms to bullets whose `keywords_carried` contains the term (case-insensitive) and
  to skills whose name or spelling variants contain it.
- Print: new terms, updated terms, unclassified count.

## 3. `fit --picks "…"` (also `--jd <Co>`) — pre-build length check
Render the picks and report, per line: rendered chars and a verdict against the template's
measured limits — bullet ≤ 225 (lead + " — " + body + 4), card title line ≤ 230, scope ≤ 120,
Skills row ≤ 118 (label + items). Exit 1 when any line is over. Known regression to catch:
`bullet=PS1:7` ("LLM-powered applications in production") renders 3 lines. `assemble_cv.py`
calls this automatically and refuses to write when a line is over unless `--force`.

## 4. `pick --jd <Co> --dir <abs $D> [--archetype X] [--cards 2|3]` — suggestion engine
Deterministic helper (the writer still decides). Inputs: `01_jd.md` (archetype line, duty
phrases line), `07_job_keywords.json`, the DB.
- Cards: archetype map (seed `_meta` or a constant): MLOps/platform → VI,PS · computer vision →
  VI,PS · agentic/LLM → DT,MC,PS · backend/automation → PS,DT,VI · full-stack → PS,DT,MC.
- Bullets per card: rank by number of JD terms in `keywords_carried` + body (whole-word), then
  by status (Production > POC > Legacy), max 3 per card, 6 total, never DT2a+DT2b together, never
  two bullets with the same body.
- Lead per bullet: the `leads` variant with the highest token overlap with any duty phrase from
  `01_jd.md` line 4 (lower-cased, stop words removed); tie → ord 0.
- Context per card: ord 0 unless the fitted page budget needs the 1-line variant (use `fit`).
- Skills rows: the 6 rows whose items/spellings hit the most JD terms; pick the spelling variant
  that matches a JD term; drop items until the row ≤ 118 chars; keep the row label variant whose
  words appear in the JD (e.g. "Machine learning & computer vision" when the JD says machine learning).
- Output: a pick string, a per-line rationale (which JD terms each bullet/lead/row serves), and
  the `fit` report. Must produce a valid, fitting pick string for all six recorded JDs.

## 5. `status` — the applications table
For every row in `applications`: company, title, dir, ATS overall + keyword n/N
(`07_external_ats.json`), layout status + utilisation (`06_layout.json`), HR/HM (first
`HR nn` / `HM nn` in `04_review.md`), verdict, date. Markdown table, sorted by ATS desc.

## 6. `applications` schema additions + `assemble_cv.py --jd`
Add `headline`, `country`, `summary_chars`, `notion_id` to `applications`; `record` accepts
`--headline` / `--country`. `assemble_cv.py --jd <Co>` then needs no other arguments (reads
picks, headline, country from the DB; keeps the current Summary line of `03_cv.md` if present).

## 7. `notion-payload applications [--jd <Co>]`
Emit, per application, the Markdown body used for the TOMRA Notion page (status callout, CV
text converted from `03_cv.md` — section headings, bullets, role lines with dates — picks string,
bullet mentions by `notion_id`, file list) so the main agent can create the page with the Notion
tool. Keep it under 8 k characters.

## 8. `ats_check.py` — DB-aware classification
When `cv/profile/experience.sqlite` exists, append to `07_external_ats.md` a section
"Missing terms by library status": covered-elsewhere (term is `covered` for another JD → list the
bullets/skills that carry it), SoT-supported wording, gap, do-not-claim, unclassified. No change
to the score.

## 9. Docs
- `SKILL.md`: step 1 adds `normalize-keywords`; step 2 uses `pick` then `fit`; step 3 uses
  `assemble_cv.py --jd`; step 5 adds `ingest-ats` after `ats`; step 8 uses `status`.
- `references/ats_keywords.md`: make "one concept per term" a hard rule with three split examples
  taken from the Scout24 raw file.

## Acceptance (extend `selftest`)
1. v1 checks still pass.
2. `normalize-keywords` on a copy of the Scout24 raw JSON (keep a fixture under
   `.claude/skills/tailor-cv/scripts/fixtures/scout24_keywords_raw.json`, created from the
   current file by reversing the documented splits, or simply a hand-written 10-term sample with
   3 multi-concept phrases) yields only single-concept terms, no duplicates, posting order kept.
3. `fit --picks "scope=SCOPE;card=PS:1:0;bullet=PS1:7"` exits 1 and names PS1.
4. `pick --jd TOMRA` and `pick --jd Scout24` each return a pick string that `fit` accepts and
   `render` renders with 5–6 bullets.
5. `ingest-ats` on TOMRA then `keywords --jd TOMRA` lists 60 terms with 43 covered.
6. `status` prints six rows with the ATS numbers currently in each `07_external_ats.json`.

## Non-goals
No Notion API calls. No edits to any `cv/applications/*/03_cv.md`, PDF or DOCX. No change to
`bullet_library.md`, `candidate_confirmations.md`, `build_cv.py`, `layout_check.py`, the template
files or the seed's existing values. Do not commit.
