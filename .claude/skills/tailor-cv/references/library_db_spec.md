# Experience library DB — spec (2026-10-29)

Goal: replace the hand-edited `cv/profile/bullet_library.md` with a relational store the agent
queries, mirrored 1:1 in Notion ("Personal experiences" page). SQLite is the local source of
truth; the markdown becomes a generated, read-only export.

## Files
- Seed (hand-maintained until the DB is live, then generated): `cv/profile/library_seed.json`
- DB: `cv/profile/experience.sqlite` (gitignored with the rest of `cv/`)
- Script: `.claude/skills/tailor-cv/scripts/library_db.py` (WSL `python3`, stdlib only — sqlite3, json, argparse; no third-party deps)
- Export: `cv/profile/bullet_library.generated.md` (never edited by hand)

## Schema (SQLite)
```
projects(code PK, name, employer, period, status, domains, metrics, best_for, constraints, evidence, notion_id)
project_titles(project_code FK, title, ord)
project_contexts(project_code FK, text, conf, note, ord)          -- ord 0 = default
scope_lines(code PK, text, conf, note)
bullets(code PK, project_code FK, status, body, capabilities, keywords_carried, metric, conf, constraints, evidence, chars, notion_id)
bullet_leads(bullet_code FK, lead, ord)                            -- ord 0 = default lead
skills(name PK, category, esco, variants, conf, on_cv, notion_id)
skill_rows(label PK, variants, ord); skill_row_items(row_label FK, item, ord)
keywords(term PK, type, coverage, esco, spelling, freq, notes, notion_id)
keyword_bullets(term FK, bullet_code FK); keyword_skills(term FK, skill_name FK); keyword_jds(term FK, jd)
applications(jd TEXT PK, company, title, dir, created)             -- which JD used which picks
application_picks(jd FK, kind 'scope'|'card'|'bullet'|'skill_row', code, variant_ord, lead, position)
```
Foreign keys ON; `chars` = len(default lead + " — " + body) + 4 (bold markers), computed on load.

## CLI (`python3 library_db.py <cmd>`), all output UTF-8, concise, agent-oriented
- `init [--seed path] [--db path]` — (re)build the DB from the seed. Idempotent: drop + create.
- `cards [--for <archetype>]` — one line per project: code, name, status, default title, metrics, best_for.
- `card <CODE>` — titles, context variants (with ord + note), constraints, then its bullets (code · default lead · chars · capabilities · constraints).
- `bullet <CODE>` — full record incl. all leads, keywords carried, CONF, evidence.
- `leads <CODE>` — lead variants only.
- `skills [--category X] [--rows]` — items table, or the 6 default Skills rows rendered as the CV lines (`**Label:** a · b · c`, with label variants noted).
- `keywords [--jd TOMRA] [--coverage gap|covered|...] [--term X]` — term, type, coverage, spelling, bullets, skills.
- `match <path/to/07_job_keywords.json>` — for each term in the JSON (required/preferred/keywords): exact-match lookup in `keywords`; if known → print coverage + covering bullets/skills + spelling; if unknown → print `NEW` so the writer classifies it. Summary line: known/new counts and which bullets cover the most required terms (ranked) → this is the pick helper.
- `render --scope <code> --card <CODE>[:title_ord[:context_ord]] --bullet <CODE>[:lead_ord] ... [--skills-row <label>[:variant]] ...` — print the Delta block + Skills rows exactly in the `03_cv.md` format (`**Title** — context`, `- **Lead —** body`, `**Label:** items`), so `03_cv.md` is assembled from the DB, never typed.
- `record --jd TOMRA --company ... --title ... --dir ... --picks "scope=SCOPE-ATS;card=VI:0:0;bullet=VI1:0;bullet=VI2:1;..."` — save the picks into `applications` / `application_picks`.
- `export-md [--out path]` — regenerate `bullet_library.generated.md` in the current library layout (Summary facts stay as a verbatim section copied from the seed `_meta`? no — keep a `facts` list in the seed and export it).
- `notion-payload <projects|bullets|skills|keywords>` — print the JSON array of `properties` objects for Notion `create-pages` (same field names as the Notion databases; relations as Notion page URLs built from `notion_id` of the related rows). Used by the main agent to push rows through the Notion MCP tool.
- `set-notion-id <table> <code> <id>` — store the Notion page id after a push.

## Acceptance
1. `init` then `render` with these picks reproduces the current TOMRA Delta block + Skills rows byte-for-byte (except the Summary line, which Codex writes):
   scope=SCOPE-ATS · card=VI:0:0 with VI1:0, VI2:1, VI3:1 · card=PS:0:1 with PS1:1, PS2:1 · card=MC:0:1 with MC4:0 · skills rows: Languages & backend, AI — computer vision (variant "Machine learning & computer vision", items minus DINO), MLOps & LLMOps, Testing & deployment (variant "DevOps & testing", items PyTest unit tests · Git (version control) · Docker · GitLab CI/CD · on-prem / air-gapped deployment), Data & UI (MinIO (object storage)), AI — LLM & agents.
   Reference file: `cv/applications/20261023_TOMRA_Senior-Python-Software-Engineer/v3_cards/03_cv.md` lines 7–25 (CRLF). Write a `diff` subcommand or test that prints any differing line.
2. `match` on `…/v3_cards/07_job_keywords.json` reports 0 NEW terms and the same coverage split as `07_external_ats.md` (42 covered / 18 gap-or-do-not-claim).
3. `export-md` produces a file that contains every bullet code and every CONF reference present in the seed.
4. `python3 -m pytest` not required; a `selftest` subcommand running 1–3 is enough.

## Non-goals
- No Notion API calls from the script (no token); only payload generation.
- No change to build_cv.py / layout_check.py / ats_check.py.
- Do not edit `bullet_library.md` or `candidate_confirmations.md`.
