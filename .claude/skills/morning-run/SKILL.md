---
name: morning-run
description: The morning run. It ranks today's new postings from the daily pipeline by experience-library fit and shows each job's top missing concepts. It classifies recruiter/ATS mail since the last run and proposes status changes, lists today's and this week's interviews with their prep file, and flags applications in 履歷送出 with no reply for more than 10 days as follow-up due. It writes one digest of 40 lines or fewer to cv/digest/YYYY-MM-DD.md. Read-only otherwise. It never sends mail, never creates or accepts events, and never changes a tracker status. Use when the user says "morning run", "早上摘要", "今日摘要", "digest", or when a scheduled job invokes it.
argument-hint: "[--date YYYY-MM-DD] [--top 5]"
---

# /morning-run — one digest each morning

Plan: `.claude/plans/job_system_v1.md` §4. One script does everything. **Gmail and Calendar are read through
the shared local Google login** (`google_auth.py`, scopes `gmail.readonly` + `calendar.readonly`, added by the
user on 2026-11-02), so a run needs no Claude and works headless. The claude.ai connectors remain a fallback:
Claude fetches with them and pipes the JSON **on stdin**.

```bash
M=".claude/skills/morning-run/scripts/morning.py"     # WSL python3, stdlib only
```

**Hard rules**
- **Google calls are GET only**, on the read-only scopes (`gmail.readonly`, `calendar.readonly`,
  `spreadsheets` read path).
- **Writes:** only these.
  - `cv/digest/YYYY-MM-DD.md`.
  - `~/.config/ai-job-tracker/google_token.json`, when `google_auth.py` refreshes an expired access token
    while the Sheet is read. That is the shared Sheets login, and the scopes do not change.
  - `data/morning/jev_cache.json` with `--classifier jev` (gitignored, chmod 600). It holds Jev's answers per
    Gmail message id + wording version, so each mail is sent once.

  Nothing else is written. The DB is opened `mode=ro`. The Sheet is read through `sheets_api.py` without a
  flush. The pipeline ledger and logs are only read.
- **One exception, never scheduled:** `run --pipeline` runs `daily_pipeline.py --live --only search`.
  That appends new jobs to the Sheet tab 每日職缺, the pipeline's own append-only write. Use it only when
  the user explicitly asks in an interactive session. The script refuses it when `CLAUDE_SCHEDULED=1` or
  stdin is not a TTY, unless `--yes` is given.
- **Gmail (connector fallback):** search/list/read tools only. Never send, draft, reply, label, archive or delete.
  **Calendar:** list/read tools only. Never create, accept, decline or move events.
- **Status changes are proposals** in the digest. Apply one only when the user says so, in the Sheet
  (狀態 = furthest stage reached; see `references/inbox_rules.md`).
- **No auth in scheduled runs.** Never trigger a connector authentication yourself in a scheduled run.
  Report "not connected" and go on.

## Steps

| # | What | How | Output |
|---|---|---|---|
| 1 | New postings by library fit | `$M jobs` reads today's ledger from the 04:00 daily pipeline (`data/pipeline/runs/<day>.json`) and JD summaries (`logs/<day>_search.json`). It runs `fit` on each | top N, fit %, top 3 missing |
| 2 | Inbox | `$M inbox --google` (Gmail API, read-only), fallback: connector JSON → `$M inbox -` | kind, matched phrase, proposed 狀態 |
| 3 | Calendar | `$M calendar --google` (Calendar API, read-only), fallback: `$M calendar -` | today / next 7 days, prep link |
| 4 | Follow-ups | `$M followups`: tracker rows 履歷送出, no reply > 10 d, plus `outreach` rows `sent` with no reply | `/outreach <Co> follow-up` |
| 5 | Digest | `$M run` (default = Google), or `$M run --connectors -` | `cv/digest/YYYY-MM-DD.md` (≤ 40 lines) + stdout |

### 1 · New postings

The daily pipeline (`/daily-job-tracker`, Task Scheduler at 04:00 Asia/Taipei) already searched, scored
(Codex 1–10) and appended to 每日職缺. The morning run adds the **library fit**
(`references/fit_scoring.md`):

```bash
python3 $M fit cv/applications/<D>/jd.md [more …] [--json]   # any JD text: fit % + top missing (the /apply gate uses this)
python3 $M jobs [--top 5]                                     # today's new postings ranked
```

**Ranking.** Postings with a Codex score ≤ 3 sink to the bottom with their blocking reason, usually work
permit or sponsorship. All others are sorted by fit, then by Codex score.

**JD text.** The pipeline keeps only a one-line JD digest of up to 3000 characters, written by the search
run, in `logs/<day>_search.json`. `fit` splits it into sentences and reads inline headings
("Must-haves: …"). A posting with fewer than 8 scored items shows "–" and "summary too thin". For a real
answer, run `fit` on the full `jd.md` or use `/tailor-cv` Phase 1.

### 2 · Inbox (Gmail, read-only)

**Default: local Google login.** `python3 $M inbox --google [--since D]` (no `--since` = the last digest, else 2
days ago).
- It calls only `GET gmail/v1/users/me/messages?q=…` and `messages/{id}?format=metadata` (From, Subject, Date,
  Auto-Submitted, List-Id + snippet).
- It reads `format=full` only when the snippet alone says ack or other. Snippets stop at about 200
  characters, and rejections open with "thank you for your interest".
- It never calls modify, labels, drafts or send. The scope is `gmail.readonly`.
- The query is in `references/inbox_rules.md`. It excludes the user's own mail and the known job-alert
  senders.
- Job-board alerts and newsletters are dropped as other/newsletter and counted: LinkedIn Job Alerts,
  StepStone Jobagent, Glassdoor, Indeed, Jobgether, webinars. Board mail about the candidate's own
  application is kept, for example "Your application was sent to X".

**`run` fetches 45 days** (the follow-up window) in one pass:
- The Inbox section lists mail since the last digest.
- Older mail whose proposal is not applied yet is grouped into one line.
- Any company with recruiting mail in the window is not listed as follow-up due.
- The first run on 2026-11-02 read about 110 mails in about 70 s.

**Fallback: claude.ai connector.** Use it when the token lacks the scope or the API fails; the digest
names the error.
1. `ToolSearch` for `Gmail`. If only `…__authenticate` exists, the connector is not connected either.
2. Otherwise search with the same query and pass `[{id, from, subject, date, snippet, auto_submitted?}]` to
   `$M inbox -` or `run --connectors -`.

Output per mail: the kind, the phrase that decided it, the mapped company and tracker row (by title when
the company has several rows), and the proposed 狀態. When the mail names a role the company's rows do not
have, it shows "role not in tracker — check by hand" and makes no proposal.

### Inbox classifier: regex (default) or Jev

`--classifier jev` on `inbox` / `run` asks TypeSafe's Jev (System One API, `jev-latest`) instead of the regex
table. The user chose Jev for the scheduled 07:30 run (`tools/pipeline/run_morning.sh`) and approved sending
mail sender, subject and text to typesafe.ai.
- **Wording, thresholds and truth table** are all in `scripts/jev_questions.py`, and the client is
  `scripts/jev_client.py` (stdlib urllib).
- **What gets sent.** Only kept mails go to Jev: an ATS domain, a regex company match, or a recruiting
  subject.
  - Private mail (gmail, outlook, hotmail, yahoo, icloud, gmx, web.de …) is never sent unless it names a
    tracker company.
  - LinkedIn message notifications ("sent you a message", "Connecting with …") are classified other and not
    sent.
  - Board alerts are dropped earlier, by the regex prefilter.
- **One request per mail.** Seven kind Nouls, plus the `names_a_candidate_company` Noul and a company Choice
  over a **shortlist** of at most 8 tracker companies (data minimisation: never the whole tracker).
- **Company rule:** a company is accepted only when the companion Noul says yes, the Choice `confidence` is
  0.8 or above, **and** the name appears literally in the sender, domain or subject. Otherwise the line
  reads "company unsure: <name> (p)" and makes no proposal.
- **Truth table** (Noul ≥ 0.8 yes, ≤ 0.4 no, in between review). Each review gives no proposal:
  - a missing Noul → review;
  - two of offer / interview / rejection at yes → review;
  - an auto-reply or mass mailing with any decisive Noul above 0.4 → review (an out-of-office that quotes
    an invite).
- **Cache** `data/morning/jev_cache.json`, keyed by Gmail id + `wording_version()` (a hash of the model and
  the wording). Only uncached ids are sent, so the older pending-proposal mails of the 45-day window reuse
  it. Changing any wording re-asks everything once.
- **Failures.** The timeout is 15 s, with 2 retries on 408 / 429 / 5xx / 529 (Retry-After honoured, capped at
  10 s). A network or 5xx error opens a circuit breaker: the rest of the inbox uses regex, with one digest
  note. The whole inbox has a 120 s deadline. API errors never become answers.
- **Digest markers.** The digest prints "Jev: N calls · C cached · F regex fallbacks · T tokens", and each
  inbox line is marked `[jev]`, `[regex]` or `[regex↩]` (fallback).
- **Key**: env `TYPESAFE_API_KEY` or `~/.config/ai-job-tracker/typesafe_key`, kept in the WSL ext4 home with
  chmod 600 (NTFS under `/mnt/*` cannot enforce it). Without a key: "Jev: no API key", then regex.
- **`--debug`** prints the payloads to stderr with the key redacted. It is refused when `CLAUDE_SCHEDULED=1`.
- **Cost** (docs models.md): $0.042 per million input tokens, output free. About 1,000 tokens per mail.
- **Tests** (`selftest`): truth table, request builder, company rules, the five recorded live responses in
  `fixtures/jev_recorded.json` (synthetic mails), the cache (a second run sends 0 requests), the keep
  filter, and the breaker.

`morning.py abtest [--days 45] [--fixtures] [--stub] [--refetch] [--score CSV]` compares the two arms on
identical inputs.
- **Inputs**: the labelled fixtures plus real mail of the last N days, a set written once to
  `data/morning/abtest_mails_<date>.json` (gitignored, chmod 600).
- **Outputs**: the report `cv/digest/abtest_<date>.md`, and for real mail
  `cv/digest/abtest_labels_<date>.csv` with **empty** gold columns for the user to fill in, then
  `--score`.
- **Switch rule**: make Jev the default only if wrong status proposals ≤ regex **and** accuracy ≥ regex
  on the gold set, with n stated.
- **Status on 2026-11-02**: fixtures only (n = 31). Both arms reach 97 % with 0 wrong proposals. The
  fixtures are in-sample for both arms: the regex was built on them, and Jev's ack and invitation
  wordings were revised (v2) on them. The real-mail run is still pending; see the report.

### 3 · Calendar (read-only)

`python3 $M calendar --google`. This is `GET calendar/v3/calendars/primary/events`, today to +7 days,
`singleEvents=true`, scope `calendar.readonly`. The fallback is the connector JSON on stdin. The script
keeps events that name a tracked company (same matcher as the inbox) or read like an interview. For each
one it links `$D/10_interview.md` when it exists (`$D` = `applications.dir`), else it suggests
`/interview-prep <Co>`.

### 4 · Follow-ups (read-only tracker)

`python3 $M followups [--days 10]` reads 投遞表單, 求職追蹤表_AI 投遞 and 求職追蹤表, deduped by link or
company + title.
- **Sent date**: the latest dated line in 求職過程 with 投遞/送出/sent/applied. Otherwise it is
  inferred from the 日期 column or the earliest date in 求職過程, and shown as `sent≈`. `M/D` dates get
  the current year, or last year if that lands in the future.
- **Last contact**: the sent date, or a later follow-up line or an `outreach` row with status `sent`.
- **Not due**: 結束追蹤日期 filled, a reply line after the sent date, or mail from the company since the
  last run. A reply line needs a reply word (回覆, replied, 拒絕, Absage, invited, interview
  scheduled …); the bare word "interview" is not enough.
- **Due**: last contact 11 to 45 days ago. Older rows are counted as "stale, consider closing".

### 5 · Digest

```bash
python3 $M run                                  # Gmail + Calendar via the local Google login (default)
python3 $M run --connectors - <<'JSON'          # fallback: connector results, null = not connected
{"mails": [ … ], "events": null}
JSON
python3 $M run --no-gmail --no-calendar         # skip both
python3 $M run … --dry                          # print only, no file
```

The digest has the new-postings table (fit %, Codex score, top missing or low-score reason), inbox
proposals with the matched phrase, interviews, follow-ups due and next actions. Show it to the user as
printed. The user replies with the postings to run `/apply` on.

**Notion "Today" page: skipped.** `notion_sync.py` only mirrors the Sheet tabs 每日職缺 / 興趣職缺 and has
no page write. When the user asks for it in an interactive run, create the page with the Notion MCP under
GERMAN PLAN (`40b77152-eb3d-8166-9d0a-c7ceb8904012`) from the digest text. The script never does that.

`python3 $M selftest` checks the fixtures in `scripts/fixtures/`: 11 classifier phrases, 5 company
matches, 8 status proposals, and TOMRA ranked above Mapbox on both the full JD and the ledger summary.

## Gmail and Calendar access

**Done on 2026-11-02.** The user added `gmail.readonly` + `calendar.readonly` to `SCOPES` in `google_auth.py`,
enabled the Gmail and Calendar APIs, and re-consented with `google_auth.py --auth`. The token now carries
four scopes, and `morning.py` checks for the two read scopes before calling.
- **If the token is revoked** or the consent screen falls back to Testing, the refresh token dies after 7
  days. Run `google_auth.py --auth` again.
- **Connector fallback:** the claude.ai Gmail / Calendar connectors can still be authenticated once via
  `/mcp`. Only their search/list/get tools go into a run's allowed tools.

## Scheduling (documented, not created)

Run it on weekdays at 07:30 Asia/Taipei, after the 04:00 pipeline. Prefer **local**, because the
experience DB, the Sheet token and the pipeline ledger live on this machine. A `/schedule` cloud routine
would not see them. Also, the user disabled the earlier cloud routine on 2026-10-24 and asked for local
runs (memory note job-tracker-google-sheet).

- **Windows Task Scheduler**, the same pattern as `tools/pipeline/windows/run_pipeline.ps1`. Add a second
  task. No Claude is needed, because Gmail and Calendar come from the local Google login:
  ```
  wsl.exe --cd /mnt/e/PythonProject/LinkedinJobSearcher -- bash -lc \
    'CLAUDE_SCHEDULED=1 python3 .claude/skills/morning-run/scripts/morning.py run >> data/pipeline/logs/morning.log 2>&1'
  ```
  `CLAUDE_SCHEDULED=1` (and the missing TTY) make `morning.py` refuse `--pipeline`.
- **cron in WSL**, only if WSL is kept running: the same command at `30 7 * * 1-5`.
- **Via Claude** (`claude -p "/morning-run"`): only for the connector fallback. Allow
  `Skill,Read,ToolSearch,Bash(python3 .claude/skills/morning-run/scripts/morning.py:*)` plus the connectors'
  read tools. Deny `Write,Edit` and every send/draft/label/create/update/delete/respond tool. Take the
  names from `/mcp`.
- `/schedule` (cloud routine): only if the user wants it despite the above. It would need the repo and DB
  reachable from the cloud.

## Files

| Path | Role |
|---|---|
| `scripts/morning.py` | fit · jobs · followups · inbox · calendar · since · run · selftest |
| `scripts/jev_questions.py` · `scripts/jev_client.py` · `scripts/abtest.py` | Jev wording + truth table · TypeSafe client · regex-vs-Jev A/B |
| `scripts/fixtures/` | `inbox_cases.json` (classifier / matcher / proposal cases), TOMRA + Mapbox ledger-style summaries |
| `references/fit_scoring.md` | fit formula, splitting, sections, credits, calibration |
| `references/generic_terms.txt` | everyday JD words never scored |
| `references/inbox_rules.md` | Gmail query, classifier order, company matching, status proposals, ATS domain list |
| `references/tech_terms.txt` | extra JD tool terms that count as "not in library" unless they resolve exactly |
