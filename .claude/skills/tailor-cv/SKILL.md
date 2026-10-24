---
name: tailor-cv
description: Tailor the user's CV to a job description through a fixed multi-model pipeline with an HR & Hiring Manager Review Gate — Fable analyses the JD, Opus builds an evidence ledger from Notion and writes the Delta bullets + Skills, Codex runs recruiter / ATS / hiring-manager / interview-risk gates with a weighted job-fit score, Opus revises, Codex writes the Profile last, then DOCX + PDF are built, layout-checked and released only after a final gate. Use whenever the user gives a JD (URL, file, or pasted text) and wants a tailored CV, or says "tailor my cv", "adjust cv for this job", "客製化履歷".
argument-hint: "<JD url | path to jd.md | pasted JD text> [--company X] [--role Y] [--max-rounds 2] [--threshold 85] [--no-pdf]"
---

# /tailor-cv — JD → evidence-supported tailored CV

You are the **orchestrator**. Follow the phases in order. The objective is not a
better-written CV but an **evidence-supported, job-relevant CV that survives recruiter
screening, hiring-manager review and technical interview scrutiny**. Never let a phase
invent facts, and never release a CV before the final gate has run.

```
Phase 0  Setup              main thread   inputs, folder, template, Notion check
Phase 1  JD analysis        Fable         01_jd_analysis.md
Phase 2  Evidence + draft   agent team    lead Opus → 1 Opus investigator per Notion project
                                          (reads its records itself) → lead writes
                                          02_evidence_map.md, 03_cv.md (Delta + Skills), 07_log
Phase 3  Review gate        Codex         04_review_rN.md — Gates A–D, claim audit,
         (loop review)                    weighted score; review_tools.py gate     ─┐
Phase 4  Revise             Opus          bullets + Skills, log entries             ─┘ ≤ 2 passes
Phase 4a Second read        Fable         independent fact + English check of bullets/Skills
Phase 4b Profile (last)     Codex         Profile paragraph from final bullets + JD (+ Fable read)
Phase 5  Build + layout QC  main thread   .docx, .pdf, 06_layout.json (build_cv.py check)
Phase 6  Final gate         Codex         05_final_gate.md — whole CV vs first draft;
                                          review_tools.py recommend
Phase 7  Reports            main thread   08_hr_review_report.md, summary.md
```

Defaults: `--threshold 85`, `--max-rounds 2` (= up to two substantive revision passes),
PDF on.

**Editable scope (default):** the **Profile/Summary** (written last by Codex), the
**Delta work-experience bullets** (max 5; heading fixed) and the **Technical Skills
rows** (text only; same row count; single-column). Every other line — header, other
roles, projects, education, certifications, languages — is copied verbatim from
`cv/template/CV_template.md`. `--scope` can add sections; it never moves the Profile
before Phase 4b.

**References** (read the ones each phase names; do not paste them into prompts — pass
paths): `references/evidence_rules.md` (claim statuses, measurement integrity),
`references/review_rubric.md` (the HR & Hiring Manager Review Gate and its output
format), `references/cv_writing_rules.md` (voice, bullet structure, length, format).

**Scripts:** `scripts/build_cv.sh` → `build_cv.py extract | build | patch | check`
(Windows venv: python-docx, docx2pdf, PyMuPDF); `scripts/review_tools.py gate | ledger |
compare | recommend` (stdlib; run with `python3 -I`); tests in `scripts/tests/`.

Paths below are relative to the project root. `SKILL_DIR` = `.claude/skills/tailor-cv`.

---

## Phase 0 — Setup (main thread)

1. **Parse arguments.** The JD is a URL, an existing file path, or pasted text.
   Extract `--company`, `--role`, `--max-rounds`, `--threshold`, `--no-pdf`, `--scope`.
2. **Fetch the JD.**
   - URL → `WebFetch` the page (LinkedIn: "About the job"; SmartRecruiters: the public
     API `https://api.smartrecruiters.com/v1/companies/<co>/postings/<id>` returns the
     full text). If blocked or truncated, ask the user to paste it.
   - File → read it. Pasted text → use as-is.
   Infer company and role from the JD if not given by flags.
3. **Create the application folder** `cv/applications/<YYYYMMDD>_<Company>_<Role>/`
   (ASCII, spaces → `-`). Write `jd.md` using `cv/applications/_jd_template.md`
   (metadata + raw JD, unedited). Re-runs for the same JD reuse the folder.
4. **Locate the base template** in `cv/template/`:
   - Prefer `CV_template.md`. If only a `.docx` exists, or the `.docx` is newer than the
     `.md`, run `bash SKILL_DIR/scripts/build_cv.sh extract --docx <abs .docx> --out <abs CV_template.md>`
     and tell the user the Markdown twin was (re)created.
   - If neither exists, stop and ask the user to drop their CV into `cv/template/`.
5. **Check Notion.** `mcp__notion__notion-search` with a cheap query. On an auth error,
   stop and ask the user to run `/mcp`, then re-run the skill.
6. **Baseline layout**: build the untouched template once to a scratch path and run
   `build_cv.sh check` on it, so Phase 5 knows the expected page count and utilisation.

## Phase 1 — JD analysis (Fable)

Run **inline** if the session model is Fable (`claude-fable-*`); otherwise spawn
`Agent(subagent_type: "general-purpose", model: "fable")` with this brief and the paths.

Produce `01_jd_analysis.md`:

```markdown
# JD analysis — <Company> · <Role>

## Snapshot
- Title / seniority / team · Location / remote / JD language · Domain & product
- Why this role exists (1–2 lines)
- Role archetype: Python platform | computer vision | agentic AI | MLOps | other (one
  primary, optional secondary) — drives which projects the CV should emphasise

## Requirements
| ID | Requirement (JD wording) | Class | Weight | Keyword(s) | Explicit? |
|----|--------------------------|-------|--------|------------|-----------|
| R1 | ... | must-tech | must | ... | explicit "required" / implied |
Classes: must-tech · nice-tech · prod-eng (production software engineering) ·
ownership (senior ownership & architectural decisions) · collab (stakeholder &
cross-functional) · logistics (work authorization, relocation, language, working mode).
`must` = explicitly required or clearly central; `nice` = plus / bonus / ideally /
familiarity / willingness to learn. Distinguish explicit mandates from preferences.

## Hard requirements
- Genuine gates only (degree, language level, work permit, on-site location,
  mandatory certification) — each with the JD wording. Kept apart from the score.

## Top 5 selection criteria
1. ... — why: where the JD puts emphasis (position, repetition, "primary focus"
   wording), not keyword frequency alone
...

## Responsibilities → what the CV must demonstrate
## Company context (WebSearch, max 3 searches; cite URLs)
## Tone & phrasing notes
## Flags (JD vs template language, visa / relocation, stale text, application package)
```

Use `WebSearch` (standard) for company context only; never research the candidate.

## Phase 2 — Evidence ledger + draft (agent team: lead → Opus investigators)

**Default — agent team via nested agents.** Spawn **one** lead:
`Agent(subagent_type: "general-purpose", model: "opus")`, briefed with the folder, the
skill path and this team structure (subagents can spawn their own subagents):
1. **Lead (Opus) — inventory:** reads the JD analysis, template and `cv/profile/`, then
   maps the candidate's distinct Notion projects (KB hubs) that can evidence ≥ 1 R#.
2. **One investigator per project** — the lead spawns them in parallel
   (`general-purpose`, model `opus`). Each investigator does the research **itself — it
   does not delegate**: it explores its project, picks the records worth reading (≤ 25,
   noting how many exist and what it left out), reads each one, and records per fact a
   verbatim quote (≤ 200 chars, original language), numbers with baseline / sample /
   conditions / environment, executed vs configured, and the environment. It then
   synthesises its project into conservative candidate claims with statuses, sources,
   measurement scope, conflicts, gaps and questions, and returns that plus the number
   of records read to the lead.
3. **Lead — synthesis:** merges all investigations and performs Steps A–C below
   (evidence map incl. records read per project, draft, improvement log, `ledger --cv`
   until clean), then replies with projects + records read, bullet leads, gaps,
   weak claims and questions for the candidate.

**Alternative — deterministic workflow** (same structure, script-dispatched,
resumable): `Workflow({ name: "tailor-cv-evidence", args: { dir: "cv/applications/<folder>",
maxRecordsPerProject: 25 } })` (`.claude/workflows/tailor-cv-evidence.js`). Use it when the
user asks for a workflow or when nested agent spawning is unavailable.

**Notion lookups — use `/notion find`.** Every agent in Phase 2 locates pages and
databases with the **Notion:find** skill (`Skill(skill: "Notion:find", args: "<title
keywords>")`) — e.g. project names, "KB", "Commits", "Decisions", ADR titles, release
names — and then reads the pages it found with the Notion fetch tool. Use the raw
Notion search tool only when `/notion find` returns nothing for a needed lookup, and say
so in the findings.

Prerequisite for both: Notion must be connected in this session. Candidate confirmations
live in `cv/profile/candidate_confirmations.md` so every application reuses them; add new
ones there with the date. Check the lead's result (projects covered, records read per
project) before Phase 3.

Steps A–C (the specification the coordinator follows):

> You are building an evidence-supported tailored CV. Read, in order:
> `<dir>/01_jd_analysis.md`, `cv/template/CV_template.md`, everything in `cv/profile/`,
> `SKILL_DIR/references/evidence_rules.md`, `SKILL_DIR/references/cv_writing_rules.md`.
>
> **Step A — survey evidence.** For each R#, find pages with `/notion find`
> (`Skill(skill: "Notion:find", args: "<keywords>")`), then read them with the Notion fetch tool for the candidate's real work: implementation
> records, technical decisions (ADRs), experiments, benchmarks, release notes. Use
> several focused queries (tool, project and employer names, R# keywords, Chinese
> equivalents). Write `<dir>/02_evidence_map.md` with:
> - `## Requirement map` — | R# | Weight | Evidence (1 line, numbers) | Source | Strength (strong/partial/none) |
> - `## Claim ledger` — one row per claim you will use, in the format and with the
>   statuses of `evidence_rules.md`, including **measurement scope** for every number
>   (baseline · sample · conditions · environment; `unknown` if unknown). Apply the
>   measurement-integrity rules while classifying (component ≠ pipeline, similar ≠
>   identical, configured ≠ executed, staging ≠ production, small samples, percent vs
>   points, causation).
> - `## Candidate confirmations` (copy any that exist), `## Honest gaps`,
>   `## Honest gaps / suggestions (outside editable scope)`, `## Reusable metrics`,
>   `## Project fact sheet` (per project: what, stack, role, outcomes, sources).
>
> **Step B — write the draft** into `<dir>/03_cv.md`: start from a byte-for-byte copy
> of `cv/template/CV_template.md`; rewrite only the **Delta bullets** (max 5, ordered by
> the JD's top selection criteria, Impact-First, ≤ 2 rendered lines each) and the
> **Skills rows** (verified, JD-relevant technologies first; same row count). Leave the
> Profile exactly as in the template — Codex writes it last. Every rewritten line uses
> only Verified, User-reported or Template ledger claims, plus low-severity Inferred
> claims worded within the evidence; Template claims keep their template wording and
> may not lead a bullet or the Profile. End each rewritten bullet with `<!-- R#, R# -->`.
>
> **Step C — start `<dir>/07_improvement_log.md`**: one entry per changed line —
> original (template) text, revised text, reason, R# addressed, ledger IDs and their
> statuses.
>
> Reply in ≤ 8 lines: must-haves covered, honest gaps, any claim you could not
> classify better than Inferred / Missing Evidence, ambiguities for the user.

If the agent could not reach Notion, stop and tell the user.

## Phase 3 — HR & Hiring Manager Review Gate, loop review (Codex)

Spawn `Agent(subagent_type: "codex:codex-rescue")` in the **foreground**, read-only:

> Review-only task, do not edit or create any files. Act as a senior technical recruiter
> and an engineering hiring manager for this JD. Read `<dir>/jd.md`,
> `<dir>/01_jd_analysis.md`, `<dir>/02_evidence_map.md`, `<dir>/03_cv.md`,
> `cv/template/CV_template.md`, `SKILL_DIR/references/review_rubric.md`,
> `SKILL_DIR/references/evidence_rules.md`, `SKILL_DIR/references/cv_writing_rules.md`.
> MODE: loop review r<N>. The Profile is still template text and is written after this
> loop — exclude it from every gate, score and suggestion. Authorised extra scope:
> `<--scope sections or "none">`. Run the hard gates, Gates A–D,
> the claim audit and the weighted score exactly as the rubric describes. Re-classify
> every claim in the Delta bullets and Skills yourself: use your Notion MCP tools to
> re-read the cited source for every high-severity claim (note "re-read <source>" in
> the claim-audit Note) — do not grade against the writer's ledger paraphrase alone.
> Apply the reader test (evidence_rules rule 3), placement-implies-causation (rule 8)
> and the Gate C readability checklist; a Gate D "did X actually run / cause Y?"
> question caps that claim at Inferred, with severity at least medium, unless you
> re-read a cited execution or causation record, or a candidate confirmation establishes it.
> [r1 only] Fill "Suggested bullet order" with a reason per position (merge/drop if
> more than 5). [r ≥ 2] Write `unchanged` unless a revision broke the order.
> Output exactly the rubric's "Required output format", nothing before `JOB FIT SCORE`.

Save the reply verbatim to `<dir>/04_review_r<N>.md`, then run:

```bash
python3 -I SKILL_DIR/scripts/review_tools.py gate --review <dir>/04_review_r<N>.md \
  --cv <dir>/03_cv.md --threshold <threshold>
python3 -I SKILL_DIR/scripts/review_tools.py ledger --evidence <dir>/02_evidence_map.md --cv <dir>/03_cv.md
```

`ledger --cv` fails if a number in the Profile or the Delta bullets has no live ledger
row — fix the ledger (or the CV) before the next phase. Never tell the reviewer the
threshold; the script applies it.

The script recomputes the score from the table, validates the weights, counts Delta
bullets, reads the claim audit, scope and writing-issue sections and prints the
**derived verdict** — it overrides the reviewer's own verdict when they disagree (note
the mismatch in the log). If the review cannot be parsed, re-run Phase 3 once with a
reminder about the format; if it fails again, show the user the raw output.

Decision (derived verdict):
- **r1 with a changed order** — apply it even on PASS: if PASS and no merge/drop is
  needed, do an order-only pass (pure permutation, verified by diff) and continue;
  otherwise run a full Phase 4 with the order applied first.
- `PASS` → Phase 4a.
- `REVISE` / `NEEDS EVIDENCE` with substantive passes left (< `--max-rounds`) → Phase 4.
  (Blocking claims = any Contradicted or Missing Evidence, or Inferred above low.)
- Out of passes → continue to Phase 4a only if hard gates 1–3 pass; any remaining
  `NEEDS EVIDENCE` claims are carried into the final gate and reported as **Needs
  Evidence**. If a hard gate still fails, stop and report the violations instead of
  building.

Record the final order and per-position reasons in `summary.md` ("Bullet order").

## Phase 4 — Revise (Opus)

Spawn a **new** `Agent(subagent_type: "general-purpose", model: "opus")`:

> Revise `<dir>/03_cv.md` in place using `<dir>/04_review_r<N>.md`. Read
> `02_evidence_map.md`, `SKILL_DIR/references/evidence_rules.md` and
> `SKILL_DIR/references/cv_writing_rules.md` first. Do not touch the Profile. Priority:
> (1) remove or correct every Contradicted / Missing Evidence / high-severity Inferred
> claim — if it cannot be supported, drop it or reword to what the evidence shows;
> (2) hard-requirement and scope problems; (3) apply "Suggested bullet order" (whole
> bullets, keep `<!-- R# -->`); (4) unclear ownership, poor JD relevance and every
> "Writing issues" rewrite (plainer and more exact, never longer); (5) "Top edits" and
> uncovered must-haves that the ledger supports; (6) Skills ordering. Never add a claim
> outside the ledger; you may search Notion for more evidence and add ledger rows with
> sources. Enforce max 5 Delta bullets and the length limits. Append one entry per
> changed line to `07_improvement_log.md` (original, revised, reason, R#, ledger
> ID/status, review round). Reply with a bullet list of what changed and anything
> you could not fix.

Then back to Phase 3 (N + 1). Do not optimise a subjective score once the gates pass,
and never trade credibility for points.

## Phase 4a — Second read (Fable)

After the loop ends and before the Profile, get an independent second read of the
final Delta bullets and Skills — a different model from the Codex reviewer. Run it
**inline** if the session model is Fable, otherwise spawn
`Agent(subagent_type: "general-purpose", model: "fable")`, read-only:

> Read-only. Check the Delta bullets and Skills rows of `<dir>/03_cv.md` against
> `<dir>/02_evidence_map.md` and the Notion pages it cites (re-fetch the source of every
> high-severity claim), using `SKILL_DIR/references/evidence_rules.md` (reader test,
> placement-implies-causation, Template status) and the Gate C readability checklist in
> `SKILL_DIR/references/review_rubric.md`. List only problems: quoted line, rule, what
> the evidence actually says, and a corrected line within the length budget.

Disagreement with the Codex review → the stricter reading wins. If substantive passes
remain, Opus applies claim-changing fixes through Phase 4 (consuming one pass), then the
pipeline returns to Phase 3 and repeats Phase 4a when the loop ends. If no passes
remain, carry unresolved findings into the final gate; stop if hard gates 1–3 fail.
Wording-only fixes that meet the Phase 6 micro-pass limits may be applied without
consuming a pass; then continue to Phase 4b.

## Phase 4b — Profile paragraph, written last (Codex)

Only after the loop ends. Target ≈ **4 rendered lines** (400–470 characters including
bold markers). Spawn `Agent(subagent_type: "codex:codex-rescue")`, read-only, reply-only:

> Read-only writing task: do not edit or create files; return the text in your reply.
> Write the PROFESSIONAL PROFILE paragraph for `<dir>/03_cv.md` from the **final** Delta
> bullets and Skills. Read `<dir>/jd.md`, `<dir>/01_jd_analysis.md` (top 5 selection
> criteria, hard requirements), `<dir>/02_evidence_map.md` (claim ledger — Verified /
> User-reported claims, plus Template claims in supporting positions with their template
> wording), `<dir>/03_cv.md`, the last `04_review_r<N>.md`,
> `SKILL_DIR/references/evidence_rules.md`, `SKILL_DIR/references/cv_writing_rules.md`.
> Make the target role obvious in the first sentence, lead with the strongest measured
> Delta result that has a Notion source or candidate confirmation (`Template` numbers may
> appear but not lead), connect 1–2 more achievements to the top selection criteria in the JD's
> terms, and carry ownership / collaboration evidence the bullets lack. Never state
> work authorization, relocation or availability unless confirmed. Start with the
> template's bold opening clause exactly (`<bold clause>`); 3–4 sentences; 400–470
> characters including markers; plain engineer voice; numbers identical to the bullets
> with their scope; tools by their real role. Output the paragraph as one Markdown
> line, a blank line, `CHARS: <n>`, then a note mapping each claim to a ledger ID.

Orchestrator: (1) fact-check every claim against the ledger and the bullets — on any
problem re-run 4b (prefer `--fresh` with the rejected draft and the reason) instead of
editing Codex's text; (2) insert it as the Profile line (keep CRLF; Bash one-liner);
(3) scope check — `diff` against the template shows only the Profile, Delta bullets,
Skills rows and any `--scope` sections; (4) log the Profile in `07_improvement_log.md`;
(5) give the Profile the same Fable second read as Phase 4a (Codex should not be the
only model to check Codex's own Profile).
If Codex is unavailable, fall back to Opus and say so.

## Phase 5 — Build + layout quality control (main thread)

1. Build (absolute paths; the wrapper converts them for the Windows venv):
   ```bash
   bash SKILL_DIR/scripts/build_cv.sh patch \
     --template-docx <abs cv/template/<file>.docx> --template-md <abs CV_template.md> \
     --md <abs dir>/03_cv.md --out <abs dir>/CV_<FirstnameLastname>_<Company>.docx --pdf
   ```
   `patch` rewrites only the changed paragraphs — body text and table cells such as the
   Skills rows — inside the original docx. A WARN line means an edit did not land: fix
   the Markdown line so its unchanged text matches the template, and rebuild. If the
   docx is locked (open in Word), build to a `_preview` name and ask the user to close
   it. Template is `.md` only → `build_cv.sh build --md ... --out ... --pdf`.
2. **Layout QC** on the rendered PDF:
   ```bash
   bash SKILL_DIR/scripts/build_cv.sh check --pdf <abs pdf> --docx <abs docx> \
     --md <abs dir>/03_cv.md --pages <template pages> --json <abs dir>/06_layout.json
   ```
   Checks page count, A4, font sizes (body 10 pt / headings 11 pt), clipping and
   margins, page-height utilisation (target 85–98%), section reading order in the
   extracted text, Profile line count, lines per Delta bullet, bullet and date
   alignment. Also `Read` the PDF and look at it. FAIL → fix: Profile too long → re-run
   4b tighter; a bullet wraps to 3 lines or the page overflows → Phase 4 with explicit
   character budgets, re-review the changed bullets (Phase 3), then re-run 4b. WARN →
   report it. With `--no-pdf`, omit `--pdf` from the build, write `06_layout.json` as
   `{"status": "UNVERIFIED", "checks": []}`, tell the final reviewer the rendered layout
   was not checked, and never claim the layout passed or recommend Ready to Apply.

## Phase 6 — Final gate (Codex)

Spawn `Agent(subagent_type: "codex:codex-rescue")`, read-only, with the Phase 3 prompt
but **MODE: final gate**: review the whole CV including the Profile, read the
earliest loop review in the current format (`<first rN>`) and `<dir>/06_layout.json`
too, and compare against that first draft. Save as `<dir>/05_final_gate.md`, then:

```bash
python3 -I SKILL_DIR/scripts/review_tools.py recommend --review <dir>/05_final_gate.md \
  --cv <dir>/03_cv.md --layout <dir>/06_layout.json --threshold <threshold>
python3 -I SKILL_DIR/scripts/review_tools.py compare --before <dir>/<first rN> --after <dir>/05_final_gate.md
python3 -I SKILL_DIR/scripts/review_tools.py ledger --evidence <dir>/02_evidence_map.md --cv <dir>/03_cv.md
```

Recommendation (`review_tools.py recommend`, in priority order): **Major Revision
Required** for a hard requirement NOT MET, layout FAIL, score below threshold, scope /
bullet / high-severity writing violations, or an invalid review format; otherwise
**Needs Evidence** for blocking claims (any Contradicted or Missing Evidence, Inferred
above low), AT RISK / UNKNOWN hard requirements, or layout other than PASS; otherwise
**Ready to Apply**. If the final gate only flags the Profile, re-run Phase 4b once,
rebuild the DOCX/PDF and re-run the Phase 5 layout QC, then re-run the final gate once.
Bullet problems found here are reported without another substantive revision loop, even
if passes remain. One **wording-only micro-pass** may be applied without consuming a
pass: each edited line changes no number, tool, claim or status, stays within ±5
characters of its previous length and within all length limits (e.g. fixing a
tool-role term the final gate flagged). Log the edits, rebuild, re-check the layout, get
a read-only final-gate confirmation of the edited lines, and re-run `recommend` on the
updated review.
Never silently approve a high-severity claim.

## Phase 7 — Reports (main thread)

Write `<dir>/08_hr_review_report.md` from the final gate, r1 and the script outputs:

- **A. Recruiter Review Report** — job-fit score (with the "not an ATS score / hiring
  probability" note), strongest matching qualifications, missing or weak
  qualifications, potential rejection reasons, relocation / work-authorization
  observations (only what is confirmed; otherwise "unconfirmed").
- **B. Engineering Hiring Manager Report** — evidence of technical depth, evidence of
  senior judgement, top three engineering achievements, weak or questionable claims,
  five likely technical interview questions.
- **C. Resume Improvement Log** — link to `07_improvement_log.md` (original → revised,
  reason, R#, evidence status for every major edit).
- **D. Final Tailored Resume** — absolute paths of the `.docx`, `.pdf`, `03_cv.md`.
- **E. Final Quality Gate** — score before (r1) → after (final gate) per category,
  remaining evidence gaps, number of unverifiable claims (Inferred + Missing Evidence +
  Contradicted), layout result (PASS / WARN / FAIL / unverified, from `06_layout.json`),
  hard requirements, and the recommendation.

Update `summary.md` (round table, bullet order, outputs, things to confirm). Reply to
the user with A–E condensed, the recommendation, the output paths and the questions
only they can answer. Do **not** commit anything — `cv/` is gitignored.

---

## Guardrails

- **Truth beats score.** A PASS with a Contradicted or high-severity unsupported claim
  is a failure. Never invent technologies, responsibilities, deployment status,
  certifications or numbers; never assume work authorization, sponsorship or
  relocation.
- **Scope beats score.** Out-of-scope edits are reverted. Requirements that can only be
  shown elsewhere go under "Honest gaps / suggestions".
- **The derived verdict wins.** Always run `review_tools.py gate` / `recommend`; do not
  hand-wave the reviewer's numbers.
- **Layout is verified, not assumed.** Only `build_cv.py check` on a rendered PDF counts.
- Pass paths, not file contents, to subagents.
- Stopping a `codex:codex-rescue` agent does **not** stop its Codex job: run
  `node <plugin>/scripts/codex-companion.mjs status` and `cancel <job-id>`, or the orphan
  blocks later `--resume`. Prefer `--fresh` with the full brief (including the rejected
  draft and why) when another Codex job ran in between.
- Keep every intermediate file — they are the audit trail. Review numbers continue
  across re-runs in the same folder (r4, r5 …).
- **Score-only mode:** if the user only wants an assessment of an existing CV, run
  Phases 0, 1, 2 (Step A only), 3 and 7 (reports A, B, E) without rewriting.
- After editing this skill, ask Codex for a read-only consistency review of `SKILL.md`
  and `references/` and run `python3 -I -m unittest discover -s SKILL_DIR/scripts/tests`.
