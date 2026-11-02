---
name: apply
description: End-to-end application for one JD — fetch the JD, research the company, brief + keywords + concept match, a library-fit gate (default 50 %, below it stop and log "skip: <top missing concepts>" unless the user says go), tailored CV (tailor-cv steps 2–7 incl. ATS + Codex review), cover letter, interview prep, outreach drafts, tracker rows (準備中) in Notion 投遞紀錄 + Sheet 投遞表單, then a human gate. Each step writes into the application dir and the next reads it; a re-run resumes at the first missing output. Never sends, submits or posts anything. Use when the user says "/apply <JD url|file|text>", "apply to <job>", "投遞", "幫我申請", or picks a posting from the morning digest.
argument-hint: "<JD url | file | text | JD code | app dir> [--from <step>] [--threshold 50]"
---

# /apply — one JD, end to end

Plan: `.claude/plans/job_system_v1.md` §3. This skill only orchestrates. Each step is an existing skill; read
that skill's SKILL.md and follow it for the step. Every step writes into `$D`, and the next step reads from `$D`.

**Glossary** — `$AP` `python3 SKILL_DIR/scripts/apply.py` · `$D` the application dir
`cv/applications/<YYYYMMDD>_<Co>_<Role>/` (older applications: the `v3_cards/` inside it, the dir `library_db.py
status` prints) · `$DB` `python3 .claude/skills/tailor-cv/scripts/library_db.py` · `$CR` `/company-research` ·
`$PUSH` `python3 .claude/skills/daily-job-tracker/scripts/push_to_sheet.py`. `cv/` is gitignored. Never commit it.

**Hard rules**
- Never send, submit or post. No e-mail, no LinkedIn message or invite, no form submission. The candidate does that.
- Filling an application form is only done by `job-apply:job-apply`, only when the user asks, and only through its
  own confirm-before-submit step.
- The tracker gets **append-only** rows with 狀態 / 最遠關卡 = 準備中. Never edit the user's rows. Change a status only
  when the user says it changed.
- Library edits (NEW LEAD, `sot-leads --write-from`, new CONF items) need the candidate's approval, as in /tailor-cv.
  In a headless run, list them under "pending approval" in `summary.md` and go on without them.
- Do not edit `library_db.py` or `candidate_confirmations.md` from this skill.

## Steps

| # | Step | Skill / command | Output in `$D` (what `status` checks) |
|---|---|---|---|
| 0 | JD | /tailor-cv step 0 (WebFetch / file / text) | `jd.md` |
| – | pre-gate | `$AP gate $D` (needs only `jd.md`) | STOP → skip summary, no web research |
| 1 | Company research | `$CR` steps 1–5 | `02_company.json`, `02_company.md` |
| 2 | JD brief + keywords + concept match | /tailor-cv step 1 | `01_jd.md`, `07_job_keywords.json` |
| G | **Fit gate** | `$AP gate $D [--threshold 50]` | PASS / STOP / cannot judge (+ top missing) |
| 3 | CV | /tailor-cv steps 2–7 | picks in the DB, `03_cv.md` (Summary ≠ template line), `CV_*.pdf`, `06_layout.json`, `07_external_ats.json`, `04_review.md` |
| 4 | Cover letter | /cover-letter with `$APP = $D` | `cv/cover_letters/<dir>/CoverLetter_*.pdf` + `review.md` |
| 5 | Interview prep | /interview-prep | `10_interview.json` + `.md`, rows in `interview_qa` |
| 6 | Outreach drafts (optional) | /outreach | `11_outreach_<kind>_<person>.md`, rows in `outreach` |
| 7 | Tracker | `$DB record` + `$AP tracker $D` + Notion MCP + `$PUSH --live --submissions-append` | applications row, Notion 投遞紀錄 row, Sheet 投遞表單 row |
| 8 | **Human gate** | the user | 狀態 履歷送出 (set by the user) |
| 9 | Report | `$AP summary $D` | `summary.md` (≤ 25 lines) |

### Resume

Always start with `$AP status <arg>`. The argument can be a JD code, an app dir, or a `jd.md` path. For a new
URL or text, run step 0 first. `status` prints one row per step (done / partial / todo / optional / stop / user)
and a `NEXT:` line. Continue from that step. `--from <N>` redoes step N and everything after it, even when the
outputs exist. Use it when the JD changed or the user asks for a rebuild. A tailor-cv rebuild overwrites `03_cv.md`,
PDF and DOCX. Copy them to `*_pre_<reason>.*` first, as tailor-cv does. `status --sheet` also reads the tracker
Sheet, read-only, for the row and its 狀態. When 狀態 is already past 準備中, `NEXT` says so, and open steps only
matter for the next stage.

### 0 · JD
/tailor-cv step 0. Choose `$D` = `cv/applications/<YYYYMMDD>_<Co>_<Role>/` and write `jd.md` with the Source URL, Company,
Role, Location and Posted lines. If a dir for the same posting already exists, use it and resume.
Then run the **pre-gate**: `$AP gate $D`. It scores `jd.md` alone, so a STOP here saves the web research. On STOP,
follow the gate rule below without running step 1.

### 1 · Company research
Run `$CR` steps 1–5: `company_research.py init --jd <Co>`, or `--company … --jd-file $D/jd.md --out $D` while
there is no applications row yet. Then research, fill, `check` until OK, and `render`. A failure or thin result
does not block the later steps. Note it in the summary. Its hooks feed step 3's Summary, the cover letter's
`company_line`, interview motivation/ask answers and the outreach hook.

### 2 · Brief, keywords, concept match, gate
Run /tailor-cv step 1: `01_jd.md`, `07_job_keywords.json` per `references/ats_keywords.md`,
`normalize-keywords --write`, `match`, `gaps`, and `resolve` / `set-concept` for unresolved terms. Then the
**gate**:
```bash
$AP gate $D                    # exit 0 PASS · 1 STOP · 3 too thin to score
```
- **PASS** (fit ≥ threshold, default 50 %): go on.
- **STOP**: run `$AP summary $D`. It writes the skip summary with the line `skip: <top missing concepts>`. Show the
  user the fit and the top missing concepts, then stop. Go on only when the user says "go". Then continue with
  step 3. `status` shows the gate as `override` once `03_cv.md` exists.
- **cannot judge** (exit 3, `fit: null`, fewer than 8 scored items): never read null as 0 %.
  - Interactive run: ask the user "go or skip?" before step 1. `status` shows the gate as `warn` and prints the
    question.
  - Headless run (`--headless`, or `CLAUDE_SCHEDULED=1` in the scheduled job's environment): STOP. Run
    `$AP summary $D --headless`. It writes "cannot judge" and nothing else is built. The user decides later.
  A TTY test is not used, because Claude's Bash tool never has a TTY.
The fit formula is in `.claude/skills/morning-run/references/fit_scoring.md`. A user-set `--threshold` applies to this
run only. Calibration on the ten recorded applications, all chosen by the user (2026-11-02, full `jd.md`):

| Fit | Applications |
|---|---|
| ≥ 68 % | Scout24 86, Jupus 86, HelloFresh 82, MAIA 78, MAIA-SWE 78, TOMRA 73, Ixopay 68 |
| 50–59 % | Mapbox 55, AIVenture 53 |
| < 50 % | Taktile 49 |

At 60 % three of them would have stopped, so the default is 50 %. Taktile would still stop: the user's "go" decides.

### 3 · CV (tailor-cv steps 2–7)
Follow /tailor-cv exactly: pick → align → fit → record, assemble, Summary via `/multi-writer summary`, build +
layout + external ATS + `ingest-ats`, Codex review, one revision with `sot-leads`. The Summary may reuse one hook's
company fact, taken from `python3 .claude/skills/company-research/scripts/company_research.py excerpt --jd <Co>` and only from a hook marked `use: all`. Never read `02_company.md` for this: its
candidate lines hold library facts that are not on the CV. The Summary never adds a candidate claim. Skip tailor-cv step 8's `summary.md`, because
step 9 writes it. The pick string stays in the DB (`$DB picks <Co>`).

### 4 · Cover letter
Run /cover-letter with `$APP = $D`. It reads `01_jd.md`, `03_cv.md` and `07_job_keywords.json`. Its output dir is
`cv/cover_letters/<same dir name>/`, and `status` finds it by `letter_check.json` → keywords file, or by the dir
name. The `company_line` may use one hook's company fact, taken from `python3 .claude/skills/company-research/scripts/company_research.py excerpt --jd <Co>` and only from a `use: all` hook,
never from `02_company.md`. The Codex score and one revision
happen inside that skill.

### 5 · Interview prep
Run /interview-prep. Its pack includes the company-research excerpt with each hook's `on CV:` and `use:` flags.
Motivation answers use those hooks, and ask items start from its questions. Company numbers pass the check only
under the company-number rule (no first-person sentence, company or product named, same context). Store the result with `qa import`. Report the `TODO candidate:` items.

### 6 · Outreach drafts
Run /outreach. It writes drafts only.
- **connect-note** to the hiring manager when one is known: the user names them, or the JD or a public source
  cited in `02_company.json` names them. Never look up private data on people.
- **referral-letter** only when the user names a referrer. Its PRIOR is `09_referral_final.md` if present.
- No person known → skip. `status` shows the step as `optional`, and the summary says "no hiring manager or referrer
  named".
Each draft runs `check`, then the Codex review, then `record` with status `draft`. Nothing is sent.

### 7 · Tracker
1. `$DB record --jd <Co> --company … --title … --dir <abs $D> --picks "…" --headline "…" --country <C>`. Re-running
   it with the final picks is harmless.
2. `$AP tracker $D`. It looks up the Sheet read-only and prints what exists. When the Notion row and the Sheet row
   both exist (the daily pipeline made them), stop here. Never create a duplicate.
3. Otherwise:
   - First query 投遞紀錄 through the Notion MCP (`notion-query-data-sources` on the data source the prompt names, or
     `notion-search`) for the job URL in 職缺連結 and for the company. The user may have made the row by hand, and
     then that row is used and no new one is created.
   - Create the **Notion 投遞紀錄** row exactly as the printed prompt says: properties with 最遠關卡 = 準備中, and
     the PDFs attached with `notion-create-file-upload` plus one `curl` each, within the hour.
   - Put the new page URL into the printed row JSON's `notion`, then append the **Sheet 投遞表單** row:
     `$PUSH --live --submissions-append -`. It skips a URL that is already there.
   Load the Notion tools with ToolSearch first. If Notion or the Sheet token fails, report it and go on.

### 8 · Human gate
Show the user the CV PDF, the cover-letter PDF, the outreach drafts and `summary.md`, then stop. The user then does
one of these:
- marks 履歷送出 in the Sheet or dashboard themselves after sending, or
- asks `job-apply:job-apply` to fill the form, with approval at its submit step.
On the user's word only, record what happened: `$DB outreach set <id> --status sent --sent <date>` for a sent
draft. A 狀態 change goes in the Sheet, by the user or on their explicit request.

### 9 · Report
`$AP summary $D` writes `$D/summary.md` (≤ 25 lines). It covers fit + top missing, company hooks and top risk, CV
picks, ATS and keywords, layout, HR/HM and verdict, cover-letter scores and required-term coverage, interview counts
and TODOs, outreach drafts, tracker rows and 狀態, file paths and **Next action**. Add `--sheet` for 狀態. It
refuses to overwrite a `summary.md` it did not write, such as an older standalone /tailor-cv report. In that case,
use `--out <path>`, or `--force` only after the user agrees. `--print` writes nothing. Reply in 5–8 lines: fit,
ATS/review, letter, TODOs, files, next action.

## Files

| Path | Role |
|---|---|
| `scripts/apply.py` | `status` · `gate` · `tracker` (prints the Notion prompt + Sheet row) · `summary` |
| `.claude/skills/morning-run/scripts/morning.py` | `fit()` used by the gate (imported, read-only) |
| `.claude/skills/daily-job-tracker/scripts/daily_pipeline.py` | `notion_row_prompt()` reused by `tracker` |
| `.claude/skills/daily-job-tracker/scripts/sheets_api.py` | read-only Sheet lookup (`--sheet`, `tracker`) |
