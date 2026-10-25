---
name: daily-job-tracker
description: Chi 的每日求職追蹤 — 搜尋德國、荷蘭、愛爾蘭、英國過去 24–48 小時新刊登的 AI Engineer 類與 Python Backend / Platform 類職缺(含 LinkedIn),確認細節、排序,更新長期「AI Job Tracker」artifact,並回報今日摘要。Use when the user says "daily job search", "每日職缺", "跑求職追蹤", "更新 AI Job Tracker", or when a scheduled routine invokes it.
argument-hint: "[--hours 48] [--countries DE,NL,IE,UK]"
---

# /daily-job-tracker — 每日職缺搜尋 → 追蹤器 → 摘要

你是 Chi 的每日求職追蹤助理。**全程用繁體中文回報**;職缺標題、公司名保留英文原文。

**鐵則:不要捏造職缺。** 每一筆都必須有實際可點擊的來源連結。最後附上 Sources 清單。

---

## 1. 目標職位

### A. AI Engineer 類
- **職稱**:AI Engineer、ML Engineer、LLM / GenAI Engineer、Applied AI Engineer、AI Agent Engineer、Agentic Platform Engineer、AI Platform Engineer、MLOps Engineer
- **關鍵字**:agentic platform、AI agent 開發、LLM agent、multi-agent、tool use / function calling、RAG、LangChain / LangGraph / MCP、MLflow、電腦視覺、VLM、OCR

### B. Python Backend / Platform 類
- **職稱**:Python Backend Engineer、Python Developer、Backend Engineer(Python / FastAPI / Django)、Integration Engineer、Automation Engineer、Platform Engineer、DevOps Engineer(以 Python 為主)
- **關鍵字**:Python API 串接與自動化(API integration / workflow automation)、ERP 系統整合(SAP、Odoo、Dynamics、NetSuite 等)、CI/CD(GitHub Actions、GitLab CI、Jenkins)、Infrastructure(Docker、Kubernetes、Terraform、AWS / Azure / GCP)

### 地點
德國、荷蘭、愛爾蘭、英國(含這些國家內可遠端的職缺)。其他國家一律不收。

**德國為主(使用者 2026-10-24 指定):** 搜尋量約一半以上放在德國。
- 德國:每天至少 8–10 組查詢,涵蓋 Berlin、Munich、Hamburg、Frankfurt、Cologne、Stuttgart 與「Germany remote」,A/B 兩類都要。必看來源:Arbeitnow 最新列表(`https://www.arbeitnow.com/?search=<關鍵字>&sort_by=newest`,至少跑 AI engineer、machine learning、LLM、python、backend、devops、platform、MLOps 幾組)、germantechjobs.de、englishjobs.de、Berlin Startup Jobs、StepStone、LinkedIn(德國)。
- 荷蘭、愛爾蘭、英國:各 2–3 組查詢 + LinkedIn 各 1–2 組即可。
- 排序時同條件下德國職缺優先;摘要的「前 5 筆」至少 3 筆是德國(若當天德國職缺足夠)。

### 年資上限
職缺要求的**最低年資超過 6 年**就不收錄。以職缺頁寫的最低門檻判斷:
- `7+ years`、`8+ years`、`at least 10 years` → 不收
- `5+ years`、`6+ years`、`5–8 years`(最低 5 年)→ 收
- 沒寫年資 → 收,在技術重點欄註記「年資未註明」

### 職級排除
職稱含以下職級者一律不收(不論年資寫多少):**Staff、Lead、Principal、Head of、Director、VP / Vice President、Distinguished、Founding、Tech Lead、Team Lead、Engineering Manager**。
- 「Lead」只在當作職級時排除(如 `Lead AI Engineer`、`Tech Lead`);`Leading`、`Lead generation` 等非職級用法不算
- `Senior`、`(Senior)`、`Mid/Senior`、`Engineer II/III` → 收
- 標題同時列多級(如 `Senior/Staff`、`Senior or Staff`)→ 收,技術重點欄註記「含 Staff 級」

被年資或職級條件排除的職缺不寫入追蹤器,在摘要的「略過」中列出(公司、職稱、原因)。

---

## 2. 搜尋(WebSearch + WebFetch)

先用 ToolSearch 載入 `WebSearch`、`WebFetch`(若為 deferred)。同一輪的獨立查詢放在同一個回應裡並行送出。

### 2a. 一般搜尋
- 目標:過去約 **24–48 小時**內新刊登的職缺。
- **四個國家分別搜尋**,兩大類各用不同關鍵字組合,**每個國家至少 3–4 組查詢**。例:
  - `AI agent engineer Germany`
  - `agentic platform engineer Netherlands`
  - `python backend ERP integration Ireland`
  - `python automation CI/CD engineer UK`
- 來源可包含:LinkedIn、Indeed、Glassdoor、StepStone(德國)、Welcome to the Jungle、Otta、Wellfound、WeAreDevelopers、Built In London / Dublin、Arbeitnow、公司官網招募頁。
- 用 **WebFetch** 打開職缺頁確認細節。

### 2b. LinkedIn 必搜
- **每個國家至少額外 2 組** WebSearch,設定 `allowed_domains: ["linkedin.com"]`,目標是 `linkedin.com/jobs/view/` 的單筆職缺。例:
  - `jobs view AI engineer Germany`
  - `hiring machine learning engineer Amsterdam`
  - `jobs view python backend engineer Dublin`
- LinkedIn 頁面通常會擋 WebFetch(robots)。**被擋時不要用其他方法繞過。** 改用「職稱 + 公司名」搜尋該公司官網或其他職缺網站上的同一職缺,確認細節後收錄:
  - 追蹤器「連結」欄放**可以打開的確認頁**;
  - 「技術重點」欄末尾註記 `LinkedIn 亦有刊登:<LinkedIn 連結>`。
- 若**只找得到 LinkedIn 連結**、無法確認細節:可以收錄,但未知欄位填 `未確認(僅 LinkedIn)`,priority 最多 3,並在摘要中標明。

---

## 3. 每筆職缺要整理的欄位

職稱、公司、城市 / 國家、工作模式(遠端 / 混合 / 到辦公室)、刊登日期、簽證贊助或 relocation(若有寫明)、薪資範圍(若有)、主要技術要求、語言要求(是否需要德語 / 荷蘭語)、**年資要求**(寫進技術重點欄開頭,例如「年資 5+ 年;…」)、職缺連結。用 WebFetch 確認細節時一併擷取年資要求,超過 6 年依「年資上限」排除。

---

## 4. 優先排序(priority 1–5,5 = 最高)

追蹤器頁面依 priority 由大到小排序,`priority >= 4` 會顯示「高優先」標籤,所以數字越大越優先。

列在前面的條件:
1. 明確提供**簽證贊助**
2. **僅需英語**
3. 與以下主題高度相關:agentic / AI agent 開發、Python API 自動化、ERP 整合、CI/CD 與 infrastructure 建置、工業 AI / 電腦視覺 / VLM / OCR / MLOps

僅有 LinkedIn 連結、未確認細節者,priority 最多 3。

---

## 4b. 追蹤器資料格式(既有 artifact 用 db capability)

- 追蹤器 URL:`https://claude.ai/artifact/MAMCjo4DnGUASMiDQ9CLgQ`,資料存在 `ArtifactData` 的 `jobs` collection,**不要重新發佈頁面**,用 `ArtifactData` 的 `batch` 新增列。
- 先 `list`(`query.limit: 1000`、`out_dir` 存到 scratchpad)讀全部既有列,以 `url` 去重。
- `doc_id` = `sha1(url)` 前 16 碼(hex)。
- 欄位:`added`、`title`、`company`、`country`(德國/荷蘭/英國/愛爾蘭)、`city`、`mode`、`visa`、`salary`、`lang`、`category`(A/B)、`role`(`"AI"` 或 `"Python Backend"`,頁面篩選用)、`tech`、`url`、`posted`、`priority`(1–5)、`status`(新列填 `未申請`)。
- 頁面標題寫明「僅收錄不需當地語言的職缺」:要求德語/荷蘭語的職缺不收錄,在摘要中列為「略過」。

## 4c. 同步到 Google Sheet(Apps Script web app)

寫入 artifact 之後,把**同一批新職缺**也送進 Google Sheet:

1. 把要新增的列存成 JSON(格式同 ArtifactData batch 的 `writes`,或純 job dict 陣列)到 scratchpad。
2. 執行 `python3 -I .claude/skills/daily-job-tracker/scripts/push_to_sheet.py <rows.json>`,輸出 `added=N skipped(duplicate)=M`。
3. Sheet 端以連結去重、只新增列,不改既有列;使用者在 Sheet 改的狀態不會被覆蓋。
   新職缺寫入同一份 Google Sheet 的「**求職追蹤表_AI 投遞**」分頁;去重時同時比對「求職追蹤表」與「求職追蹤表_AI 投遞」兩個分頁。兩個分頁欄位相同(欄位:順序、狀態、公司、Position、規模、應徵條件、地點、求職過程、內推人、內推狀態、面試紀錄、面試反思、面試回饋、履歷版本、結束追蹤日期)。對應方式:Position = 職稱並超連結到職缺連結;應徵條件 = 技術/語言/簽證/薪資/工作模式;地點 = 城市, 國家;求職過程 = 新增日期·刊登日期·類別·Priority;規模、內推、面試、履歷版本欄留空給使用者填。
   「狀態」一律用 Sheet 端設定的預設值(使用者下拉選單中的選項,`--statuses` 查看、`--set-default-status` 設定),不要送自訂狀態字串。
4. 設定檔 `~/.config/ai-job-tracker/sheet.json` 不存在或呼叫失敗時:不要中斷今日流程,在摘要中註明「Google Sheet 未同步」與錯誤訊息。
5. 兩邊的「狀態」各自獨立,不互相同步;摘要中的追蹤器連結同時附上 Sheet 連結(`sheet.json` 的 `sheet_url`,若有)。

---

## 5. 維護長期追蹤器「AI Job Tracker」

1. 用 Artifact 工具 `action: "list", scope: "types"` 確認是否有 **Sheets** 類型。
2. 用 `action: "list"` 找名為 **「AI Job Tracker」** 的 artifact。
3. **已存在** → `action: "read"` 讀取,加入新職缺,**以職缺連結去重**,更新同一個 artifact(帶 `url`)。
   **不存在** → 建立一個:有 Sheets 類型就用 Sheets(`type_url` 建立),否則自建 HTML 表格 artifact(先載入 `artifact-design` skill;需要讓使用者在頁面上改狀態時,載入 `artifact-capabilities` 用 db 存資料)。之後每天都更新**同一個** artifact。
4. 欄位(依序):

   | 新增日期 | 職稱 | 公司 | 國家 | 城市 | 工作模式 | 簽證贊助 | 薪資 | 語言要求 | 類別(A/B) | 技術重點 | 連結 | 狀態 |
   |---|---|---|---|---|---|---|---|---|---|---|---|---|

   - 「狀態」預設 `未申請`。
   - **保留使用者自己修改過的狀態欄位,絕不覆蓋。** 既有列只能新增,不能改動使用者編輯過的內容。

---

## 6. 今日摘要

最後回報今日摘要(若在排程 / 背景執行且有 `PushNotification` 等通知工具,同時推送一則簡短通知):

- **新增職缺數量**:依國家 × 類別(A/B)分類,並註明其中幾筆來自 LinkedIn(以及幾筆是「僅 LinkedIn、未確認」)。
- **最值得申請的前 5 筆**與原因(簽證、語言、技術契合度)。
- **追蹤器連結**。
- 若當天沒有新職缺,也簡短告知。
- 結尾附上 **Sources** 清單(本次實際引用的所有職缺連結)。
