# Codex prompts (fill the `{…}` fields, send verbatim)

Every prompt is sent through `Agent(subagent_type: "codex:codex-rescue")`. The first line
is always `--fresh` and the second always the review-only guard, so the wrapper never
adds `--write` and never resumes an older thread. Replace nothing else.

Common header (both prompts):

```
--fresh
Review-only. Do NOT edit or write any files. Do NOT use Notion. There is no AGENTS.md — do not search for it.
Project root: /mnt/e/PythonProject/LinkedinJobSearcher
Application dir: {D}
Read, in this order: {D}/jd.md · {D}/01_jd.md · {D}/03_cv.md · cv/profile/bullet_library.md ·
cv/profile/candidate_confirmations.md · .claude/skills/tailor-cv/references/writing_rules.md
Source of truth = bullet_library.md + candidate_confirmations.md. Anything not in them is unverified.
```

## §Summary (step 4)

```
<header>
Task: write line 5 (PROFESSIONAL PROFILE) of 03_cv.md for {COMPANY} — {JD_TITLE}.

The Delta block in 03_cv.md is a scope line + 2 project cards (bold title — context line, then
2–3 bullets whose bold lead is a JD duty phrase). Build the Summary from the two cards' context
lines and the scope line, not from individual bullets.

Rules
- 400–470 characters including the ** markers (≈ 4 rendered lines at 10 pt).
- Opens exactly: **{JD_TITLE} with 6 years of industry experience** followed by a relative clause ("who …").
- Sentence 1 = role + the business outcome of card 1; sentence 2 = card 2's outcome + ownership
  (design → launch → maintenance → incident response); sentence 3 = how the candidate works with
  users / international teams; optional sentence 4 = one engineering judgement from
  "Decision rationales".
- ≤ 3 metrics, all from the library's "Summary facts" (a from→to pair = 1 metric; "6 years" does not count).
- Mirror the JD's lead responsibilities in its own words; no tool lists; no project that is not in 03_cv.md.
- Follow writing_rules.md (voice, banned words, no over-claiming: staging ≠ production, no "matching output").

Reply ONLY:
SUMMARY: <text>
CHARS: <count including ** markers>
SOURCES: one line per clause → library fact / card / bullet ID / CONF ID
```

Retry (one `SendMessage` to the same agent) only for: length outside 400–470, a fact not in SoT,
or an over-claim. Quote the offending clause and the rule; never hand-edit the Summary.

## §Review (step 6)

```
<header>
Also read {D}/06_layout.json and {D}/07_external_ats.md.

You are two reviewers at {COMPANY} ({COUNTRY}, {INDUSTRY}) reading this CV for the
{JD_TITLE} opening, then an ATS keyword matcher. Score each independently.

HR (0–100) — a recruiter's 10-second read: does the headline + Summary + card titles say
"this person already does this job"? Why-this-job signal, visible outcomes, location/relocation
and language lines, photo present, rejection risks.
HM (0–100) — the hiring manager: depth on the JD's top-5 criteria (from 01_jd.md), business
value, credibility of numbers, senior ownership, and 3 questions they would probe in interview.
Project frame — for each card: can you say in one line what it is, who uses it and what the
candidate owned? If not, name what is missing.
Lead check — for each bullet: does the bold lead match a JD duty phrase (quote the JD text) or
is it a result/tool phrase? List bullets whose lead could be swapped to a closer JD phrase
using only the `leads:` variants stored in bullet_library.md.
ATS — do NOT recount keywords. Read {D}/07_external_ats.md (deterministic Resume-Matcher run:
parse scores, keyword match n/N, found/missing lists). Report its numbers, then classify every
missing term as "SoT-supported (cite library ID / CONF and the wording to use)" or "gap". Flag any
parse check that is not pass and say whether it is a template matter (photo, two-column block,
tables) or a per-application matter.
VERDICT: READY | REVISE, then ≤ 7 edits. Bullet edits = library ID or lead-variant swap only;
Skills/headline edits = library items only; Summary edits = describe the problem (the writer
re-runs the Summary prompt). Mark anything you cannot verify from SoT as UNVERIFIED instead of
proposing it.

Output ≤ 60 lines: HR, HM, project frame, lead check, ATS table + percentage, 3 strengths,
3 risks, VERDICT + edits.
```
