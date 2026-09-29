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

### 地點與地區預設

地點可以用逗號分隔多個，其中可混用**地區預設**，會自動展開成多個國家逐一搜尋後合併（LinkedIn 的 guest API 一次只接受一個地點）：

| 輸入 | 展開為 |
|---|---|
| `北歐` / `Nordics` / `Scandinavia` | Denmark, Sweden, Norway, Finland, Iceland |
| `德語區` / `DACH` | Germany, Austria, Switzerland |
| `荷比盧` / `Benelux` | Netherlands, Belgium, Luxembourg |
| `波羅的海` / `Baltics` | Estonia, Latvia, Lithuania |

例如 `Berlin, 北歐` 會搜尋六個地點。預設定義在 `linkedin_scraper.REGION_PRESETS`，要加新的地區就往裡面加一行。

### 簽證／工作許可支持檢查

搜尋結果出來後，CLI 會問要不要**逐筆讀取職缺描述並判斷是否提供簽證支持**（Discord 用 `visa_check` 參數）。每筆會多一次 LinkedIn 請求，所以較慢，也請不要對太多筆開啟。

判斷分兩層：

1. **規則**（離線、免費）：比對英文、德文與北歐語言的常見句型。「不提供／須已有工作許可／限 EU 公民」這類否定句先於肯定句比對，因為否定句通常也包含肯定關鍵字。結果為 `有` / `無` / `不明`，並附上依據的原句。
2. **LLM 輔助**（選填）：規則判不出來的職缺，若 `.env` 有 `ANTHROPIC_API_KEY`，會交給 Claude 讀整篇描述再判一次；沒有金鑰就維持「不明」。LLM 的結果會標示 `(LLM)`，不會覆蓋規則已判定的結果。

結果會出現在表格的「簽證」欄、職缺詳情、Excel 匯出（「簽證支持」「簽證依據」兩欄）與 Discord embed（🛂✅ / 🛂❌ / 🛂❓）。判定只反映職缺描述**有沒有寫**，「不明」不代表不提供，投遞前請自行確認。

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
| `/jobs subscribe` | 在頻道建立訂閱。**首次會把目前既有職缺記為基準線但不推播**，之後只推新的。`location` 可用地區預設（如 `北歐`）；`visa_check: True` 會在推播前逐筆標示簽證支持 |
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
python tests/test_visa.py
python tests/test_gitkb.py
```

離線執行，用假的 Discord 物件驗證推播邏輯：冷啟動基準線、去重、單次上限與溢出處理、送出失敗時不可標記為已看過、失效通知與恢復、熔斷器、排程的 exactly-once。不需要 token，也不會連上 LinkedIn 或 Discord。

`test_visa.py` 檢查地區預設展開與簽證規則（含 LLM 只在「不明」時才被呼叫）；`test_gitkb.py` 在暫存 git repo 裡跑完整的知識庫流程。都不需要網路。

## gitkb：git 歷史知識庫

`knowledge/` 底下是每個 commit 的知識筆記：每個 commit 一份 `knowledge/commits/<sha256>.md`，commit 裡的每個檔案變更一份 `knowledge/changes/<sha256>.md`。檔名是「被摘要的那段標準化文字」（commit 標頭 + diff）的 sha256，所以筆記本身就能證明它描述的是哪段變更。`knowledge/index.db` 是 SQLite 索引（commit、檔案、筆記之間的對應，加上 FTS5 全文搜尋），**不進版控**、隨時可從 md 重建。

摘要由 Claude Code 在對話中撰寫，不呼叫任何 LLM API：

```bash
python -m gitkb pending        # 匯出還沒摘要的 commit 到 knowledge/pending.json
#  -> 在 Claude Code 裡輸入 /gitkb，它會讀 pending.json、寫 knowledge/summaries.json
python -m gitkb import knowledge/summaries.json   # 產生筆記並建索引
python -m gitkb log            # 已索引的 commit
python -m gitkb show 0e83df7   # 用 git sha（可縮寫）或筆記 sha256 看筆記
python -m gitkb search visa    # 全文搜尋摘要
python -m gitkb history main.py   # 某個檔案的所有變更
python -m gitkb rebuild-index  # 刪掉 index.db 後從 md 重建
```

需要 Python 3.10+。`build --dry-run` 會先寫出佔位筆記（檔名與正式筆記相同），之後 `import` 會原地覆蓋。
---

## AI job agent (`python -m jobagent`)

An agentic pipeline for the newest **AI jobs in Germany, the Netherlands and Dublin**. It gathers postings from LinkedIn and from the public job boards of 51 AI companies, dedupes them across sources, and scores each job against your profile with OpenAI. A research agent then finds companies the watchlist misses, and the run writes a ranked Markdown and Excel report. Every step is traced in **Langfuse**.

### Architecture

```
 python -m jobagent run
 │
 ├─ 1. collect (deterministic, parallel)
 │     ├─ LinkedIn guest search: {Germany, Netherlands, Dublin} x 8 queries (mostly AI Engineer variants),
 │     │  posted within --since (default 24h)
 │     └─ Watchlist job boards: Greenhouse / Ashby / Lever APIs for 51 AI companies (jobagent/companies.py),
 │        roles posted within the last 7 days
 ├─ 2. normalize + dedupe → SQLite (data/jobagent.db)
 │     region classifier, AI-title filter, cross-source fuzzy dedupe (job board beats LinkedIn), NEW flag
 ├─ 3. score (OpenAI structured outputs, parallel)
 │     JD + profile.md → fit_score, apply_priority, language/visa blockers, gaps, pitch
 ├─ 4. research agent (OpenAI Responses API tool loop, max 12 turns)
 │     tools: list_jobs, get_job_detail, search_linkedin, check_company_board,
 │            add_company_candidate, web_search (OpenAI built-in)
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
| `python -m jobagent run` | Full run: collect, score, research, report. Options: `--since 24h\|7d` (default `24h`), `--regions DE,NL,IE`, `--no-llm`, `--no-agent`, `--max-score N` |
| `python -m jobagent report` | Re-render the latest report from the database |
| `python -m jobagent search "RAG engineer" --region NL` | One ad-hoc LinkedIn search |
| `python -m jobagent companies verify` | Check every watchlist job board and count relevant regional roles |
| `python -m jobagent companies candidates` | Companies the agent proposed, for you to add to `jobagent/companies.py` |
| `python -m jobagent feedback <job_key> --label applied` | Record your verdict; it is sent to Langfuse as a `human_label` score on that job's trace |
| `python -m jobagent doctor` | Check keys, model access, Langfuse and LinkedIn |

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

Each run is one trace named `jobagent.run`. It uses session `jobagent-<date>`, tags `jobagent` plus the regions, and the prompt version as `version`. Inside it:

- `collect.linkedin <region> / <query>` and `collect.<ats> <slug>` spans. Failed sources are marked `WARNING` or `ERROR` with the reason, such as a LinkedIn block.
- `store` with collected, unique, and new counts.
- `score <job_key>` spans. Each holds the OpenAI generation (tokens, cost, latency) and the `fit_score` and `apply_priority` scores.
- `agent` with one generation per turn and a `tool.<name>` span per tool call, including `tool.web_search`.
- `report` with the files written.

Use `feedback` to label jobs you applied to or rejected. In Langfuse you can then compare the model's `fit_score` against your `human_label` and tune `profile.md` or the scorer prompt. Bump `PROMPT_VERSION` in `jobagent/llm/prompts.py` when you edit a prompt, so runs stay comparable.

### Tests

```bash
.venv/bin/python -m pytest tests/        # offline: sources, store, report, agent loop, Langfuse span tree
.venv/bin/python tests/test_pusher.py    # the Discord bot's scenario script
```
