# CV dashboard — spec (2026-10-31)

A local TypeScript web app that shows, per job application, which library items the CV uses,
which JD keywords each item carries, what is missing, and lets the candidate ask Claude Code
(headless `claude -p`) to change something — then shows the result.

Runs on the candidate's machine only (WSL, Node 22). Opened from Windows Chrome at
`http://localhost:5173`. No external services, no telemetry, no CDN at runtime.

## Stack
- `tools/cv-dashboard/` — one npm project, TypeScript everywhere.
- Frontend: Vite + React 18 + TypeScript. Plain CSS (CSS variables, light/dark via
  `prefers-color-scheme`); no UI framework needed. Must stay readable at 1280 px and at 390 px.
- Backend: Node 22 + TypeScript (run with `tsx`), a small HTTP server (Fastify or plain `node:http`)
  on `127.0.0.1:5174`; Vite dev server proxies `/api` to it. One command: `npm run dev` starts both
  (e.g. `concurrently`). `npm run build && npm start` serves the built frontend from the backend.
- SQLite: prefer `node:sqlite` (built in, no native build); fall back to `better-sqlite3` only if
  `node:sqlite` is unusable. **Open the DB read-only.** Never write it from the dashboard.
- Bind to `127.0.0.1` only. Add `tools/cv-dashboard/node_modules/` and `dist/` to the repo `.gitignore`.

## Data sources (repo root = `/mnt/e/PythonProject/LinkedinJobSearcher`; resolve from the
dashboard folder, overridable with `CV_REPO_ROOT`)
- `cv/profile/experience.sqlite` — schema in `.claude/skills/tailor-cv/references/library_db_spec.md`
  and `library_db_spec_v2.md`. Tables used: `applications`, `application_picks`, `projects`,
  `project_titles`, `project_contexts`, `scope_lines`, `bullets`, `bullet_leads`, `skills`,
  `skill_rows`, `skill_row_items`, `keywords`, `keyword_bullets`, `keyword_skills`, `keyword_jds`.
  Read the real schema with `sqlite3 … .schema` / `PRAGMA table_info` before coding.
- Per application dir (`applications.dir`, relative to the repo root, e.g.
  `cv/applications/20261023_TOMRA_Senior-Python-Software-Engineer/v3_cards`):
  `03_cv.md` (CRLF, the CV text), `01_jd.md` (JD brief: archetype, top-5 criteria, duty phrases),
  `07_job_keywords.json` (JD terms by type), `07_external_ats.json` (`keyword_match.found/missing/percent/n_terms`,
  `ats_score.overall_score` + `sub_scores`, `parse_checks`, `repeated_terms`), `07_external_ats.md`
  (incl. the "Missing terms by library status" section), `06_layout.json` (status, checks),
  `04_review.md` (first `HR nn` / `HM nn`, VERDICT), `CV_ChiYiHsu_<Co>.pdf`, `summary.md`.
- CLI helpers you may call from the backend (spawn, no shell): `python3
  .claude/skills/tailor-cv/scripts/library_db.py <cmd>` — `picks <JD>`, `pick --jd <JD>`,
  `fit --jd <JD>` / `fit --picks "<s>"`, `status`. Treat their stdout as text to display.

## API (JSON)
- `GET /api/applications` — rows: jd, company, title, dir, headline, country, ats overall,
  keyword n/N, layout status + utilisation, HR, HM, verdict, date, `concepts: {covered, sot, gap,
  doNotClaim, unresolved}` (distinct concepts of the JD's `keyword_jds` terms by `concepts.claim`; SQL).
- `GET /api/applications/:jd` — everything for one CV:
  - `cv.lines`: `03_cv.md` lines parsed into typed items: headline, contact, summary, skill rows
    (label + items), scope line, card (title + context), bullet (lead + body), other-role bullet,
    other. For every item derived from a pick: `code` (e.g. `VI1`, `PS`, `SCOPE-ATS`, skill-row
    label), `variant` (lead/title/context ord), `status` (Production/POC/…), `conf` refs.
  - `keywords`: every term from `07_job_keywords.json` with `type` (required/preferred/keyword),
    `found` (from `07_external_ats.json`), `jdCount` (repeated_terms), and `libraryCoverage`
    (keywords table: covered / SoT-supported wording / gap / do not claim / unclassified + notes).
  - `hits`: for each CV item, the JD terms it literally contains — compute with the SAME rule as
    the ATS check (`ats_check.py`/`text_match.py`: case-insensitive whole-word, no synonyms; port
    the regex). Inverse map term → items.
  - `ats`, `layout`, `review` (HR, HM, verdict, the edits table rows if present), `pdfUrl`.
  - Library links (read-only SQL, `server/library.ts`; URL = `https://www.notion.so/` + id without
    dashes, `null` when the row has no `notion_id`):
    - every item with a pick gets `library: { notionUrl, projectCode?, projectName?,
      projectNotionUrl?, variants: [{kind, ord, text, current, conf?, note?, code?, item?}],
      bodyPreview?, skillItems? }`. Bullets: `kind:"lead"` per `bullet_leads` row + `bodyPreview`
      (bullet body), `notionUrl` from `bullets.notion_id`, project link from `projects`. Cards:
      `title` (project_titles) + `context` (project_contexts) variants, `notionUrl` = the project
      page. Scope line: every `scope_lines` row as `kind:"scope"` (with `code`). Skill rows:
      `label` variants (row label + `skill_rows.variants`), `spelling` variants (item + its
      `spellings`, `item` names it) and `skillItems: [{item, ord, notionUrl}]` from
      `skills.notion_id`. Exactly one variant per group is `current` (the one on this CV).
    - every `keywords[].library` also has `notionUrl` (`keywords.notion_id`), `bulletRefs:
      [{code, notionUrl, projectCode, projectNotionUrl, leads: [{ord, text}]}]` and `skillRefs:
      [{name, notionUrl}]`.
    - concept layer (spec v3, SQL only, `server/concepts.ts`): every `keywords[]` has `concept:
      {code, name, claim, kind} | null` (`keywords.concept_code`, else an exact `concept_aliases`
      match, NOCASE). Top-level `concepts: [{code, name, kind, claim, weight, definition, notes,
      notionUrl, jdTerms: [{term, type, found}], evidence: [{kind, code, strength, note?, notionUrl,
      projectCode?}], aliases: [string], relations: [{src, rel, dst}]}]`, one per concept this JD's
      terms resolve to; `weight` = max over its JD terms of 3 required / 2 preferred / 1 keyword;
      sorted by weight, then name.
    - `notion: { applicationUrl, rootUrl }`: the application's Notion page (`applications.notion_id`)
      and the "Personal experiences" root page.
- `GET /api/applications/:jd/pick` — output of `library_db.py pick --jd` (pick string + rationale
  + fit), parsed into rows where easy, raw text otherwise.
- `GET /api/applications/:jd/align` — `library_db.py align --jd` parsed: `rows: [{line, concept,
  weight, jdPhrase, current, action, actionKind: keep|use_lead|new_lead|spelling|summary, leadOrd?,
  proposal?, variant?, skillRow?}]`, `notes` (lines before the table), `summary` (the counts line),
  `picks` (pick string with lead ords applied), `picksUnchanged`, `raw`, `stderr`, `exitCode`.
- `GET /api/applications/:jd/gaps` — `library_db.py gaps --jd` parsed: `groups: {sotSupported, gap,
  doNotClaim, coveredNotClaimable}` of `{code, name, weight, jdTerms: [{term, type, status?}],
  evidence, notes}`, plus `unresolved: [{term, type, inKeywordMap}]` (JD terms without a concept),
  `raw`, `stderr`, `exitCode`. Align and gaps are cached per JD for 10 min; the cache drops when a
  run ends (`board-changed`) or the DB file changes.
- `GET /api/applications/:jd/sot-leads` — `library_db.py sot-leads --jd --json` as `{bullets: {code: {terms, draft, body}}, raw, stderr, exitCode}`; cached like align/gaps.
- `GET /api/applications/:jd/pdf` — streams the CV PDF.
- `POST /api/claude` — body `{ jd, target: {kind, code?, text?, term?}, instruction }` → starts a
  Claude Code run (below), returns `{ runId }`. When the target maps to library rows with Notion
  pages, the prompt ends with `Notion evidence pages for this target: <bullet url>, <project url>…`.
  Bullet / card / term targets also get a `Concept context:` block (concepts the bullet or card
  context proves with claim + strength; for a term its concept, this JD's phrases for it and the
  evidence) and the v3 rule: JD noun in the lead, tool name in the body, no semantic body rewrite.
- `GET /api/claude/:runId/stream` — Server-Sent Events: text chunks, tool-use summaries, final
  result, exit code.
- `GET /api/claude/runs` — history (persisted to `tools/cv-dashboard/data/runs.json`, gitignored).
- Live browser (2026-11-01), `server/live.ts`, for the apply browser only (CDP `127.0.0.1:APPLY_CDP_PORT`):
  `GET /api/apply/live/tabs` → `[{id, title, url, jd, viewers, screencasting}]`;
  `GET /api/apply/live/:targetId/stream` → SSE `hello | frame {seq, data (base64 JPEG), meta {deviceWidth,
  deviceHeight, offsetTop, pageScaleFactor, scrollOffsetX/Y}} | dialog {type, message, guard} | dialog-closed |
  page | status | closed` (Page.startScreencast JPEG q60, maxWidth 1280, every frame acked, ≤ LIVE_MAX_FPS 15;
  stopped when the last viewer leaves; frames only in memory);
  `POST …/:targetId/input` `{type: click|scroll|key|text, x, y, deltaY, key, text}` (viewport CSS px; ≤ 30/s,
  429 above; 409 while a dialog is open); `POST …/:targetId/dialog` `{accept, promptText?}` →
  Page.handleJavaScriptDialog. POSTs go through guardMutation; all live routes also need a local or
  `*.ts.net` Host and refuse cross-site requests. After a click or Enter the page is watched
  (APPLY_SUBMIT_WAIT_MS) for CONFIRM_RE / REJECT_RE (`scripts/apply-confirm.mjs`, shared with
  apply-submit.mjs); a new match updates the linked application's status.json (`submitted` +
  `submittedVia: "live"` + Sheet push, or `submit_failed` after a submit-type click). A click on the recorded
  submitControl or an accepted guard dialog records `submit-network.json` for 30 s.

## Claude Code integration (option A — headless)
- Spawn (no shell, argv array) in the repo root:
  `claude -p <prompt> --output-format stream-json --verbose --permission-mode acceptEdits
   --allowedTools "Read,Edit,Write,Glob,Grep,Bash(python3 .claude/skills/tailor-cv/scripts/*),Bash(bash .claude/skills/tailor-cv/scripts/*)"`
  Check `claude --help` for the exact flag names of the installed version (2.1.x) and adapt.
- Prompt template (server-side, not editable from the page except the instruction):
  "You are working in the LinkedinJobSearcher repo with the tailor-cv skill (read
  .claude/skills/tailor-cv/SKILL.md first). Application: <jd> (<dir>). Target: <kind> <code>
  '<current text>' [JD term: '<term>']. Request from the candidate: <instruction>. Follow the
  skill's truth rules (SoT = cv/profile/library_seed.json + candidate_confirmations.md; never invent
  facts). After changing library text: library_db.py init, selftest, export-md; rebuild this
  application (assemble_cv.py --jd <jd>, build_cv.sh patch + check + ats). Report what you changed."
- FIFO queue, up to `MAX_CONCURRENT_RUNS` (3) at once, never two on one application; shared run board + `scripts/shared_lock.py` for the seed, CONF file, DB and Notion (2026-10-31). Timeout 15 min, kill or dequeue on cancel (`DELETE /api/claude/:runId`).
- When a run ends, the page re-fetches the application and highlights lines whose text changed.

## UI
1. **Applications** (home): table from `/api/applications`, sortable, ATS bar, colour for
   layout PASS/WARN/FAIL and verdict. Click a row → CV view.
2. **CV view** — two panes:
   - Left: the CV as structured blocks in CV order (headline, Summary, Skills rows, scope, each
     card with its bullets, Mirle). Each block shows a small code badge (`VI1 · lead 3`), status
     badge (POC in amber), and its keyword chips (the JD terms it hits). Hover a block → highlight
     its terms on the right.
   - Right: JD keywords grouped **Found / Missing — covered elsewhere / Missing — SoT-supported
     wording / Missing — gap / do-not-claim / unclassified**, with type (req/pref/kw) and JD count.
     Click a term → scroll to and highlight the CV blocks that contain it (or say "not on this CV",
     plus which library bullets carry it per the keywords table).
   - Header: ATS overall + sub-scores, keyword n/N, layout status, HR/HM/verdict, "Open PDF",
     "DB suggestion" (shows `pick --jd` next to the current picks, diff by line).
3. **Ask Claude Code**: every block and every missing term has an "Ask Claude" button that opens a
   side drawer pre-filled with the target; the candidate types the instruction (Chinese or
   English), sends, and watches the streamed output. Buttons: Cancel, Re-run. After completion:
   "Reload" diff view of changed CV lines (old → new). History tab lists past runs.
- Copy in English; the instruction box accepts any language. Keyboard: Ctrl+Enter sends.
4. **Live browser** (Apply status panel): full-screen frame of an apply-browser tab, scaled to the
   width; tap = click, wheel / two-finger drag / ↑↓ = scroll, text box + Type, Enter / Tab / ⌫, tab picker,
   connection state, fps and tap→frame latency; a page dialog opens as a bottom sheet (Accept / Dismiss).
   Accepting the submit guard there is the candidate's own OK for that submit. Works at 390 px.

## Non-goals
No writes to the DB, the seed, CV files or Notion from the dashboard itself (all changes go
through the Claude run). No auth (localhost only). No deployment. Do not commit.

## Acceptance (write `npm test` with vitest for the pure parts + a scripted smoke check)
1. `npm run dev` starts both servers; `GET /api/applications` returns the 6 recorded rows with the
   ATS overall equal to each `07_external_ats.json` (TOMRA 80.3, Ixopay 63.6, Scout24 62.2,
   AIVenture 57.1, Mapbox 52.3, Taktile 52.3).
2. For TOMRA: the parsed CV has 6 Delta bullets with codes VI1, VI2, VI5, PS1, PS2, PS4, 6 Skills
   rows, 2 cards; the found-keyword set computed by the ported matcher equals
   `07_external_ats.json.keyword_match.found` for every application (unit test over all six).
3. Term → item map: for TOMRA, "data modelling" maps to the Data & UI Skills row; "project
   management" maps to PS4.
4. `POST /api/claude` with a harmless instruction ("Read SKILL.md and reply with the number of
   steps; change nothing") streams output and ends with exit code 0 (run once manually, record the
   output in `tools/cv-dashboard/SMOKE.md`).
5. `npm run typecheck` and `npm run build` pass with no errors.
6. README.md in the folder: start command, what each screen does, how the Claude button works and
   its permission scope.
