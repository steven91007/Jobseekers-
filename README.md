# LinkedinJobSearcher

**English** | [繁體中文](README.zh-TW.md)

A LinkedIn job-search toolkit you can use in four ways:

- **CLI** (`python main.py`): an interactive terminal app. Answer a few questions and it shows the matching jobs in a table.
- **Discord bot** (`python -m bot`): a long-running service. Register searches with slash commands, and every day at a fixed time it posts the **new jobs it has not posted before** to a channel.
- **MCP server** (the [jobseekers-mcp](https://github.com/steven91007/jobseekers-mcp) submodule): lets agents such as Claude Code call job search, visa checks and gitkb directly, with every call traced in Langfuse. See [below](#mcp-server).
- **AI job agent** (`python -m jobagent`): an agentic pipeline for AI jobs in Germany, the Netherlands and Dublin. It collects, dedupes, scores with OpenAI and writes a report. See [below](#ai-job-agent-python--m-jobagent).

The CLI and the Discord bot talk to you in Traditional Chinese; the labels quoted below are what they show.

## Installation

```bash
pip install -r requirements.txt
```

> Windows users: `tzdata` is required, not optional. Windows has no built-in IANA time-zone database, so without it `ZoneInfo("Asia/Taipei")` raises `ZoneInfoNotFoundError`.

## CLI

```bash
python main.py
```

Enter a keyword, location, work type (onsite/remote/hybrid), job type, whether you want English job descriptions only, and how many results to show. Then enter a row number to see a job's details.

### Locations and region presets

You can list several locations separated by commas and mix in **region presets**. Each preset expands to several countries, which are searched one by one and merged, because LinkedIn's guest API accepts only one location per request:

| Input | Expands to |
|---|---|
| `Nordics` / `Scandinavia` / `北歐` | Denmark, Sweden, Norway, Finland, Iceland |
| `DACH` / `德語區` | Germany, Austria, Switzerland |
| `Benelux` / `荷比盧` | Netherlands, Belgium, Luxembourg |
| `Baltics` / `波羅的海` | Estonia, Latvia, Lithuania |

For example, `Berlin, Nordics` searches six locations. The presets are defined in `linkedin_scraper.REGION_PRESETS`; add a line there to add a region.

### Visa / work-permit sponsorship check

After the results appear, the CLI asks whether to **read each job description and check whether it offers visa sponsorship**. In the Discord bot, this is the `visa_check` option. Each job costs one extra LinkedIn request, so it is slower; don't turn it on for large result sets.

The check has two layers:

1. **Rules** (offline, free): common phrasings in English, German and the Nordic languages. Negative phrases ("no sponsorship", "must already have a work permit", "EU citizens only") are checked before positive ones, because the negative sentences usually contain the positive keywords too. The result is `有` (supported), `無` (not supported) or `不明` (not stated), with the sentence it was based on.
2. **LLM assist** (optional): for jobs the rules cannot decide, if `.env` has `ANTHROPIC_API_KEY`, Claude reads the whole description and decides. Without a key those jobs stay `不明`. LLM results are marked `(LLM)` and never override a rule-based verdict.

The result appears in the table's `簽證` (visa) column, the job details, the Excel export (`簽證支持` and `簽證依據` columns: verdict and evidence), and the Discord embed (🛂✅ / 🛂❌ / 🛂❓). The check only reflects **what the posting says**; "not stated" does not mean "not offered", so confirm before applying.

## Discord bot

### 1. Create a Discord application

You need to do these steps yourself in a browser:

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** in the sidebar → **Reset Token** → copy the token (it is shown only once)
3. **OAuth2 → URL Generator** in the sidebar: tick the **`bot`** and **`applications.commands`** scopes, and the **Send Messages** and **Embed Links** bot permissions
4. Invite the bot to your server with the generated URL

No privileged gateway intents are needed.

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and set at least `DISCORD_TOKEN`. `.env` is excluded by `.gitignore` and never committed.

Setting `DISCORD_DEV_GUILD_ID` (your server's ID) is recommended: slash commands then sync to that server **immediately**. Left empty, they register globally, which can take up to an hour to appear.

Set `JOBBOT_OWNER_ID` to your Discord user ID to get a DM when the scraper breaks.

### 3. Run

```bash
python -m bot
```

Keep the process running. Logs go to the terminal and to `logs/bot.log`.

### Commands

| Command | What it does |
|---|---|
| `/jobs subscribe` | Create a subscription in the channel. **The first run records the jobs that already exist as a baseline without posting them**; after that only new jobs are posted. `location` accepts region presets (such as `Nordics`); `visa_check: True` marks visa support on each job before posting |
| `/jobs list` | List this server's subscriptions and their health |
| `/jobs preview` | Run a search now without creating a subscription or touching the dedupe records; use it to test filters |
| `/jobs run` | Run a real push right now (deduped, posts to the channel) |
| `/jobs status` | Schedule, last cycle, and each subscription's raw/new/outcome |
| `/jobs toggle` | Pause or resume a subscription |
| `/jobs remove` | Delete a subscription together with its posted-job records |

`subscribe`, `run`, `toggle` and `remove` require the **Manage Server** permission by default; server admins can change this per command under Server Settings → Integrations.

## Design notes

**Scheduling is a ticker plus a database claim, not a daily timer.** Every 5 minutes the bot checks whether the push time has passed and whether today has not run yet. A unique key in the `run_marker` table guarantees one run per day. If the machine was asleep at push time, it catches up after waking, and restarts or reconnects never push twice.

**The scraper tells "no new jobs today" apart from "the scraper is broken".** `search_jobs_strict()` classifies each result as `OK`, `EMPTY_OK`, `EMPTY_SUSPICIOUS`, `PARSE_DRIFT`, `BLOCKED`, `RATE_LIMITED` or `TRANSPORT_ERROR`. `PARSE_DRIFT` is the canary: a normal page with job cards, but no job ID could be parsed, which means LinkedIn changed its HTML. Without this, the bot would quietly post nothing and look healthy.

**Failures are reported**: a channel message, a DM to the owner, and the bot's status text. Alerts are sent only on the healthy → broken transition, so they don't repeat, and recovery is announced too.

**Deduplication is per subscription**, so two channels subscribed to the same search both receive the jobs. Posted jobs are remembered for 90 days (`JOBBOT_SEEN_RETENTION_DAYS`). Don't go below 60: LinkedIn resurfaces old postings, and a short retention causes duplicate posts.

**Requests to LinkedIn are strictly sequential**, with random gaps between subscriptions. Two blocks in a row stop the rest of the day's work. The guest API has no authentication, and hitting it too hard gets your IP blocked for hours.

## Tests

```bash
python tests/test_pusher.py
python tests/test_visa.py
python tests/test_gitkb.py
python -m pytest tests/test_mcp.py   # the submodule's MCP checks; needs uv pip install -e ./jobseekers-mcp
```

`test_pusher.py` checks the push logic offline with fake Discord objects: the cold-start baseline, dedupe, the per-run cap and overflow, never marking a job as seen when sending failed, failure alerts and recovery, the circuit breaker, and exactly-once scheduling. It needs no token and never contacts LinkedIn or Discord.

`test_visa.py` checks region-preset expansion and the visa rules, including that the LLM is only called for undecided jobs. `test_gitkb.py` runs the whole knowledge-base flow in a throwaway git repo. `test_mcp.py` runs jobseekers-mcp's checks against this project's code: every tool is called over the MCP protocol with a fake LinkedIn, and the Langfuse trace shape and masking are checked. It is skipped when the submodule is not checked out or not installed. None of the tests needs the network.

## gitkb: a knowledge base of the git history

`knowledge/` holds a note for every commit: one `knowledge/commits/<sha256>.md` per commit and one `knowledge/changes/<sha256>.md` per file changed in it. Each file name is the sha256 of the canonical text that was summarized (the commit header plus the diff), so a note proves which change it describes. `knowledge/index.db` is a SQLite index (links between commits, files and notes, plus FTS5 full-text search). It is **not committed** and can always be rebuilt from the Markdown.

Claude Code writes the summaries in the conversation; no LLM API is called:

```bash
python -m gitkb pending        # export unsummarized commits to knowledge/pending.json
#  -> in Claude Code, type /gitkb: it reads pending.json and writes knowledge/summaries.json
python -m gitkb import knowledge/summaries.json   # render the notes and index them
python -m gitkb log            # indexed commits
python -m gitkb show 0e83df7   # a note, by git sha (prefix ok) or note sha256
python -m gitkb search visa    # full-text search over the summaries
python -m gitkb history main.py   # every change to one file
python -m gitkb rebuild-index  # delete index.db and rebuild it from the Markdown
```

Requires Python 3.10+. `build --dry-run` writes placeholder notes first (with the same file names as the real ones); a later `import` overwrites them in place.

Claude Code can also do this through the MCP server, without the pending.json / summaries.json scratch files: pick the `gitkb_update` prompt of the `jobseekers` server, or just ask it to "update the knowledge base with gitkb_pending and gitkb_import_summaries".

## MCP server

The MCP server is maintained in its own repository, [jobseekers-mcp](https://github.com/steven91007/jobseekers-mcp), and mounted here as a git submodule at `jobseekers-mcp/`. It wraps this project's core features as [MCP](https://modelcontextprotocol.io/) tools that agents such as Claude Code can call. It copies no code: it imports `linkedin_scraper.py`, `visa.py`, `gitkb/` and `bot/db.py` from here, so the CLI, the Discord bot and the MCP server always share the same logic.

| Tool | What it does |
|---|---|
| `search_jobs` / `get_job_detail` | LinkedIn job search (newest first; multiple locations, region presets and `posted_within`) and the details of one job |
| `check_visa` | Check up to 15 jobs for visa sponsorship. Jobs the rules cannot decide are handed to the calling model, so the server needs no LLM key |
| `gitkb_search` / `gitkb_show` / `gitkb_log` / `gitkb_history` | Query the git knowledge base |
| `gitkb_pending` / `gitkb_import_summaries` | Replace the scratch-file flow of `/gitkb` |
| `list_subscriptions` / `bot_status` | The Discord bot's subscriptions and push status (read-only) |

Every tool call is one trace in Langfuse (keys are read from this project's `.env`). For the trace layout, settings and development, see the [jobseekers-mcp README](https://github.com/steven91007/jobseekers-mcp#readme).

### Getting the submodule

```bash
git clone --recurse-submodules https://github.com/steven91007/Jobseekers-.git
# if you already cloned:
git submodule update --init
```

### Usage

The server is registered in `.mcp.json` at the repository root. It runs with [uv](https://docs.astral.sh/uv/) on Python 3.13 and installs `./jobseekers-mcp` in editable mode, so no manual venv is needed. Open Claude Code in the repository and approve the `jobseekers` server when asked the first time; `/mcp` shows its connection status.

Check the installation, the Langfuse connection, and which project path it found:

```bash
uv run --no-project --python 3.13 --with-editable ./jobseekers-mcp python -m mcp_server --check
```

### Updating the MCP server

The submodule is pinned to a commit. To take a new version of jobseekers-mcp:

```bash
git submodule update --remote jobseekers-mcp      # or: cd jobseekers-mcp && git checkout v1.1.0
python -m pytest tests/test_mcp.py                # run the MCP checks against this project's code
git add jobseekers-mcp && git commit -m "Bump jobseekers-mcp to <version>"
```

Changes to the MCP server itself go to the jobseekers-mcp repository; here you only move the submodule pointer. If you change the interface of `linkedin_scraper.py`, `visa.py`, `gitkb/` or `bot/db.py`, run `tests/test_mcp.py`, because the MCP server depends on them directly.

---

## AI job agent (`python -m jobagent`)

An agentic pipeline for the newest **AI jobs in Germany, the Netherlands and Dublin**. It gathers postings from LinkedIn and from the public job boards of 76 AI companies, dedupes them across sources, and scores each job against your profile with OpenAI. A research agent then finds companies the watchlist misses, and the run writes a ranked Markdown and Excel report. Every step is traced in **Langfuse**.

### Architecture

```
 python -m jobagent run
 │
 ├─ 1. collect (deterministic, parallel)
 │     ├─ LinkedIn guest search: {Germany, Netherlands, Dublin} x 8 queries (mostly AI Engineer variants),
 │     │  posted within --since (default 24h)
 │     └─ Watchlist job boards: 76 AI companies (jobagent/companies.py): Greenhouse, Ashby, Lever,
 │        Personio, Recruitee, SmartRecruiters, Workday, Teamtailor, schema.org JobPosting pages;
 │        roles posted within the last 7 days
 ├─ 2. normalize + dedupe → SQLite (data/jobagent.db)
 │     region classifier, AI-title filter, cross-source fuzzy dedupe (job board beats LinkedIn), NEW flag
 ├─ 3. score (OpenAI structured outputs, parallel)
 │     JD + profile.md → fit_score, apply_priority, language/visa blockers, gaps, pitch
 ├─ 4. research agent (OpenAI Responses API tool loop, max 12 turns)
 │     tools: list_jobs, get_job_detail, search_linkedin, detect_company_board,
 │            check_company_board, add_company_candidate, web_search (OpenAI built-in)
 │     → extra jobs, new company candidates, written briefing
 └─ 5. report → reports/jobs_<date>.md + .xlsx + console table
 Langfuse: one trace per run; a span per source, job score and tool call; OpenAI calls as generations
```

Steps 1, 2 and 5 always run. Steps 3 and 4 need `OPENAI_API_KEY`. Langfuse tracing needs `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`. Without them every tracing call is a no-op, and a Langfuse outage never fails a run.

### Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env              # add OPENAI_API_KEY, LANGFUSE_* keys
cp profile.example.md profile.md  # describe yourself: skills, languages, visa needs
.venv/bin/python -m jobagent doctor
```

`doctor` checks that your OpenAI key can use the model you set in `OPENAI_MODEL`. It also checks your profile, the Langfuse keys and LinkedIn access.

**Fill in `profile.md`.** Every score compares a job against it. Scoring and the research agent refuse to run when the profile is missing or still the unedited template, because scores against the template describe a fictional candidate and look plausible. `profile.md` is gitignored; to share one profile across worktrees or checkouts, set `JOBAGENT_PROFILE` to its absolute path. Each assessment stores a fingerprint of the profile it was made with, so after you edit the profile, `python -m jobagent rescore` re-scores only the jobs scored with an older version.

### Commands

| Command | What it does |
|---|---|
| `python -m jobagent run` | Full run: collect, score, research, report. Options: `--since 24h\|7d` (default `24h`), `--regions DE,NL,IE`, `--no-llm`, `--no-agent`, `--max-score N` |
| `python -m jobagent report` | Re-render the latest report from the database |
| `python -m jobagent search "RAG engineer" --region NL` | One ad-hoc LinkedIn search |
| `python -m jobagent companies verify` | Check every watchlist job board and count relevant regional roles |
| `python -m jobagent companies detect <careers page URL> [--name N]` | Find which job board a company uses, verify it, and print a line to paste into the watchlist |
| `python -m jobagent companies candidates` | Companies the agent proposed, for you to add to `jobagent/companies.py` |
| `python -m jobagent feedback <job_key> --label applied` | Record your verdict; it is sent to Langfuse as a `human_label` score on that job's trace |
| `python -m jobagent rescore [--all] [--limit N]` | Re-score jobs whose score was made with another version of your profile (`--all`: every scored job); prints the score and priority changes |
| `python -m jobagent doctor` | Check the profile, keys, model access, Langfuse and LinkedIn |

### Adding companies

Run `companies detect` with a company's careers page. It looks for an embedded or linked job board: Greenhouse, Ashby, Lever, Personio, Recruitee, SmartRecruiters, Workday or Teamtailor. It then looks for schema.org `JobPosting` data on the page and its job pages. If the page shows nothing, it guesses the board slug from the company name. Every candidate is verified with a live fetch. Paste the printed `Company(...)` line into `WATCHLIST` in `jobagent/companies.py`.

Some sites render jobs only with JavaScript and publish no structured data, for example Zalando, Booking.com and ASML. Detection cannot read those. They need LLM-based page extraction, which is not built. Their roles often still appear through the LinkedIn search.

### Reading the report

Nothing posted more than 7 days ago is listed anywhere (`JOBAGENT_MAX_AGE_DAYS`). Each region has two tables. The first lists every job posted within `--since`, which defaults to the last 24 hours. The second lists roles posted earlier but still within 7 days. The console prints every job in the `--since` window. 🆕 marks jobs first seen in this run. "Apply now" collects scored jobs with priority `now`. The Lang column flags postings that require German or Dutch.

Three numbers rank each job:

| Column | Meaning |
|---|---|
| Fresh | 0 to 100 from the posting's age. It is 100 right after posting and halves every 24 hours (`JOBAGENT_FRESHNESS_HALF_LIFE_HOURS`), so 12h is 71, 24h is 50, 3 days is 12. |
| Fit | The LLM's 0 to 100 match against `profile.md`. |
| Rank | 70% fit plus 30% fresh (`JOBAGENT_FRESHNESS_WEIGHT`), plus 5 for AI Engineer titles. Empty until the job is scored. |

Scored jobs sort by rank. Unscored jobs follow, AI Engineer titles first and then newest first. The scorer also works through AI Engineer titles first and then the newest postings. 🎯 marks AI Engineer titles such as "AI Engineer", "Applied AI Engineer", "LLM Engineer" or "Software Engineer - AI".

LinkedIn cards say "5 hours ago", so LinkedIn ages are exact to the hour. In 24h mode each LinkedIn query fetches up to 100 results (`JOBAGENT_LINKEDIN_PER_QUERY_24H`). If a query hits that cap, the console warns and the report's run details list it, because the window may hold more.

### Observability with Langfuse

Tracing follows the [Langfuse best practices](https://langfuse.com/docs/observability/best-practices). Each run is one trace, `run-job-search`. It carries session `jobagent-<date>`, `user_id` from `JOBAGENT_USER_ID`, and tags `jobagent`, `daily-run` and `region:<code>`. Its version is the prompt version.

```
run-job-search                      span       input: the search request · output: top jobs, briefing, report path
├── collect-jobs                    span
│   ├── collect-job-board           retriever  one per watchlist company (metadata: company, ats)
│   └── collect-linkedin-jobs       retriever  one per region x query (metadata: region, query)
├── store-jobs                      span       collected -> unique -> new
├── score-jobs                      span       metadata.phase: initial | agent-followup
│   └── score-job                   chain      scores: fit_score, apply_priority, human_label
│       ├── fetch-job-description   retriever
│       └── assess-job-fit          generation model, tokens, cost, reasoning summary
├── research-jobs                   agent      input: task prompt · output: briefing
│   ├── research-agent-step         generation one per turn (metadata: turn)
│   └── list_jobs, get_job_detail, check_company_board   retriever
│       search_linkedin, add_company_candidate, web_search tool (web_search lists its sources)
└── write-report                    span
```

- **Names are stable.** Job keys, companies, regions and turn numbers live in metadata, so dashboards and LLM-as-a-judge evaluators can target a name across runs. All names are defined in `NAMES` in `jobagent/observability.py`. Treat them as an API.
- **Reasoning is captured.** OpenAI calls request a reasoning summary, so each generation shows the model's thinking. The code falls back automatically if your organization or model doesn't allow summaries.
- **Sensitive data is masked** at export with `mask_otel_spans`. This covers emails, `+country` phone numbers and API-key-like strings. OpenAI's encrypted reasoning blobs are also dropped as noise. Set `JOBAGENT_LANGFUSE_MASK=0` to turn masking off.
- **Environment** defaults to `production`. Set `JOBAGENT_ENV=development` while experimenting, so test runs stay out of your real dashboards.
- **Failures are visible.** Blocked sources are marked `WARNING` or `ERROR` with the reason, and agent errors mark the `research-jobs` observation.

Use `feedback` to label jobs you applied to or rejected. The label is attached as a `human_label` score to that job's `score-job` observation. In Langfuse you can then compare `fit_score` against your labels and tune `profile.md` or the scorer prompt. Bump `PROMPT_VERSION` in `jobagent/llm/prompts.py` whenever you edit a prompt.

### Tests

```bash
.venv/bin/python -m pytest tests/        # offline: sources, store, report, agent loop, Langfuse span tree
.venv/bin/python tests/test_pusher.py    # the Discord bot's scenario script
```
