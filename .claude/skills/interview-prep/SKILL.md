---
name: interview-prep
description: Prepare for an interview from a finished application — 15–20 likely questions with spoken-English answers drawn only from the experience library (STAR behavioural from the two picked project cards, project deep-dives, technical and system-design on the JD's concepts, honest answers for the JD's gaps, motivation, and 3 questions to ask them), checked for truth against the cited bullets, Codex-reviewed, stored in the Q&A table. Questions candidates publicly report for the company (02_company.json interview_reports) are practised first. Mock mode runs a live interview in chat — one question at a time, follow-ups, rubric scores, truth-checked feedback. Use when the user says "interview prep", "面試準備", "準備面試", "mock interview", "模擬面試", "練習面試" or names a company/JD to prepare for.
---

# /interview-prep

**Glossary** — `$D` the job's tailor-cv dir (`$DB status` → Dir; holds `jd.md`, `01_jd.md`, `03_cv.md`,
`07_job_keywords.json`) · `$DB` `python3 .claude/skills/tailor-cv/scripts/library_db.py` · `$IP`
`python3 SKILL_DIR/scripts/interview_prep.py` · `$CONF` `cv/profile/candidate_confirmations.md` · `$PROFILE`
`cv/profile/candidate_profile.md` (TODO fields stay TODO) · `$MOCK` `python3 SKILL_DIR/scripts/mock.py` · `$RULES`
`.claude/skills/tailor-cv/references/writing_rules.md` · **Codex** = `Agent(subagent_type: "codex:codex-rescue")`,
prompt starts `--fresh`, review-only, no Notion. `cv/` is gitignored — never commit.

**Principles** — SoT only (`$DB` + `$CONF`); the candidate owns metrics; never invent a number, tool, team size or
incident detail. A fact missing from the library becomes a `TODO candidate:` in the answer and a line in the
report, not a guess. Gaps are answered honestly: what adjacent work was done, what is not done, how it would be learned.

## Steps
| # | Step | Owner | Output |
|---|---|---|---|
| 0 | Resolve the application; read `$PROFILE`, `$D/03_cv.md`, `$D/01_jd.md`, the company-research excerpt (if present), `$DB gaps --jd <Co>` | Opus | `$D`, JD code |
| 1 | `$IP pack --jd <Co>` | script | context pack (hard cap 12,500 chars; ~10–12k typical) |
| 2 | Write 15–20 Q&A (+3 asks) | Opus | `$D/10_interview.json` |
| 3 | `$IP check` → fix → `$IP render` | script | `$D/10_interview.md` |
| 4 | Truth + realism review (`references/codex_prompt.md`) | Codex | fixes, one revision |
| 5 | `$DB qa import --update $D/10_interview.json`; report | Opus | DB rows, summary |
| 6 | After the interview: record real questions and outcomes | Opus | `qa add --source asked`, `qa set` |
| M | Mock interview (any time after step 5) | Opus as interviewer + `$MOCK` | `$D/12_mock/mock_*.json` + `.md` |

**0 · Resolve** — `$DB status` gives JD code and `$D`; a JD named by the user (company, role, URL) is matched to
a row. No row (job never ran `/tailor-cv`) → stop and say so; interview prep needs the submitted CV and picks.
Read `$PROFILE` if present (missing → motivation and logistics answers carry TODOs), `03_cv.md`, `01_jd.md`,
`$DB gaps --jd <Co>`, and the company-research excerpt (`company_research.py excerpt --jd <Co>`, also printed in the
pack, with each hook's `on CV:` / `use:` flags), never `02_company.md` itself, whose candidate lines hold library facts
not on the CV. No `02_company.json` → suggest `/company-research <Co>` first, then go on with `jd.md` alone. A
`02_company.json` without `interview_reports` (researched before 2026-11-03) → offer to run company-research step 2
row 7 (public interview reports) first; they are the best realism signal the prep can have.

**1 · Pack** — `$IP pack --jd <Co>` prints: JD brief and company paragraph · the Delta block as submitted · each
picked bullet (code, lead, body, metric, CONF) · the company-research excerpt (`02_company.json`: snapshot, hooks with `on CV:` / `use:` flags, gap notes, questions, news) when present; a hook marked `on CV: no` may feed a motivation answer, but its candidate half must still come from cited bullets · picked cards' metrics · the JD's required and preferred concepts
with covering bullets · the gap terms (gap, do-not-claim, term not claimable) · existing `qa list` rows · profile
sections (identity, countries/relocation/visa, languages, compensation, rules). Each section has its own budget.
Use only these facts, plus the CONF items you cite in `notes` (e.g. `CONF-o2`).

**2 · Write** — JSON list; each object: `jd, company, question, kind, concepts, bullets, answer, source
("generated"), outcome (""), notes`. Mix:

| Part | n | kind / notes | Source of facts |
|---|---|---|---|
| Opening | 2 | `intro`: (1) "Tell me about yourself." — Present → Past → Future, 90–150 words: current role and what you own, 2 proof points, why this role next; (2) "Walk me through a technical project you are proud of." — STAR on the strongest picked card | `03_cv.md` (headline, profile, roles), the picked cards' bullets, `$PROFILE` |
| Behavioural | 3–5 | `behavioural`, STAR | the two picked cards' bullets |
| Project deep-dive | 3–5 | `project` | one per picked card + strongest bullets: why this design, trade-off, failure, metric definition |
| Technical | 2–4 | `technical` | the JD's required concepts the CV covers |
| System design | 1–3 | `system-design` | shaped like the JD's product, built from picked bullets |
| Gaps | 2–5 | `gap` | `gaps --jd` terms (integration/performance tests, Vue/React, NoSQL, edge, cloud…): name the term, say plainly it is missing, give adjacent work, how it would be learned; never claim it. Claimable neighbours (PyTest unit tests, TypeScript, MinIO) go in the same answer |
| Motivation / company / logistics | 2–4 | `motivation` | `jd.md` + `$PROFILE` + the excerpt's hooks; why this company (the hook's company fact + its cited bullets, listed in `bullets`), why now, location / start date / salary. Answer in the candidate's voice; anything not on file → `TODO candidate:`, never coaching |
| Questions to ask them | 3 | `ask`; `answer` = why ask / what to listen for | the excerpt's `ask:` questions first (keep their `why` from `02_company.json` as what to listen for), then JD gaps and role scope |

**Reported questions first** — every `reported R#` line in the pack (questions candidates say they were asked, from
`interview_reports`) becomes one item before the generic ones: keep the reported wording (light paraphrase only), pick
the kind it fits, set `reported: "R2"` (several reports of one question share an item), and let it count toward that
kind's quota. More reports than the mix allows → newest and closest role first; say in the report which were left out.
A report is never evidence: the answer still comes only from cited bullets, CONF and `$PROFILE`. A report about a
take-home, coding test or a stage the library cannot answer (e.g. live LeetCode) becomes a `notes` line ("expect a
coding round: practise …") on the closest item, not an invented answer. `check` errors on an unknown `R#` and warns for
every report no item practises.

Answer rules: first person, spoken English, **60–150 words**; STAR (Situation, Task, Action, Result) for
behavioural; every factual claim and number appears in a cited bullet body/metric, the card context, or a CONF
item, and the bullet is listed in `bullets` (comma codes); `concepts` = concept codes the question targets
(`$DB concepts`); apply `$RULES` (no "identical output", staging ≠ production, cloud = API consumer only,
MaterialCheck: only phase 1 live, no "DINO"/"Claude" detail where the card forbids it). Do not add before/after metrics
or reasons the library does not hold: no invented motives, causes, baselines or self-characterisations. A metric
whose baseline or hardware is unknown (e.g. 10× throughput, 3 s per image) gets `TODO candidate: baseline …` in the answer.
MaterialCheck (CONF-an, 2026-11-02, supersedes CONF-o5–6): say "phase 1 delivered on time and in production, used by 200+
NPE and engineers in Germany, China and Taiwan, 30 → 5 minutes per document; phases 2–3 are the plan". Never imply
phases 2–3 shipped. Do not tell the same
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
`$D/10_interview.md` path, and the reported questions practised / left out.

**6 · After the interview** — add each real question: `$DB qa add --jd <Co> --source asked --question "…" --kind …
--answer "…"`; record how it went: `$DB qa set <id> --outcome "…"` (what landed, what did not). `qa list --jd <Co> --full`
shows ids. Prep rows (`source=generated`) stay as the starting point for the next application.

## Mock mode
Trigger: "mock interview", "模擬面試", "練習面試", "考我". Needs `$D/10_interview.json` (no file → run steps 0–5 first).
The agent is the interviewer; the candidate answers in chat. Questions in English; feedback in the candidate's chat
language (繁中), stronger answers in English.

1. `$MOCK start --jd <Co> [--stage mixed|hr|tech|hm] [--n 5] [--kinds …] [--followups 2]` → session path + brief.
   Selection: reported questions first, then never-practised ones, then the lowest previous score; kinds round-robin
   (`hr` motivation/behavioural/gap · `tech` technical/system-design/project/gap · `hm` project/behavioural/system-design/
   gap/motivation). Read the brief silently: it holds the reference answers and the evidence; never show or paraphrase a
   reference answer before that question is scored. Ask the user once which stage if they did not say (default mixed).
2. Per question: ask it as the interviewer would (one line of context at most, no hints) and wait. Then up to
   `--followups` follow-ups, **one at a time**, each aimed at the weakest spot of the last answer: a vague "we", a
   number without a definition, a claim beyond the cited evidence, a gap term said as if done, a missing result. Use
   the brief's follow-up seeds, filled with the candidate's own words. Never a follow-up that tests a gap term as if the
   candidate should know it; ask how they would close the gap instead.
3. Write `{"answer", "followups":[{"q","a"}]}` to the scratchpad and run `$MOCK answer <session> --q N --file …`. Read the
   auto checks: UNVERIFIED number (in no bullet, card, CONF item or the CV), GAP CLAIM, TRAP, concepts missed, length,
   I/we.
4. Score the six rubric dimensions 1–5 (structure, evidence, relevance, honesty, clarity, followups; omit followups
   if none were asked). An unverified number, gap claim or trap caps honesty at 3 unless `honesty_note` says why (e.g.
   the candidate confirms the number is real and not yet in the library). Write `{"scores", "feedback", "better",
   "better_bullets", "notes", "honesty_note"}`: feedback = 2–3 concrete fixes; `better` = the candidate's own answer
   improved, 60–180 words, using only the cited bullets (+ `better_bullets`, CONF in `notes`) — it is truth-checked like
   step 3, so start from the reference answer and keep what the candidate did well. Run `$MOCK score …` until it passes.
5. Show the candidate: scores (one line), the fixes, the stronger version. Then the next question. Commands from the
   candidate: `skip` (score nothing, next), `hint` (name the evidence codes and the concept, not the answer), `again`
   (re-answer the same question; a new `answer` clears the old score), `stop` (go to 6).
6. `$MOCK report <session>` → `<session>.md`. Tell the candidate: average, weakest dimension, questions to practise again,
   and **Claims to check**: a number or fact they said that the library lacks. Ask whether it is true; if yes, propose
   adding it through tailor-cv library mode or a CONF item — never add it without their explicit approval.
   `$MOCK history --jd <Co>` shows progress across sessions. `$MOCK selftest` runs the end-to-end checks.
A real question asked in the mock that the prep lacks may be added as `qa add --source self` after the candidate agrees.
**Frameworks and chunks** — every question has an answer framework (`mock.py FRAMEWORKS`): intro "Tell me about
yourself" → Present → Past → Future; project walk-through, behavioural, project → STAR (project may use CAR);
technical, motivation → PREP; system design → Requirements → Design → Trade-offs → Operate; gap → Honest gap →
Closest experience → Plan. Before answering, the candidate may ask for the skeleton (labels + what each chunk must do,
never the content). The score maps the answer onto the chunks: `framework` + `chunks[{label, yours, tip, better}]`
(yours/tip in 繁中, better in English, labels in framework order); the joined `better` texts are the stronger answer
and are truth-checked. Stages `hr`, `mixed`, `hm` open with the `intro` items ("Tell me about yourself" always first in
`hr`).
**Dashboard page** — `tools/cv-dashboard` → 模擬面試 (`#/mock`, `server/mock.ts`) runs the same loop in the browser:
typed or spoken answers (Web Speech API, en-US/en-GB/zh-TW), questions read aloud, follow-ups from a fast model
(`MOCK_FOLLOWUP_MODEL`, default sonnet), scores from `MOCK_SCORE_MODEL` (default opus) through `$MOCK answer/score`
(a refused score is retried up to 3 times with the checker's errors). Model calls run with no tools, no MCP and no
settings. Reference answers reach the browser only after a question is scored or skipped. Sessions are the same
`$D/12_mock/` files, so `$MOCK report/history` and chat mode see them.
