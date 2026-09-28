# LinkedinJobSearcher

LinkedIn 職缺搜尋工具，有三種用法：

- **CLI**（`python main.py`）—— 互動式終端機介面，問答式輸入條件後顯示表格。
- **Discord bot**（`python -m bot`）—— 常駐服務，用 slash 指令登記追蹤條件，每天固定時間自動把**沒推播過的新職缺**貼到指定頻道。
- **MCP server**（`python -m mcp_server`）—— 讓 Claude Code 等 agent 直接呼叫職缺搜尋、簽證判斷與 gitkb，每次呼叫都可追蹤到 Langfuse。見[下方](#mcp-server)。

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
python tests/test_mcp.py     # 需要 Python 3.10+ 與 mcp 套件
```

離線執行，用假的 Discord 物件驗證推播邏輯：冷啟動基準線、去重、單次上限與溢出處理、送出失敗時不可標記為已看過、失效通知與恢復、熔斷器、排程的 exactly-once。不需要 token，也不會連上 LinkedIn 或 Discord。

`test_visa.py` 檢查地區預設展開與簽證規則（含 LLM 只在「不明」時才被呼叫）；`test_gitkb.py` 在暫存 git repo 裡跑完整的知識庫流程；`test_mcp.py` 用假的 LinkedIn 透過 MCP 協定呼叫每個工具，並把 Langfuse 的 span 導到記憶體裡，檢查 trace 結構、巢狀關係與遮罩。都不需要網路。

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

Claude Code 也可以透過 MCP server 做同一件事，不需要 pending.json／summaries.json 這兩個暫存檔：在 Claude Code 裡選 `jobseekers` server 的 `gitkb_update` prompt，或直接請它「用 gitkb_pending 和 gitkb_import_summaries 更新知識庫」。

## MCP server

`mcp_server/` 把專案裡的核心功能包成 [MCP](https://modelcontextprotocol.io/) 工具，讓 Claude Code 之類的 agent 直接呼叫。CLI、Discord bot 和 MCP server 共用同一份 `linkedin_scraper.py`、`visa.py` 與 `gitkb/`。

| 工具 | 作用 |
|---|---|
| `search_jobs` | 搜尋 LinkedIn 職缺（最新優先，可用多地點與地區預設），回傳 `outcome` 讓 agent 分辨「沒有職缺」和「被封鎖／爬蟲壞了」 |
| `get_job_detail` | 讀單一職缺的完整描述、條件與規則判斷的簽證結果 |
| `check_visa` | 一次檢查最多 15 筆職缺是否提供簽證支持；規則判不出來的會附上描述，**交給呼叫端的模型自己判斷**，所以 server 不需要任何 LLM 金鑰 |
| `gitkb_search` / `gitkb_show` / `gitkb_log` / `gitkb_history` | 查詢 git 歷史知識庫：改程式前先查「為什麼當初這樣寫」 |
| `gitkb_pending` / `gitkb_import_summaries` | 取代 `/gitkb` 的暫存檔流程，直接以工具參數交換 diff 與摘要 |
| `list_subscriptions` / `bot_status` | Discord bot 的訂閱與上次推播狀態（唯讀，以 `mode=ro` 開啟資料庫，不會與 bot 搶寫入） |

另外提供 resource `jobs://regions`（地區預設清單）和 prompt `gitkb_update`（更新知識庫的步驟）。

### 使用

repo 根目錄的 `.mcp.json` 已登記這個 server，用 [uv](https://docs.astral.sh/uv/) 自動建立 Python 3.13 環境並安裝 `requirements.txt`，不需要手動建 venv。在 repo 裡開 Claude Code，第一次會詢問是否啟用 `jobseekers` server，同意即可。`/mcp` 可以看連線狀態。

自己檢查安裝與 Langfuse 連線：

```bash
uv run --no-project --python 3.13 --with-requirements requirements.txt python -m mcp_server --check
```

LinkedIn 的限制對 agent 更敏感，因為 agent 呼叫得比人快很多：所有 LinkedIn 工具共用一個節流器（`MCP_LINKEDIN_MIN_GAP`，預設 3 秒），`search_jobs` 最多 50 筆、`check_visa` 最多 15 筆。被封鎖時工具回傳錯誤並明確告訴 agent **不要重試**，因為重試會把短暫限流變成數小時的 IP 封鎖。

### Langfuse 追蹤

`.env` 填了 `LANGFUSE_PUBLIC_KEY` 與 `LANGFUSE_SECRET_KEY` 就會開啟（見 `.env.example`）；沒填則照常運作、不追蹤。

- **一次工具呼叫 = 一個 trace**。根 observation 的 input 是工具參數、output 是工具結果，名稱固定（`search-linkedin-jobs`、`check-visa-sponsorship`、`search-git-history`……，完整清單見 `mcp_server/observability.py` 的 `NAMES`），可以直接拿來建 dashboard 或 evaluator。
- `check_visa` 底下每筆職缺各有一個 `check-job-visa`，裡面再分成 `fetch-job-description`（retriever）與 `classify-visa-rules`，可以看出哪一筆慢、哪一筆判斷依據是什麼。
- 同一個 server 行程（通常就是一個 Claude Code session）的所有 trace 共用一個 session id，tag 為 `mcp` 加上 `jobs` / `gitkb` / `bot`。
- MCP client 若在請求的 `_meta` 帶了 W3C `traceparent`，工具的 trace 會直接接到 client 的 trace 底下。
- 職缺描述裡的 email、電話與 API 金鑰在送出前就會被遮罩（`MCP_LANGFUSE_MASK=0` 可關閉）。
- 每次工具呼叫結束都會立刻 flush。MCP client 關閉 session 時常常直接結束 server 行程，如果不 flush，最後幾次呼叫的 trace 會遺失。
