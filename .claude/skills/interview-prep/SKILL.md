---
name: interview-prep
description: Prepare for an interview from a finished application — 15–20 likely questions with spoken-English answers drawn only from the experience library (STAR behavioural from the two picked project cards, project deep-dives, technical and system-design on the JD's concepts, honest answers for the JD's gaps, motivation, and 3 questions to ask them), checked for truth against the cited bullets, Codex-reviewed, stored in the Q&A table. Use when the user says "interview prep", "面試準備", "準備面試", "mock interview" or names a company/JD to prepare for.
---

# /interview-prep

**Glossary** — `$D` the job's tailor-cv dir (`$DB status` → Dir; holds `jd.md`, `01_jd.md`, `03_cv.md`,
`07_job_keywords.json`) · `$DB` `python3 .claude/skills/tailor-cv/scripts/library_db.py` · `$IP`
`python3 SKILL_DIR/scripts/interview_prep.py` · `$CONF` `cv/profile/candidate_confirmations.md` · `$PROFILE`
`cv/profile/candidate_profile.md` (TODO fields stay TODO) · `$RULES`
`.claude/skills/tailor-cv/references/writing_rules.md` · **Codex** = `Agent(subagent_type: "codex:codex-rescue")`,
prompt starts `--fresh`, review-only, no Notion. `cv/` is gitignored — never commit.

**Principles** — SoT only (`$DB` + `$CONF`); the candidate owns metrics; never invent a number, tool, team size or
incident detail. A fact missing from the library becomes a `TODO candidate:` in the answer and a line in the
report, not a guess. Gaps are answered honestly: what adjacent work was done, what is not done, how it would be learned.

## Steps
| # | Step | Owner | Output |
|---|---|---|---|
| 0 | Resolve the application; read `$PROFILE`, `$D/03_cv.md`, `$D/01_jd.md`, the company-research excerpt (if present), `$DB gaps --jd <Co>` | Opus | `$D`, JD code |
| 1 | `$IP pack --jd <Co>` | script | context pack (hard cap 12,000 chars; ~9k typical) |
| 2 | Write 15–20 Q&A (+3 asks) | Opus | `$D/10_interview.json` |
| 3 | `$IP check` → fix → `$IP render` | script | `$D/10_interview.md` |
| 4 | Truth + realism review (`references/codex_prompt.md`) | Codex | fixes, one revision |
| 5 | `$DB qa import --update $D/10_interview.json`; report | Opus | DB rows, summary |
| 6 | After the interview: record real questions and outcomes | Opus | `qa add --source asked`, `qa set` |

**0 · Resolve** — `$DB status` gives JD code and `$D`; a JD named by the user (company, role, URL) is matched to
a row. No row (job never ran `/tailor-cv`) → stop and say so; interview prep needs the submitted CV and picks.
Read `$PROFILE` if present (missing → motivation and logistics answers carry TODOs), `03_cv.md`, `01_jd.md`,
`$DB gaps --jd <Co>`, and the company-research excerpt (`company_research.py excerpt --jd <Co>`, also printed in the
pack, with each hook's `on CV:` / `use:` flags), never `02_company.md` itself, whose candidate lines hold library facts
not on the CV. No `02_company.json` → suggest `/company-research <Co>` first, then go on with `jd.md` alone.

**1 · Pack** — `$IP pack --jd <Co>` prints: JD brief and company paragraph · the Delta block as submitted · each
picked bullet (code, lead, body, metric, CONF) · the company-research excerpt (`02_company.json`: snapshot, hooks with `on CV:` / `use:` flags, gap notes, questions, news) when present; a hook marked `on CV: no` may feed a motivation answer, but its candidate half must still come from cited bullets · picked cards' metrics · the JD's required and preferred concepts
with covering bullets · the gap terms (gap, do-not-claim, term not claimable) · existing `qa list` rows · profile
sections (identity, countries/relocation/visa, languages, compensation, rules). Each section has its own budget.
Use only these facts, plus the CONF items you cite in `notes` (e.g. `CONF-o2`).

**2 · Write** — JSON list; each object: `jd, company, question, kind, concepts, bullets, answer, source
("generated"), outcome (""), notes`. Mix:

| Part | n | kind / notes | Source of facts |
|---|---|---|---|
| Behavioural | 3–5 | `behavioural`, STAR | the two picked cards' bullets |
| Project deep-dive | 3–5 | `project` | one per picked card + strongest bullets: why this design, trade-off, failure, metric definition |
| Technical | 2–4 | `technical` | the JD's required concepts the CV covers |
| System design | 1–3 | `system-design` | shaped like the JD's product, built from picked bullets |
| Gaps | 2–5 | `gap` | `gaps --jd` terms (integration/performance tests, Vue/React, NoSQL, edge, cloud…): name the term, say plainly it is missing, give adjacent work, how it would be learned; never claim it. Claimable neighbours (PyTest unit tests, TypeScript, MinIO) go in the same answer |
| Motivation / company / logistics | 2–4 | `motivation` | `jd.md` + `$PROFILE` + the excerpt's hooks; why this company (the hook's company fact + its cited bullets, listed in `bullets`), why now, location / start date / salary. Answer in the candidate's voice; anything not on file → `TODO candidate:`, never coaching |
| Questions to ask them | 3 | `ask`; `answer` = why ask / what to listen for | the excerpt's `ask:` questions first (keep their `why` from `02_company.json` as what to listen for), then JD gaps and role scope |

Answer rules: first person, spoken English, **60–150 words**; STAR (Situation, Task, Action, Result) for
behavioural; every factual claim and number appears in a cited bullet body/metric, the card context, or a CONF
item, and the bullet is listed in `bullets` (comma codes); `concepts` = concept codes the question targets
(`$DB concepts`); apply `$RULES` (no "identical output", staging ≠ production, cloud = API consumer only,
MaterialCheck is a POC, no "DINO"/"Claude" detail where the card forbids it). Do not add before/after metrics
or reasons the library does not hold: no invented motives, causes, baselines or self-characterisations. A metric
whose baseline or hardware is unknown (e.g. 10× throughput, 3 s per image) gets `TODO candidate: baseline …` in the answer.
MaterialCheck is a POC: say "phase 1, a proof of concept, delivered on time; phases 2–3 are the plan". Its "200+" is
**target users** only: write it only with `CONF-o6` in `notes` and the POC qualifier ("200+ target users; 30 → 5 minutes
per document measured in POC testing"), never "used by 200+". Do not tell the same
story in two questions.

**3 · Check** — `$IP check $D/10_interview.json` (exit 1 on error): schema and kinds; mix per group vs target
(warn if off by more than 1); concept and bullet codes exist; answer length; behavioural/project answers cite a
bullet; `gap` answers must name one of the gap terms from `gaps --jd` and say plainly the experience is missing;
**truth check**: every number-like token in an answer (digits, %, ×, €, k/m/b suffix) must appear **with the same
unit** in the cited bullets' body/metric/constraints, their cards' contexts/metrics, the CV line carrying that
bullet's lead, or a CONF item cited in `notes` (motivation and ask answers may also use `jd.md` and the profile). A number found only in the company facts
(`company_text()`: snapshot, product team, news, hook company facts; never `02_company.md`) passes in a motivation or
ask answer only when its sentence has no first-person reference (I/my/we/our), names the company or a product, and
shares a context word with the company fact next to the number ("51% stake" never becomes "51% fewer errors").
`truth.company_number_ok`; `check --selftest` runs the cases.
Ranges expand ("84–89%" gives 84% and 89%), so "84" bare does not match "84%". Number words (two … hundred, half,
double, twice) are mapped to digits and only warned about. Offenders are printed with the answer number. Fix and re-run until `RESULT: OK`, then
`$IP render $D/10_interview.json` → `$D/10_interview.md` (grouped by kind, evidence codes shown).

**4 · Codex review** — read `jd.md`, `03_cv.md`, `10_interview.json`, the cited bullets (`$DB bullet CODE`),
`$CONF`. Two questions: (a) **truth** — any claim not provable from the CV or library evidence, any gap answered
as if covered; (b) **interviewer realism** — would a hiring manager ask these, are the deep-dives the ones a
reader of this CV would probe, is any answer evasive or over-long. Output: numbered findings with the question
number and a fix. Apply once, re-run step 3.

**5 · Store + report** — `$DB qa import $D/10_interview.json` (skips duplicates of the same jd + question).
Report in ≤ 12 lines: counts per kind, check result, Codex findings fixed, the `TODO candidate:` items, and the
`$D/10_interview.md` path.

**6 · After the interview** — add each real question: `$DB qa add --jd <Co> --source asked --question "…" --kind …
--answer "…"`; record how it went: `$DB qa set <id> --outcome "…"` (what landed, what did not). `qa list --jd <Co> --full`
shows ids. Prep rows (`source=generated`) stay as the starting point for the next application.
