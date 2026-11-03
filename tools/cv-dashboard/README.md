# CV dashboard

A local page that shows, for each tailored CV, which library items it uses, which JD keywords each item carries, and what is missing. It can also start a headless Claude Code run to change something and then show what changed.

It runs only on this machine. It reads `cv/profile/experience.sqlite` **read-only** and the files in each application folder. It writes only `tools/cv-dashboard/data/` (gitignored): `runs.json` (run history), `run_board.json` (the shared run board) and the lock files of `scripts/shared_lock.py`.

## Start

```bash
cd tools/cv-dashboard
npm install          # first time only (Node ≥ 22.5; uses the built-in node:sqlite)
npm run dev          # API on 127.0.0.1:5174 + Vite on 127.0.0.1:5173
```

Then open <http://localhost:5173> in Windows Chrome. WSL forwards localhost.

| Command | What it does |
|---|---|
| `npm run dev` | Starts the API (`tsx watch`) and the Vite dev server together (`concurrently`). |
| `npm run dev:live` / `npm run start:live` | Same as `dev` / `start`, with the Pipeline page connected to the real Google Sheet (`PIPELINE_SHEET_LIVE=1`). |
| `npm run build && npm start` | Builds the frontend into `dist/` and serves it from the API at <http://localhost:5174>. |
| `npm test` | Vitest: matcher parity with all six `07_external_ats.json`, the TOMRA parse, the library links and variants, and the HTTP API (with a fake `claude`). |
| `npm run typecheck` | `tsc --noEmit` over client, server, shared code and tests. |
| `npm run smoke` | Checks a running API. Add `-- --claude` to also make two real concurrent read-only Claude runs (TOMRA + Ixopay) and write `SMOKE.md`. |

Environment overrides:

- `CV_REPO_ROOT`: repo root. By default it is two levels above this folder.
- `CV_DASHBOARD_PORT`: API port, default 5174.
- `CV_PYTHON`: Python used for `library_db.py`, default `python3`.
- `CLAUDE_BIN`: Claude CLI, default `claude`.
- `CV_DASHBOARD_DATA`: folder for the run history, run board and lock files.
- `MAX_CONCURRENT_RUNS`: how many Claude runs may work at once, default 3.

## Screens

**Applications** (`#/`). One row per application in the `applications` table. Each row shows the ATS overall score as a bar, keywords found/total, layout status and page utilisation, the HR and HM scores, the verdict and the date. Click a column header to sort. Click a row, or press Enter on its link, to open that CV.

**CV view** (`#/app/<JD>`)

- **Header.** Shows the ATS overall score with its sub-scores, keywords n/N, layout status, HR, HM and verdict. Its buttons:
  - **Open PDF**
  - **DB suggestion**: runs `library_db.py pick --jd` and lines up each pick with the current picks (`kind=code` per row: same / changed / current only / suggested only). It also shows the rationale and the fit lines.
  - **JD brief**: `01_jd.md`
  - **Review**: the `04_review.md` edits table plus the ATS parse checks
  - **non-pass checks**: the checks in `06_layout.json`
- **Left pane.** The CV as blocks in CV order: header, profile, Skills rows, scope line, each card with its bullets, Mirle, education. Each block shows:
  - its library code and variant (`VI1 · lead 1`, `PS · title 0 · context 1`, a Skills row label with its item string)
  - its status (POC in amber) and its CONF refs
  - chips for the JD terms it contains; the same terms are also highlighted in the text

  The headline and contact lines are marked *not scanned*, because the ATS check does not read them. Hovering or focusing a block lights up its terms on the right.
- **Right pane.** The JD terms from `07_job_keywords.json` in groups: **Found**, **Missing — covered elsewhere**, **SoT-supported wording**, **gap**, **do not claim** and **unclassified**. The groups follow `ats_check.library_status`. Each term shows its type (req / pref / kw) and its JD count. Click a term to highlight and scroll to the blocks that contain it. If the CV does not contain it, the page says "Not on this CV" and lists the library bullets, Skills, other JDs and notes for that term. Press `/` to filter the terms and Esc to clear the selection.

Hits use the same rule as `ats_check.py` / `text_match.py`: case-insensitive, whole-word, no synonyms. They are computed on the text the ATS scan reads for that line (`shared/match.ts` is a line-by-line port). `npm test` checks that the found set equals `keyword_match.found` for all six applications.

**Ask Claude Code** (drawer)

- Every block and every missing term has an **Ask Claude** button. On a block you can also press Enter. It opens a drawer with the target filled in.
- Type the instruction in any language and press **Send** (Ctrl+Enter).
- The output streams in as it arrives: text, tool calls, tool results, the final result and the exit code.
- **Send** never gets refused: the run starts at once or waits in the queue (badge `queued #2`). While it waits you can **Remove from queue**; while it runs you can **Cancel** it. When it has finished you can **Re-run** it or start a **New request**.
- Every run you open stays in the chip row at the top of the drawer, with its own live log. Switch between them freely; each stream keeps going and keeps its output. **×** stops watching one. **Other runs** lists the remaining active runs with their last log line.
- At the end the drawer lists the `03_cv.md` lines that changed (old → new). **Reload CV** fetches the CV again and marks the changed blocks in green.
- The **History** tab lists all runs from `data/runs.json`, newest first: queued (with position), running (with the last log line), done (exit code, cost, changed lines, files edited). Queued and running entries have Remove / Cancel. Click one to open its log.
- The top bar shows `N running · M queued` while anything is active. When any run ends, the server pushes `board-changed` with the applications whose `03_cv.md` changed; an open CV view for one of them reloads and marks the changed lines in green.

## Notion links and variants

Every CV block that comes from a library pick shows a **Notion ↗** link next to its code badge. It opens the item's own page in a new tab. A bullet without a page yet links its project's page instead, labelled `Notion VI` for example. The header links the application's Notion page, when it has one, and the **Personal experiences** root page.

A block with stored alternatives has a **Variants (n)** expander. It lists the lead variants of a bullet, the title and context variants of a card, every scope line, or a skill row's label and item spellings. The variant on this CV is marked "on this CV". **Use** opens the Ask Claude drawer with that block as the target and an instruction such as `Switch VI1 to lead variant 3: "…"` already filled in.

In the JD keyword panel, the selected term shows its keyword-map page. A missing term lists its covering bullets with their links and leads. **Swap in** pre-fills `Replace the weakest bullet with PS4 using lead variant 1: "…"`.

The drawer has a **Library** panel for the current target with the same links and variants. **Insert** puts a variant's text into the instruction at the cursor. The run's prompt also names the target's Notion pages, so the headless Claude can fetch them.

All of this is read-only SQL on the library DB. The links are plain anchors, so the page makes no Notion calls. Rows without a `notion_id` simply show no link.

## Concepts, align and gaps

The library DB groups JD terms into concepts (`library_db_spec_v3.md`): term, then concept, then the bullets and skills that prove it. All of it is read-only.

- **By concept** is the default view of the keyword panel. Each concept shows its name, a claim badge (covered, SoT, gap, do not claim), its weight (w3 required, w2 preferred, w1 keyword) and found/total. Its terms keep the green found colouring. The expander lists the aliases, the evidence with direct/implied tags and Notion links, and the relations. Terms with no concept sit under **Unresolved**. **By coverage** is the old grouping.
- **Align** (next to DB suggestion) runs `library_db.py align --jd`. Every row that is not "keep" has **Ask Claude**, which opens the drawer pre-filled: a lead switch, a Skills spelling, a Codex Summary re-run, or a library-mode NEW LEAD that waits for your CONF. **Apply all lead swaps** sends the pick string with the lead ords applied.
- **Gaps** runs `library_db.py gaps --jd`. SoT-supported concepts have **Write lead / bullet** (library mode, asks for CONF first). Gap and do-not-claim concepts are display only. Unresolved JD terms have **Classify** (`resolve` + `set-concept`). **SoT lead proposals** lists each picked bullet with its missing SoT terms and the greyed auto-draft (a term list, never used verbatim); **Write lead** asks Claude for one natural lead variant, for your approval.
- The **Concepts** column on the applications page counts the JD's concepts as covered / SoT / gap. Gap includes do not claim; the tooltip splits them.
- Align and gaps results are cached per application for 10 minutes. The cache drops when any run ends or the DB file changes.
- A run on a bullet, card or term gets a *Concept context* block in its prompt: what the bullet proves, or the term's concept, JD phrases and evidence. It also gets the rule that the JD noun goes in the lead and the body keeps the tool name.

## How the Claude button works, and what it may do

`POST /api/claude` builds the prompt on the server from a fixed template. The page supplies only the instruction; the template adds the application, the target and the skill's truth rules. The server then spawns the CLI without a shell, in the repo root:

```
claude -p <prompt> --output-format stream-json --verbose --permission-mode acceptEdits \
  --allowedTools=Read,Edit,Write,Glob,Grep,Bash(python3 .claude/skills/tailor-cv/scripts/*),Bash(bash .claude/skills/tailor-cv/scripts/*),\
  Bash(python3 tools/cv-dashboard/scripts/shared_lock.py *),<the five Notion tools>
```

- **Scope.** The run can read files, and in `acceptEdits` mode it can edit and write files. The only shell commands it can run are the tailor-cv scripts. Any other Bash command, web access or MCP tool is not pre-approved, so headless mode refuses it.
- **Your Claude Code settings still apply.** The run uses your user and project settings, so a broader allow rule there would also apply to it.
- **Queue.** Runs enter a FIFO queue; up to `MAX_CONCURRENT_RUNS` (3) run at once, and never two on the same application (a second run for an application waits for the first; runs for other applications may pass it). Runs time out after 15 minutes. `DELETE /api/claude/:runId` removes a queued run or cancels a running one (SIGTERM, then SIGKILL after 5 s). A server restart marks running runs failed and queued runs cancelled.
- **Run board (watch each other).** `data/run_board.json` holds every active run and the last 10 finished ones of this server session: id, application, app dir, target, instruction, status, queue position, files edited so far (from Edit/Write calls), last 3 log lines. It is rewritten on every status change and at most every 0.5 s for log lines. Each prompt is built when the run leaves the queue and ends with a *Coordination* block: its run id, the other active runs (application, target, status, instruction), and the rules: read the board before editing shared files and again before finishing; build on another run's change to the same bullet/row, never revert it.
- **Shared lock (stay in sync).** The seed, `candidate_confirmations.md`, the SQLite DB and the Notion rows are shared by every run. `scripts/shared_lock.py` is an `fcntl` lock on `data/shared.lock` (holder in `shared.lock.holder.json`):
  - `python3 tools/cv-dashboard/scripts/shared_lock.py hold --run <id> --seconds 600` … edit … `release --run <id>`: for multi-step edits. A detached holder process keeps the lock until `release` or the deadline (max 30 min).
  - `… run --run <id> -- python3 .claude/skills/tailor-cv/scripts/library_db.py init`: one command under the lock. Only `python3|bash .claude/skills/tailor-cv/scripts/*` is accepted, so the helper does not widen the Bash allowlist. If the run already holds the lock, it runs at once.
  - `… status`: who holds it. Waiting prints the holder every 15 s and gives up after 10 minutes (exit 3).
  - The child gets `CV_DASHBOARD_RUN_ID`, the default for `--run`. When a run ends (also on cancel or timeout) the server releases a lock it still holds.
- **What the lock does not do.** It is advisory: a run that edits the seed without `hold` is not stopped. The Edit tool itself is not gated. Same-application files need no lock because the queue never runs two runs on one application. `board-changed` compares every application's `03_cv.md` with its state when the run started, so with overlapping runs a change can be reported by both.
- **Environment.** Variables that mark a parent Claude Code session (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, …) are removed from the child's environment. Auth and provider variables are kept.
- **The dashboard never writes** to the DB, the seed, CV files or Notion itself. Every change goes through the Claude run and the skill's own rebuild steps.

## 表單問答 (form answers) — replaces the Auto apply button since 2026-11-03

The candidate applies with an external tool and only needs the answers. **表單問答** (CV header and each
投遞表單 row) opens a box: paste the form's questions (one per line, or the raw form text) and press 產生回答.
`POST /api/applications/:jd/answers` `{questions}` saves `$D/08_form_questions.txt` and queues a headless
Claude run (`--model opus`, cwd = `$D`, read-only tools + `library_db.py cards|card|bullet`, no browser) with
the `/form-answers` skill. It writes `$D/08_form_answers.json` + `.md` (about 1 minute). The box lists each
answer with a 複製 button: 「需要你決定」 (salary figure, notice period, start date, relocation outside
Germany — never invented) first, then Claude-written answers (please check), then standing answers.
`GET /api/applications/:jd/answers` returns them with `runId`/`runStatus`. The same skill answers questions
pasted into a chat session.

The Auto apply fill / Confirm & submit code below is still in the server but no longer shown in the UI.

## Auto apply (fill, then a separate Confirm & submit)

The Auto apply and Live browser UI is in 繁體中文; the English names below are kept for reference.

Built for reviewing from a phone over Tailscale while the browser runs on this PC.

1. **Auto apply(自動填表)** (CV header) queues a headless run of `/job-apply:job-apply` (plugin
   `job-apply@neonwatty-plugins`, user scope; same queue/board as Ask Claude). It fills the employer's form
   with the application's CV PDF (plus a cover-letter PDF if the folder has one) and **stops at the
   review page**. It cannot submit: the prompt forbids it, `browser_run_code_unsafe` / `browser_evaluate` /
   `browser_handle_dialog` are denied, and `scripts/apply-guard.js` (Playwright `--init-script`) puts a
   native confirm() in front of every Submit/Send/"Bewerbung abschicken" click and `form.submit()`.
   A fill run that reports `submitted` is recorded as `failed`.
2. Outputs in `data/apply/<jd>/` (gitignored): `step-NN.png` (each filled step), `review.png`,
   `fields.json` (label, value, source profile/answers/inferred/blank, required), `summary.md`,
   `status.json` (`filled_awaiting_confirm` | `blocked_login` | `blocked_captcha` | `blocked_mfa` |
   `failed`, url, submitControl, timestamps, cdpEndpoint, targetId). The **投遞狀態** (Apply status) panel shows them;
   inferred rows are yellow, blank rows red, and blockers say what to do at the desk. Login, CAPTCHA and
   MFA are never bypassed.
3. **確認並送出** (Confirm & submit; only when `filled_awaiting_confirm`; two dialogs; the body must repeat
   `confirm: "SUBMIT"` and the form URL) runs `scripts/apply-submit.mjs`. It is plain CDP code with no LLM
   and no plugin. It reattaches to the same browser and tab, clicks the recorded control (accepting only
   the guard's own dialog), waits for a confirmation text, and saves `pre-submit.png` and
   `confirmation.png`. The state becomes `submitted`, or `submit_uncertain` / `submit_failed`. If the
   browser had closed, it reports `page_lost`: logins survive in the profile but the form does not, so
   fill again. After `submitted`, `daily-job-tracker/scripts/push_to_sheet.py --set-status` sets the job's
   Sheet row (matched by URL or company+title, in either tab) to 狀態 `履歷送出` and adds a dated line to
   求職過程; a 投遞表單 row with the same link gets the same 狀態 plus a "- MM/DD Sent (CV dashboard)" line; if the
   job is in neither, a row is appended with that status. It writes through the Google Sheets API
   (`sheets_api.py`, shared Google login: `google_auth.py --auth`); no Apps Script deployment is needed.

Answers: `scripts/seed_apply_answers.py` stores the candidate's standing answers in the plugin's answer memory
(`~/.job-apply/answers.json`, 0600, written through `job-apply-store.py answer-put`). The candidate consented
on 2026-10-31 to both filling and remembering them. The answers are how-did-you-hear "LinkedIn" (else Job board,
else Online), disability "No", gender "Male", data-protection consent ticked, certificates left empty, and a
salary rule: middle option of a range, 0 in a free number field, never an invented figure. The fill run
writes free-text questions itself (English, max 120 words, source `generated`). Facts may come only from
`cv/profile/library_seed.json`, `candidate_confirmations.md` and `library_db.py cards|card|bullet`, preferring
the application's `03_cv.md` projects. MaterialCheck counts as a POC. The panel marks generated and inferred
rows 「請確認」 (please confirm) with an edit box. Edits go with Confirm & submit, are saved to `edits.json`, and
`apply-submit.mjs` writes them into the page by label before it clicks. Any edit that cannot be placed
aborts the submit.

The guard gates `form.submit()` only for forms that contain a final-action button. Sites submit hidden
forms on load (Scout24 does), and a dialog there would freeze the page.

Browser: `scripts/apply-browser.sh` starts a visible Playwright Chromium. It opens as a WSLg window on the
Windows desktop, uses the persistent profile `data/apply/profile`, and exposes CDP on 127.0.0.1:9223.
It keeps running between fill and submit. Claude in Chrome is not usable from WSL here, because the
extension is not connected and the native host is on the Linux side only.
One-time setup without sudo is `npx playwright install chromium`. Then run `apt-get download libnspr4
libnss3 libasound2t64` and unpack each with `dpkg-deb -x` into `~/.job-apply-browser-libs/root`.
The plugin keeps the applicant profile in `~/.job-apply/profile.json` (0600).
Env overrides: `APPLY_CDP_PORT`, `APPLY_BROWSER_DIR`, `APPLY_BROWSER_CMD`, `APPLY_PLAYWRIGHT_MCP`,
`APPLY_SUBMIT_WAIT_MS`, `JOB_APPLY_PLUGIN_ROOT`, `CV_SHEET_PUSH`.

## Live browser (see and control the apply browser from the phone)

The **即時瀏覽器** (Live browser) button in the 投遞狀態 panel opens a full-screen view of a tab in the apply
browser. The panel shows the button for every application, even before Auto apply. On the phone,
open the dashboard over Tailscale (`https://<pc>.<tailnet>.ts.net`, the same `tailscale serve` as
the dashboard), open the application and press Live browser. It picks the tab linked to this
application, else the first tab. The picker at the top switches tabs.

- **Seeing.** The server runs a CDP screencast of the tab (JPEG quality 60, at most 1280 px wide, at most
  15 fps) and sends the frames over Server-Sent Events. Frames stay in memory and are never written to
  disk. The tab is brought to the front, because a background tab does not paint. The screencast stops
  when the last viewer closes the view.
- **Controlling.** A tap clicks that point of the page. The mouse wheel, a two-finger drag or the ↑/↓
  buttons scroll, and pinch zooms the picture. The text box with **Type** inserts text into the
  focused field, for example a salary. Enter, Tab and ⌫ send those keys. The server allows at most 30
  inputs per second per tab. Every POST passes the same Origin and JSON check as the other POSTs.
  The live routes also refuse a Host other than localhost, 127.0.0.1 or `*.ts.net`.
- **Dialogs.** A site alert, confirm or prompt, and the guard's confirm() in front of a final
  submit, opens as a sheet with Accept and Dismiss. Accepting the guard there is your OK for that submit.
  Nothing else in the dashboard accepts it.
- **After a submit there.** After a click or Enter, the page is watched for 20 s for the
  confirmation or rejection text, using the same patterns as Confirm & submit in
  `scripts/apply-confirm.mjs`. A new confirmation sets the application's status.json to `submitted`
  (`submittedVia: "live"`), saves `confirmation.png` and sets the Sheet row to 履歷送出. The site's
  "not sent" message after a submit click sets `submit_failed`. A click on the recorded submit
  button, or an accepted guard, records `submit-network.json` for 30 s.
- **Limits.** It is a picture of the desktop page, so on a phone you zoom in to read small text. File
  pickers, native select pop-ups and browser permission prompts are not in the screencast. Do those at
  the PC, or use Tab and the arrow keys on a focused select. A minimised browser window may stop
  painting; the view then shows one screenshot, and later frames come only after the window is
  restored. Env: `LIVE_MAX_FPS`, `LIVE_NET_CAPTURE_MS`.

## Pipeline (the Google Sheet job pipeline)

**Pipeline** (`#/pipeline`, top bar) shows three tabs of the job-tracker Google Sheet. It reads and
writes them only through the `daily-job-tracker` Python backend: the server runs
`push_to_sheet.py --action <name> -` (JSON on stdin and stdout), which uses the Google Sheets API
(`sheets_api.py`) with the shared Google login.

- **每日職缺** (`#/pipeline/daily`): the daily run's jobs, newest first and then by score. Each card shows
  the score (green 8–10 apply, amber 5–7 maybe, red ≤4 skip), the type, company · title (opens the job),
  place and mode, the reasons and the 處理狀態. **是 / 否** writes the 決定 column at once and rolls back
  on an error; pressing the active one again sets 未確認. Click the note to edit 備註 (Ctrl+Enter saves).
  The 決定 filter defaults to 未確認. Cards you changed stay visible until you press 重新整理.
- **興趣職缺** (`#/pipeline/interest`): **貼連結** adds a job URL with an optional 備註 (the Sheet sets
  the date and skips a URL already in 興趣職缺 or 投遞表單). The rows below get their score and state when
  the daily run has processed them.
- **投遞表單** (`#/pipeline/submissions`): the submitted or prepared applications with source, ATS, CV and
  cover-letter links, 狀態 and Notion. `app_dir` is matched against the `applications` table (with or
  without the `/v3_cards` sub-folder). A match links the CV view and shows the same **Auto apply** button.
  Each row also shows 內推狀態, the 求職過程 lines and the 履歷版本 link. The referral candidates are listed
  with the recommended one first: name · title (LinkedIn) · why. Each has 「複製完整訊息」, 「複製邀請附言」 (with
  its character count) and 「標記已送出」. That last button runs `referral_sent`, which sets 內推狀態 to
  ⚠️等待回覆 and adds a dated 求職過程 line.

**Live Sheet is opt-in.** By default the Pipeline routes do not touch any Sheet: they answer 503
「Pipeline 未連正式 Google Sheet:…」, make no network call, and the page shows that message. For real work,
start the dashboard with `npm run dev:live` (or `npm run build && npm run start:live`). These set
`PIPELINE_SHEET_LIVE=1`, and the server then adds `--live` to every `push_to_sheet.py` call. `dev` and `start` stay Sheet-free. Setting `AI_JOB_TRACKER_SHEET_CONFIG`
(tests, the fake server) also enables it, for that config only.

Config: `AI_JOB_TRACKER_SHEET_CONFIG`, default `~/.config/ai-job-tracker/sheet.json`. Credentials stay
with the Python backend; only `sheet_url` from that file reaches the page. When the Google login is missing
or expired, the routes answer 503 「Google 授權失效或未設定:在 WSL 執行 google_auth.py --auth」 (run
`google_auth.py --auth` once in WSL). Other backend errors are 502 with the Sheet error text. The
interpreter is `CV_PYTHON` (default `python3`). `npm test` uses `tools/pipeline/fake_sheets/server.py`
(the Sheets REST API in memory, config `{api_base, access_token, sheet_url}`), never the live Sheet.

## API

| Method & path | Returns |
|---|---|
| `GET /api/applications` | Rows: jd, company, title, dir, headline, country, atsOverall, keywordsFound/Total, layoutStatus, utilisation, hr, hm, verdict, date. |
| `GET /api/applications/:jd` | `cv.lines` (typed items with `pick` code/variant/status/conf, `library` Notion links + variants, `scanned`, `hits`), `keywords` (type, found, jdCount, library coverage with `notionUrl`, `bulletRefs`, `skillRefs`, items), `hits.byItem` / `hits.byTerm`, `ats`, `layout`, `review`, `jdBrief`, `pdfUrl`, `picks`, `notion` (application page, Personal experiences root). |
| `GET /api/applications/:jd/pick` | `library_db.py pick --jd` parsed into the suggested pick string, a diff against `picks <JD>`, the rationale and fit lines, plus the raw text. |
| `GET /api/applications/:jd/align` | `library_db.py align --jd` parsed into rows (line, concept, weight, JD phrase, current, action, actionKind, leadOrd), the summary line and the pick string with lead ords applied. |
| `GET /api/applications/:jd/gaps` | `library_db.py gaps --jd` parsed into SoT-supported / gap / do-not-claim / covered-not-claimable groups, plus the JD's unresolved terms. |
| `GET /api/applications/:jd/sot-leads` | `library_db.py sot-leads --jd --json` passed through as `{bullets: {code: {terms, draft, body}}, raw}`; same cache as align/gaps. |
| `GET /api/applications/:jd/pdf` | The `CV_*.pdf`, streamed. |
| `GET /api/applications/:jd/apply` | Auto apply info: `url` (saved override, else first URL in the JD files), `cvPdf`, `coverLetter`, `pluginRoot`, `status`, `help`, `canSubmit`, `submitting`, `fields`, `summary`, `screenshots`, `sheet`, `runId`/`runStatus` of a live fill run. |
| `POST /api/applications/:jd/apply` | Body `{ url? }` → `202 { runId, position, status, url }`; queues the fill run. 400 bad URL / no CV, 409 if one is already live. |
| `POST /api/applications/:jd/apply/submit` | Body `{ confirm: "SUBMIT", url }` → `200 { state, submitted, error, confirmationText }` after the click. 400 without the confirmation, 409 unless `filled_awaiting_confirm` and the URL matches. |
| `GET /api/applications/:jd/apply/screenshot/:name` | `step-NN.png`, `review.png`, `pre-submit.png` or `confirmation.png`; 404 otherwise. |
| `GET /api/apply/live/tabs` | Open tabs of the apply browser: id, title, url, linked jd, viewers, screencasting. 503 if the browser is not running. |
| `GET /api/apply/live/:targetId/stream` | SSE: `hello`, `frame` (base64 JPEG + metadata), `dialog`, `dialog-closed`, `page`, `status`, `closed`. 404 for a tab that is not in the apply browser. |
| `POST /api/apply/live/:targetId/input` | Body `{type: "click", x, y}`, `{type: "scroll", deltaY, x?, y?}`, `{type: "key", key}` or `{type: "text", text}`, with coordinates in viewport CSS px. 400 bad input, 409 while a dialog is open, 429 above 30/s. |
| `POST /api/apply/live/:targetId/dialog` | Body `{accept, promptText?}` answers the open JS dialog; 409 if none. |
| `POST /api/claude` | Body `{ jd, target: {kind, code?, text?, term?}, instruction }` → `202 { runId, position, status }`; position 0 = started, otherwise its place in the queue. Never 409. |
| `GET /api/claude/:runId/stream` | Server-Sent Events. Past events are replayed first, then live ones until the `exit` event (a queued run's stream stays open until it ends). |
| `GET /api/claude/:runId` | The full run: prompt, argv, events, cvChanges. |
| `DELETE /api/claude/:runId` | Removes a queued run or cancels a running one; 409 if it already ended. |
| `GET /api/claude/runs` | `{ running, queued, runs }` (runs newest first, each with `position`). |
| `GET /api/claude/board` | The run board (same as `data/run_board.json`). |
| `GET /api/claude/events` | SSE: `{type:"board", board}` on every board change, `{type:"board-changed", runId, jds, changedLines}` after each run. |
| `GET /api/pipeline/daily` · `/interest` · `/submissions` | `{ rows, sheetUrl }` from `pipeline_list` / `interest_list` / `submissions_list` (all rows). Submission rows also carry `jd` (resolved application key or null). 503 when the Sheet is not enabled, the config is missing or the Google login needs `google_auth.py --auth`; 502 on any other backend error. |
| `POST /api/pipeline/daily/decision` | Body `{ url, decision: "是" \| "否" \| "未確認" }` → `{ url, row }`. 400 bad URL or value, 404 when the URL is not in 每日職缺. |
| `POST /api/pipeline/daily/note` | Body `{ url, note }` (max 2000 chars; "" clears it) → `{ url, row }`. |
| `POST /api/pipeline/interest` | Body `{ url, note? }` (http(s) only) → `{ added: [{url,row}], skipped: [{url, reason, tab?}] }`. |
| `POST /api/pipeline/referral-sent` | Body `{ url, name }` → `{ url, row }` (`referral_sent` action). 400 bad URL or empty name, 404 when the URL is not in 投遞表單. |

## Layout

```
server/   node:http API (http.ts), DB access (db.ts, read-only), application assembly,
          library_db.py and claude spawners
shared/   types, the ATS matcher port (match.ts), the 03_cv.md parser, the line diff
src/      React 18 client (plain CSS, light/dark via prefers-color-scheme plus a toggle)
test/     vitest suites and a fake claude CLI
scripts/  smoke.ts, shared_lock.py (cross-run lock for shared state)
```

## Notes (2026-10-31)
- **Notion access for "Ask Claude"**: headless runs may use `notion-search`, `notion-fetch`, `notion-create-pages`,
  `notion-update-page`, `notion-get-async-task` (see `NOTION_TOOLS` in `server/claude.ts`). Without them every
  Notion call was denied ("you haven't granted it yet"). The prompt limits writes to the "Personal experiences"
  page, its four databases and the application pages under it. Denied tools now show as errors in the run log.
- **No hot reload on /mnt/e**: WSL gets no file-change events from the Windows drive, so after editing
  `server/*.ts` restart `npm run dev` (Ctrl+C, then start again).
