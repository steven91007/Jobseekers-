# Job-search system v1 — plan (2026-11-02)

Source idea: career.goglobal reel (four pillars: personalised AI · specialised agents · end-to-end
workflow · scheduled automation). This maps the pillars onto what already exists in this repo and
lists what to build. Rule kept from the reel's own caveat: **anything outward-facing (sending an
application, replying to HR, messaging a referrer) stays behind a human approval gate.**

## 1. Personalised AI — "an AI that remembers you"

| Have | Gap |
|---|---|
| Experience library (`cv/profile/library_seed.json` → `experience.sqlite`, Notion mirror): 29 bullets, 42 concepts, 303 JD terms, JD-wording leads, CONF confirmations | **Candidate profile**: target roles, countries, visa/relocation, salary band, notice period, languages, hard no-gos — today scattered in SKILL.md + job-apply preferences |
| `references/writing_rules.md`, `candidate_confirmations.md` (facts the candidate approved) | **Interview answer memory**: STAR answers per bullet/concept, questions actually asked per company, what landed / what didn't |
| Memory dir (`~/.claude/projects/.../memory`) for agent working knowledge | **Outreach memory**: who was contacted at which company, template used, reply |

Build: `cv/profile/candidate_profile.md` (one page, SoT) + two SQLite tables in `experience.sqlite`
(`interview_qa`, `outreach`) with `library_db.py` subcommands `qa add/list`, `outreach add/list`.
Every agent below reads the profile first instead of being told who the candidate is.

## 2. Specialised agents — one job each

| Agent | Status | Notes |
|---|---|---|
| Job finder | **have** `/daily-job-tracker` (DE/NL/IE/UK → Sheet) + job-apply `job-search` | add concept-match scoring: auto-extract keywords → `library_db.py match` → fit % per job, so the list is ranked by library fit, not just title |
| JD analyst + CV tailor | **have** `/tailor-cv` (Phase 1 brief, concept pick/align, build, ATS, Codex review, sot-leads) | — |
| Cover letter | **have** `/cover-letter` | — |
| Prose writer / judge | **have** `/multi-writer` | — |
| Company researcher | **missing** | web-fetch: product, tech stack, recent news, Glassdoor-style signals, hiring manager / team names → `02_company.md` in `$D`; feeds Summary, cover letter, interview prep |
| Referral / outreach writer | **manual** (TOMRA letter done by hand + Codex) | skill `/outreach`: referral letter, LinkedIn note, follow-up mail from `$D` artefacts + outreach memory; Codex fact-check against `03_cv.md` (the TOMRA procedure, codified) |
| Interview prep | **missing** | skill `/interview-prep`: JD concepts × library evidence → 15–20 questions (behavioural, system design, project deep-dive on the picked cards) with STAR answers drawn only from SoT; stores to `interview_qa` |
| Inbox tracker | **missing** | Gmail connector (read-only): find recruiter / ATS mails, classify (ack, interview, rejection, request), propose status change in the tracker; never sends |
| Calendar | **missing** | Google Calendar connector: interviews of the day/week, conflicts; proposes events, user confirms |
| Tracker | **have** Notion GERMAN PLAN 投遞紀錄 DB + Sheet 投遞總覽 + `library_db.py status` | single write path: `tracker.py set-status <Co> <stage>` used by all agents |

## 3. End-to-end workflow — `/apply <JD url|file>`

One orchestrating skill that chains existing skills; each step writes into `$D` and the next reads it.

```
0  jd.md                 fetch JD                              (have: tailor-cv step 0)
1  02_company.md         company research                      (new)
2  01_jd.md, 07_*.json   JD brief + keywords + concept match   (have: tailor-cv Phase 1)
   ── gate: fit % < threshold → stop, log "skip: <top missing concepts>"
3  03_cv.md → PDF, ATS   tailor-cv steps 2–7                   (have)
4  cover letter PDF      /cover-letter                          (have)
5  10_interview.md       /interview-prep                        (new)
6  11_outreach.md        referral / recruiter note drafts       (new)
7  tracker               record picks + status 準備中, files    (have: record + Notion/Sheet)
   ── gate: user reviews CV / CL / outreach → marks 已投遞 by hand (or job-apply fills the form with approval)
```

Codex review stays inside step 3/4. One `summary.md` at the end (≤ 25 lines) with the next action.

## 4. Scheduled automation — the morning run

`/schedule` (cloud routine) or a local cron, weekdays 07:30, read-only except the tracker:

1. `/daily-job-tracker` → new postings (24–48 h) → concept-match score → top 5 with fit % and the
   top missing concepts.
2. Inbox tracker → mails since yesterday classified; proposed status changes (not applied).
3. Calendar → today's / this week's interviews; for each, link `10_interview.md` if it exists.
4. Follow-up rule: applications in 已投遞 for > 10 days with no mail → "follow-up due".
5. Digest → one Notion page "Today" (or Sheet tab) + a short message; the user replies with the
   JDs to run `/apply` on.

Nothing is sent. The dashboard (`tools/cv-dashboard`) is the place to look at it; its "Ask Claude"
runs can trigger `/apply` for a chosen row.

## Build order (each step usable on its own)

1. `candidate_profile.md` + `interview_qa` / `outreach` tables (½ day) — pillar 1 foundation.
2. `/interview-prep` (1 day) — biggest direct value for the applications already out.
3. `/outreach` (½ day) — codifies the TOMRA referral procedure (two project cards, Codex fact-check).
4. Company researcher (½ day) — small, feeds 2, 3 and the Summary.
5. `/apply` orchestrator (1 day) — chains the above; gates as listed.
6. Morning run (1 day) — Gmail + Calendar connectors + `/schedule`; needs the user's OAuth once.

Out of scope for v1: auto-submitting applications, auto-replying to mail, LinkedIn automation
beyond the existing job-apply skill.
