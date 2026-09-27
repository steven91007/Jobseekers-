# LinkedinJobSearcher

LinkedIn 職缺搜尋工具，有兩種用法：

- **CLI**（`python main.py`）—— 互動式終端機介面，問答式輸入條件後顯示表格。
- **Discord bot**（`python -m bot`）—— 常駐服務，用 slash 指令登記追蹤條件，每天固定時間自動把**沒推播過的新職缺**貼到指定頻道。

## 安裝

```bash
pip install -r requirements.txt
```

> Windows 使用者注意：`tzdata` 是必要依賴，不是可選的。Windows 沒有內建 IANA 時區資料庫，少了它 `ZoneInfo("Asia/Taipei")` 會直接拋 `ZoneInfoNotFoundError`。

## CLI

```bash
python main.py
```

依序輸入關鍵字、地點、工作型態、工作類型、是否只要英文 JD、筆數，接著輸入編號可查看職缺詳情。

## Discord bot

### 一、建立 Discord 應用程式

這幾步需要你本人在瀏覽器完成：

1. 到 [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. 左側 **Bot** → **Reset Token** → 複製 token（只會顯示一次）
3. 左側 **OAuth2 → URL Generator**，scope 勾選 **`bot`** 與 **`applications.commands`**，
   Bot Permissions 勾選 **Send Messages** 與 **Embed Links**
4. 用產生的網址把 bot 邀請進你的伺服器

不需要開啟任何 Privileged Gateway Intent。

### 二、設定

```bash
cp .env.example .env
```

編輯 `.env`，至少填入 `DISCORD_TOKEN`。`.env` 已被 `.gitignore` 排除，不會進版控。

建議也填 `DISCORD_DEV_GUILD_ID`（你的伺服器 ID）—— 填了 slash 指令會**立即**同步到該伺服器；留空則註冊為全域指令，最多要等 1 小時才會出現。

`JOBBOT_OWNER_ID` 填你的 Discord 使用者 ID，爬蟲失效時會私訊你。

### 三、啟動

```bash
python -m bot
```

保持這個進程開著。log 會同時輸出到終端機與 `logs/bot.log`。

### 指令

| 指令 | 說明 |
|---|---|
| `/jobs subscribe` | 在頻道建立訂閱。**首次會把目前既有職缺記為基準線但不推播**，之後只推新的 |
| `/jobs list` | 列出本伺服器的訂閱與健康狀態 |
| `/jobs preview` | 立即試搜，不建立訂閱、不影響去重紀錄 —— 用來測條件 |
| `/jobs run` | 立刻執行一次真正的推播（會去重、會發文） |
| `/jobs status` | 排程時間、上次 cycle、每個訂閱的 raw/new/outcome |
| `/jobs toggle` | 暫停／恢復訂閱 |
| `/jobs remove` | 刪除訂閱（連同其已推播紀錄） |

`subscribe` / `run` / `toggle` / `remove` 預設需要 **Manage Server** 權限；伺服器管理員可在「伺服器設定 → 整合」逐一調整。

## 設計上值得知道的幾件事

**排程是「ticker + 資料庫 claim」而不是每日定時器。** bot 每 5 分鐘檢查一次「是否已過推播時間、今天是否還沒跑過」，用 `run_marker` 資料表的唯一鍵保證一天只跑一次。這樣桌機在推播時間正在睡眠時，醒來後仍會補跑，而重啟或斷線重連也不會重複推播。

**爬蟲會區分「今天沒有新職缺」和「爬蟲壞掉了」。** `search_jobs_strict()` 會把結果分類成 `OK` / `EMPTY_OK` / `EMPTY_SUSPICIOUS` / `PARSE_DRIFT` / `BLOCKED` / `RATE_LIMITED` / `TRANSPORT_ERROR`。其中 `PARSE_DRIFT` 是金絲雀 —— 抓到了正常網頁、有卡片，卻解析不出任何 job id，代表 LinkedIn 改版了。沒有這層分類，bot 會安靜地什麼都不推、看起來一切正常。

**失效會主動通知**：頻道訊息 ＋ 擁有者私訊 ＋ bot 的狀態列文字。通知只在「健康→故障」的狀態轉換時發送，不會重複洗版；恢復時也會通知。

**去重是 per-subscription 的**，所以兩個頻道訂閱同樣條件時兩邊都收得到。已推播紀錄保留 90 天（`JOBBOT_SEEN_RETENTION_DAYS`，不建議低於 60 —— LinkedIn 會讓舊職缺重新浮上來，保留太短會造成重複推播）。

**對 LinkedIn 的請求是嚴格序列的**，訂閱之間有隨機間隔；連續兩次被封鎖就中止當天剩餘的工作。這個 guest API 沒有認證，打太兇會被 IP 封鎖數小時。

## 測試

```bash
python tests/test_pusher.py
```

離線執行，用假的 Discord 物件驗證推播邏輯：冷啟動基準線、去重、單次上限與溢出處理、送出失敗時不可標記為已看過、失效通知與恢復、熔斷器、排程的 exactly-once。不需要 token，也不會連上 LinkedIn 或 Discord。

---

## AI job agent (`python -m jobagent`)

An agentic pipeline for the newest **AI jobs in Germany, the Netherlands and Dublin**. It gathers postings from LinkedIn and from the public job boards of 76 AI companies, dedupes them across sources, and scores each job against your profile with OpenAI. A research agent then finds companies the watchlist misses, and the run writes a ranked Markdown and Excel report. Every step is traced in **Langfuse**.

### Architecture

```
 python -m jobagent run
 │
 ├─ 1. collect (deterministic, parallel)
 │     ├─ LinkedIn guest search: {Germany, Netherlands, Dublin} x 8 AI role queries, posted within --since
 │     └─ Watchlist job boards for 76 AI companies (jobagent/companies.py): Greenhouse, Ashby, Lever,
 │        Personio, Recruitee, SmartRecruiters, Workday, Teamtailor, schema.org JobPosting pages
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

`doctor` checks that your OpenAI key can use the model you set in `OPENAI_MODEL`. It also checks the Langfuse keys and LinkedIn access.

### Commands

| Command | What it does |
|---|---|
| `python -m jobagent run` | Full run: collect, score, research, report. Options: `--since 24h\|7d\|30d`, `--regions DE,NL,IE`, `--no-llm`, `--no-agent`, `--max-score N` |
| `python -m jobagent report` | Re-render the latest report from the database |
| `python -m jobagent search "RAG engineer" --region NL` | One ad-hoc LinkedIn search |
| `python -m jobagent companies verify` | Check every watchlist job board and count relevant regional roles |
| `python -m jobagent companies detect <careers page URL> [--name N]` | Find which job board a company uses, verify it, and print a line to paste into the watchlist |
| `python -m jobagent companies candidates` | Companies the agent proposed, for you to add to `jobagent/companies.py` |
| `python -m jobagent feedback <job_key> --label applied` | Record your verdict; it is sent to Langfuse as a `human_label` score on that job's trace |
| `python -m jobagent doctor` | Check keys, model access, Langfuse and LinkedIn |

### Adding companies

Run `companies detect` with a company's careers page. It looks for an embedded or linked job board: Greenhouse, Ashby, Lever, Personio, Recruitee, SmartRecruiters, Workday or Teamtailor. It then looks for schema.org `JobPosting` data on the page and its job pages. If the page shows nothing, it guesses the board slug from the company name. Every candidate is verified with a live fetch. Paste the printed `Company(...)` line into `WATCHLIST` in `jobagent/companies.py`.

Some sites render jobs only with JavaScript and publish no structured data, for example Zalando, Booking.com and ASML. Detection cannot read those. They need LLM-based page extraction, which is not built. Their roles often still appear through the LinkedIn search.

### Reading the report

Each region has two tables. The first lists jobs posted within `--since`. The second lists roles that are still open at watchlist companies but were posted earlier. 🆕 marks jobs first seen in this run. "Apply now" collects scored jobs with priority `now`. The Lang column flags postings that require German or Dutch.

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
