# 每日職缺流程(daily pipeline)

每天 04:00(台北時間)自動執行。電腦在睡眠時會被喚醒,跑完才讓它再睡:

1. **搜尋**:headless `claude -p "/daily-job-tracker --hours 48 --pipeline"` 找新職缺。
2. **評分**:Codex 依你的真實經驗評 1–10 分,並分類成 AI agent / ML learning / automation。
3. **寫入**:新職缺寫進 Sheet「每日職缺」,同時鏡像到 Notion「每日職缺」。
4. **興趣職缺**:你貼在 Sheet「興趣職缺」的連結會補上 JD、評分與公司,並鏡像到 Notion「興趣職缺」。
5. **CV 與 Cover letter**:「決定=是」的職缺和興趣職缺會產生 CV 與 Cover letter,各自經過 Codex review。
6. **Drive 連結**:兩份 PDF 上傳到 Google Drive「CV for referrals/<公司>/」,權限是「知道連結的人可檢視」。
7. **內推**:用公開網路搜尋找 2–4 位內推候選人,並依你的範本產生兩種訊息。
8. **紀錄**:建立 Notion「投遞紀錄」列(附 PDF、Drive 連結、內推訊息),再寫進 Sheet「投遞表單」。
9. **同步**:Sheet 上的「決定 / 處理狀態」有變動時,更新 Notion 鏡像。
10. **今日摘要**:在 Notion「GERMAN PLAN」下建立當天的子頁。

流程**不會投遞,也不會送出內推訊息**。投遞用你自己的工具;表單上的問題在 dashboard 按「表單問答」貼上,Claude 產生可複製的回答(不會填表或送出)。內推訊息由你自己複製、貼上、送出,再按「標記已送出」。

| 檔案 | 用途 |
|---|---|
| `.claude/skills/daily-job-tracker/scripts/sheets_api.py` | Sheet 讀寫(Google Sheets API):所有分頁與 actions |
| `.claude/skills/daily-job-tracker/scripts/google_auth.py` | 共用 Google 登入(Drive + Sheets),`--auth` |
| `.claude/skills/daily-job-tracker/scripts/apps_script.gs` | 舊 web app,只留 `onEdit` 與一次性遷移用,不需重新部署 |
| `.claude/skills/daily-job-tracker/scripts/push_to_sheet.py` | Sheet 用戶端,`--setup-pipeline` 等子命令(`--help`) |
| `.claude/skills/daily-job-tracker/scripts/score_jobs.py` | 評分 + 分類,快取 `data/pipeline/scores.json` |
| `.claude/skills/daily-job-tracker/scripts/daily_pipeline.py` | 每日流程本體 |
| `.claude/skills/daily-job-tracker/scripts/notion_sync.py` | Sheet → Notion 鏡像(每日職缺 / 興趣職缺) |
| `.claude/skills/daily-job-tracker/scripts/drive_upload.py` | Google Drive 上傳(`--drive-backfill`) |
| `.claude/skills/daily-job-tracker/scripts/referral.py` | 內推候選解析 + 訊息範本 |
| `tools/pipeline/windows/` | Task Scheduler XML、`register_task.ps1`、`run_pipeline.ps1`(保持不睡) |
| `tools/pipeline/run_daily.sh` | 排程在 WSL 端的入口 |
| `tools/pipeline/fake_sheets/` | 測試用假 Google Sheets REST API(in-memory),`POST /` 用真正的 `sheets_api.py` 跑 action |
| `tools/pipeline/tests/` | `python3 -m unittest discover -s tools/pipeline/tests` |

## 1. Google 登入 + 建立分頁(只做一次)

流程用 **Google Sheets API** 直接寫 Sheet,和 Drive 共用**一個** OAuth 登入。不再經過 Apps Script web app。

1. **Cloud 專案**:到 Google Cloud console 建立或選一個專案。在 **APIs & Services → Library** 啟用 **Google Drive API** 和 **Google Sheets API**。
2. **OAuth consent screen**:User type 選 **External**。把自己加進 test users,然後把 Publishing status 改成 **In production**。個人使用不需要送審,登入時會看到「Google 尚未驗證這個應用程式」,按「進階 → 前往」即可。
   **一定要 In production**:停在 Testing 狀態時,Google 會在 7 天後讓 refresh token 失效,04:00 的排程就會停擺。
3. **Desktop client**:到 **Credentials → Create credentials → OAuth client ID**,類型選 **Desktop app**。下載 JSON,存成 `~/.config/ai-job-tracker/google_client_secret.json`(舊檔名 `drive_client_secret.json` 也接受)。
4. **安裝套件**:`sudo apt install python3-google-auth-oauthlib`(會一起裝 google-auth 與 requests)。
5. **登入**:執行 `python3 .claude/skills/daily-job-tracker/scripts/google_auth.py --auth`,把印出的網址貼到 Windows Chrome 並允許。WSL 會轉送 localhost,登入完會自動回到程式。權限是 `drive.file` 與 `spreadsheets`,token 存在 `~/.config/ai-job-tracker/google_token.json`,之後排程會自動更新它。
6. **建立分頁、補資料**:
   ```bash
   cd /mnt/e/PythonProject/LinkedinJobSearcher
   python3 .claude/skills/daily-job-tracker/scripts/push_to_sheet.py --live --setup-pipeline      # 建立分頁(可重跑)
   python3 .claude/skills/daily-job-tracker/scripts/push_to_sheet.py --live --pipeline-backfill   # 「求職追蹤表_AI 投遞」的「準備中」列 → 每日職缺(不刪原列,可重跑)
   python3 .claude/skills/daily-job-tracker/scripts/daily_pipeline.py --drive-backfill            # 既有 PDF → Drive
   ```
   第一次跑流程時,補進來的列也會鏡像到 Notion「每日職缺」,每 20 筆一批。

**授權失效時**:token 過期或被撤銷時,當天流程會停止。Notion 今日摘要和 `scheduler.log` 會寫明「Google Sheet 無法使用」以及重新登入的指令。

**Apps Script**:`apps_script.gs` 留在 repo 裡,**不需要重新部署**。已部署的專案繼續提供 `onEdit`,也就是狀態改成拒絕或放棄時自動填「結束追蹤日期」。一次性的 `--migrate` / `--delete` 仍走 web app,想讓舊的新增路徑也走 web app,設 `PUSH_TRANSPORT=apps-script`。

**分頁**
- **每日職缺**:決定 / 處理狀態有下拉選單。
- **興趣職缺**:你只要貼「連結」,需要時加「備註」。
- **投遞表單**:欄位和「求職追蹤表」完全相同。標題列直接複製,下拉選單也一樣。後面另外接上管線欄位:日期、連結、類型、來源、城市、國家、工作型態、ATS、應用資料夾、Notion、CV 連結、Cover letter 連結、內推訊息、內推資料。
- **依日期**:每日職缺_依日期、興趣職缺_依日期。在 B1 選日期,下面就會列出那天的列。

舊分頁一律不動。

**投遞表單各欄**
- **地點**:「City, Country / Mode」。
- **應徵條件**:第一行是「AI agent · 評分 8/10」,下面每行一個主要要求(• …)。
- **求職過程**:有日期的紀錄行,例如 `- 11/02 CV + cover letter 產生 (ATS 75.8)`。dashboard 送出後會加 `- 11/03 Sent (CV dashboard)`,內推送出後會加 `- 11/04 Sent referral request to <name>`。
- **履歷版本**:連到 Notion 投遞紀錄列(PDF 在那裡),顯示文字像 `v3 cards · ATS 75.8 + CL`。
- **內推人**:每行一位,第一位是推薦人選。
- **內推狀態**:按「標記已送出」後是 ⚠️等待回覆。之後由你改成 ✅同意 或 ❌拒絕。

## 2. Notion(都在 GERMAN PLAN 底下)

| 資料庫 | 內容 |
|---|---|
| 每日職缺(`collection://9db94811-3eaa-4b51-a4cd-ee97d7dd5b45`) | Sheet「每日職缺」的鏡像,另有「依日期」分組檢視 |
| 興趣職缺(`collection://3c036c71-f05f-4c92-a3b5-111686a146e9`) | Sheet「興趣職缺」的鏡像,另有「依日期」分組檢視 |
| 投遞紀錄(既有) | 只放已產生 CV 的職缺 |
| 今日摘要 | GERMAN PLAN 的子頁 |

- **主資料是 Sheet**:決定請在 Sheet 或 dashboard 改,在 Notion 改的內容會在下次同步時被覆蓋。
- **同步範圍**:只同步新列,以及欄位有變動的列。比對紀錄在 `data/pipeline/notion_state.json`。
- **投遞紀錄列的內容**:附 CV / Cover letter PDF。屬性有 CV Drive、CL Drive、內推候選,頁面裡有「## 內推訊息」,每位候選人各有完整訊息和邀請附言。
- **新屬性**:第一次建列時,如果還沒有 CV Drive、CL Drive、內推候選這三個屬性,會自動加上。

## 3. Google Drive(F1)

登入和 Sheets 共用(見第 1 節)。

- **補傳既有的 PDF**:6 份 CV,以及各公司最新的 Cover letter(`*_v2` 與 Scout24)。先用 `daily_pipeline.py --drive-backfill --dry-run` 看清單。連結會存在 `data/pipeline/drive_links.json`。
- **權限範圍**:只用 `drive.file`,程式只看得到自己建立的檔案。
- **重新上傳**:同一家公司的同名檔案會覆蓋內容,檔案 id 不變,所以連結不變。
- **未授權時**:流程照跑,CV 連結改填本機路徑,摘要的「失敗」裡會註明。

## 4. 內推(F2 / F3)

- **只用公開搜尋**:不登入 LinkedIn、不操作你的帳號、不抓 linkedin.com 頁面。內推步驟的 headless Claude 只開 `WebSearch`。
- **查詢範例**:`site:linkedin.com/in "<公司>" (AI engineer OR machine learning engineer OR engineering manager)`,以及 `site:linkedin.com/in "<公司>" Taiwan`。
- **排序**:同團隊工程師,然後招募 / 工程主管,最後台灣同事,最多 4 位。
- **不會捏造人選**:每位候選人的網址都必須出現在搜尋結果裡,由 `referral.py` 檢查。找不到就寫「公開搜尋未找到」。
- **兩種訊息**:
  - **完整訊息**:你的範本原文,只填入空格(對方名字、公司、職稱、職缺名稱、Drive CV 連結)。連線後或用 InMail 時使用。
  - **邀請附言**:280 字元以內,寫明職缺並請求內推。
- **送出**:由你手動。在 dashboard「投遞表單」按「複製完整訊息 / 複製邀請附言」,送出後按「標記已送出」。

## 5. 註冊排程(只做一次)

在 Windows 的一般 PowerShell 執行,不需要系統管理員權限。請用 E: 路徑執行,讓排程用 Windows 路徑:

```powershell
powershell -ExecutionPolicy Bypass -File E:\PythonProject\LinkedinJobSearcher\tools\pipeline\windows\register_task.ps1
# 選項:-Distro Ubuntu-24.04  -RunNow(註冊後立刻跑一次)  -Unregister(移除)
```

- **觸發**:每天 04:00 +08:00。設了「喚醒電腦」;錯過時(關機,或喚醒失敗)會在下次開機或登入後盡快補跑。
- **保持不睡**:排程執行 `run_pipeline.ps1`。它先呼叫 `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` 防止系統閒置睡眠,但不讓螢幕保持亮著。接著執行 WSL 流程並等它結束,最後在 `finally` 解除。5 份 CV 大約要跑 1 小時。
- **只在你登入時執行**:WSL 的 distro 屬於使用者的登入工作階段。改成「不論使用者是否登入都執行」時,wsl.exe 會失敗或找不到你的 distro。所以請保持登入,鎖定畫面沒問題,不要登出。
- **輸出**:`data/pipeline/logs/scheduler.log`(每次的開始 / 結束時間),以及 `data/pipeline/logs/<日期>.log`。
- **補跑範圍**:隔幾天沒跑,下次的搜尋視窗會自動放寬,最多 7 天。待處理的 CV 也會一起處理。

### 夜間執行檢查清單

1. **允許喚醒計時器**:控制台 → 電源選項 → 變更計劃設定 → 變更進階電源設定 → 睡眠 → **允許喚醒計時器 = 啟用**。筆電的「使用電池」也要設啟用,或整晚插著電源。檢查指令:`powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE`,目前值要是 `0x00000001`。
2. **保持登入**:晚上用鎖定(Win + L),不要登出。
3. **睡眠,不要休眠**:有些機器無法從休眠(hibernate)或關機狀態喚醒。請用睡眠,必要時關掉自動休眠。
4. **Windows Update 使用時段**:設定 → Windows Update → 進階選項 → 使用時段,手動設成涵蓋 03:30–06:00(例如 03:00–15:00),避免夜裡自動重開機、登出。
5. **確認是否有跑**:早上看 `scheduler.log`,或看 Notion 的今日摘要頁。`powercfg /waketimers` 可以看到排程的喚醒計時器。

## 6. 手動執行

```bash
cd /mnt/e/PythonProject/LinkedinJobSearcher
python3 .claude/skills/daily-job-tracker/scripts/daily_pipeline.py --live          # 全部步驟(正式 Sheet)
python3 .claude/skills/daily-job-tracker/scripts/daily_pipeline.py --live --only cv --max-cv 1
python3 .claude/skills/daily-job-tracker/scripts/daily_pipeline.py --live --only notion   # 只同步 Sheet → Notion
python3 .claude/skills/daily-job-tracker/scripts/daily_pipeline.py --live --dry-run --fixture-jobs jobs.json   # 不呼叫 Claude/Codex,正式 Sheet 只讀
python3 -m unittest discover -s tools/pipeline/tests                                # 測試
```

- **步驟**:search、interest、cv、notion、summary。
- **重跑同一天**:不會重複。每天的 ledger 在 `data/pipeline/runs/<日期>.json`,每筆職缺分「CV → Drive → 內推 → Notion 投遞紀錄 → 投遞表單」記錄,重跑會從停下的地方接續。
- **`--force`**:重新搜尋,並重建摘要頁。
- **`--live` 才連正式 Sheet**:`daily_pipeline.py` 與 `push_to_sheet.py` 的管線子命令沒加 `--live` 會拒絕執行;排程的 `run_daily.sh` 已帶 `--live`。測試一律用 `AI_JOB_TRACKER_SHEET_CONFIG` 指向假伺服器。`--dry-run --live` 只讀正式 Sheet、不寫入,也不呼叫 Notion。dashboard 的 Pipeline 頁要用 `npm run dev:live`(或 `start:live`)啟動才連正式 Sheet。
- **失敗**:失敗的職缺隔天重試,滿 2 次會把「處理狀態」設為「失敗」。想再試,把它改回「待決定」(興趣職缺改回「待產 CV」)。Notion 投遞紀錄失敗時,CV 不會重產,下次只重試 Notion 這一步。
- **低分興趣職缺**:你貼的連結即使評分 ≤ 4 也照樣產 CV,摘要會在「⚠️ 低分興趣職缺」列出分數與理由。
- **需要你核准的步驟**:headless 時不會問問題。library 新 lead、`sot-leads` 寫入、新 CONF 都不會做,改列在摘要的「待你核准」。

## 7. 費用(估計)

| 項目 | 約 |
|---|---|
| 評分(Codex,每批 5 筆) | $0.05 / 批 |
| 每日搜尋(Claude WebSearch/WebFetch) | $1–3(未實測) |
| 一份 CV + Cover letter(含兩次 Codex review、multi-writer) | $5–8 |
| 內推搜尋(每個職缺) | $0.2–0.5 |
| Notion 投遞紀錄列 + PDF / 鏡像同步(每 20 筆一批)/ 今日摘要 | 各約 $0.1–0.3 |

上限 5 份時,一天約 $30–45。用 `PIPELINE_MAX_CV` 調整。每天的實際花費記在 ledger 的 `cost_usd`,也寫在摘要裡。

## 8. 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `PIPELINE_MAX_CV` | 5 | 每天最多產生幾份 CV |
| `PIPELINE_MAX_ATTEMPTS` | 2 | 失敗幾次後標「失敗」 |
| `PIPELINE_HOURS` | 48 | 搜尋視窗(小時),漏跑時自動放寬 |
| `PIPELINE_CV_TIMEOUT` | 5400 | 一份 CV + 信的時限(秒) |
| `PIPELINE_DATA` | `data/pipeline` | ledger、快取、Notion 同步紀錄、logs |
| `AI_JOB_TRACKER_SHEET_CONFIG` | `~/.config/ai-job-tracker/sheet.json` | Sheet 設定檔 |
| `AI_JOB_TRACKER_CONF_DIR` | `~/.config/ai-job-tracker` | Google OAuth client / token / Drive 檔案對照 |
| `CLAUDE_BIN` / `CODEX_BIN` | `claude` / `codex` | CLI(`~/.npm-global/bin` 與 `~/.local/bin` 會排在 PATH 最前面) |
| `SCORE_MODEL` | Codex 預設 | 評分用的模型(`codex -m`) |

Headless Claude 的權限:

- **搜尋**:只開 WebSearch、WebFetch、Read。
- **內推**:只開 WebSearch。
- **CV**:開 tailor-cv / cover-letter 的腳本、codex plugin、Agent,以及 Notion 讀取。
- **Notion 列**:開 Notion 上傳 / 建頁 / 加屬性與 `curl`,用來上傳 PDF。
- **鏡像同步與摘要**:只開 Notion 建頁 / 更新。

你自己的 Claude Code 設定仍然會套用。

## 9. 晚上保持開機(Evening Awake,2026-11-01)

Windows 排程「Evening Awake」:每天 18:00 喚醒睡眠中的電腦,`windows/evening_awake.ps1` 用
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` 讓系統到 22:00 前不閒置睡眠(螢幕照常關閉),
22:00 後恢復電源計劃。用途:晚上從手機透過 Tailscale 用 CV dashboard / Live browser。
- 只能從睡眠喚醒,不能從關機開機;需保持登入(鎖定即可)。錯過 18:00 時開機後補跑,超過 22:00 就直接結束。
- 改時間:`Set-ScheduledTask -TaskName "Evening Awake" -Trigger (New-ScheduledTaskTrigger -Daily -At "19:00")`;
  改結束時間:把動作參數 `-Until 22:00` 換掉(`Get-ScheduledTask "Evening Awake"` 查看)。
- 移除:`Unregister-ScheduledTask -TaskName "Evening Awake" -Confirm:$false`
