"""Daily job pipeline: search → score → 每日職缺, 興趣職缺 → score, CV + cover letter for the queue, 今日摘要.

    python3 daily_pipeline.py                      # the scheduled run (Task Scheduler, 04:00 Asia/Taipei)
    python3 daily_pipeline.py --dry-run --fixture-jobs jobs.json   # no Claude/Codex calls, fake scores
    python3 daily_pipeline.py --only search|interest|cv|summary [--force]

Steps
  a) search   headless `claude -p "/daily-job-tracker --hours N --pipeline"` → jobs JSON → dedupe against every
              Sheet tab → score_jobs.py → pipeline_append (每日職缺). Once per day (ledger); a missed day widens N.
  b) interest interest_list (empty 處理狀態) → JD text (plain fetch; fallback: headless claude WebFetch/WebSearch)
              → score → interest_update (company/title/…/處理狀態=待產 CV).
  c) cv       queue = 興趣職缺 待產 CV first, then 每日職缺 決定=是 & 處理狀態=待決定 by score; at most
              PIPELINE_MAX_CV (5) per day. Per job: headless /tailor-cv (apply) + /cover-letter → Notion 投遞紀錄 row
              with both PDFs → submissions_append (投遞表單) → 處理狀態=已產 CV + links. A failure is logged and the
              job retried next day; after PIPELINE_MAX_ATTEMPTS (2) failures 處理狀態=失敗.
  d) summary  dated child page under Notion GERMAN PLAN + the same text on stdout.

Idempotent: data/pipeline/runs/<date>.json records each step and each job's sub-steps (cv → notion → sheet), so
a rerun resumes where it stopped and never duplicates; the Sheet dedupes by URL as well. Never submits an
application. Logs: data/pipeline/logs/.

Env: PIPELINE_MAX_CV=5 · PIPELINE_MAX_ATTEMPTS=2 · PIPELINE_HOURS=48 · PIPELINE_CV_TIMEOUT=5400 (s) ·
PIPELINE_DATA=<repo>/data/pipeline · PIPELINE_MODEL=opus · CLAUDE_BIN=claude · CODEX_BIN=codex · AI_JOB_TRACKER_SHEET_CONFIG.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Task Scheduler's `bash -lc` finds the Windows codex (/mnt/c/nvm4w/…) first; the WSL CLIs must win, also for
# the codex-rescue plugin inside the headless Claude runs.
for _bin in (Path.home() / ".npm-global" / "bin", Path.home() / ".local" / "bin"):
    if _bin.is_dir() and str(_bin) not in os.environ.get("PATH", "").split(":")[:2]:
        os.environ["PATH"] = f"{_bin}:{os.environ.get('PATH', '')}"
import drive_upload  # noqa: E402
import notion_sync  # noqa: E402
import push_to_sheet as sheet  # noqa: E402
import referral  # noqa: E402
import score_jobs  # noqa: E402

REPO = HERE.parents[3]
DATA = Path(os.environ.get("PIPELINE_DATA") or REPO / "data" / "pipeline")
TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Taipei, no DST
GERMAN_PLAN_PAGE = "40b77152-eb3d-8166-9d0a-c7ceb8904012"
APPLICATIONS_DS = "ed4d1c12-ff9d-4e35-86d8-5aa0fab3cd20"
NOTION = "mcp__plugin_Notion_notion__notion"
COUNTRY_EN = {"德國": "Germany", "荷蘭": "Netherlands", "英國": "United Kingdom", "愛爾蘭": "Ireland",
              "germany": "Germany", "netherlands": "Netherlands", "united kingdom": "United Kingdom", "uk": "United Kingdom",
              "ireland": "Ireland"}
PARENT_SESSION_VARS = ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
                       "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                       "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SSE_PORT"]
CODEX_PLUGIN = Path.home() / ".claude" / "plugins" / "cache" / "openai-codex"

SEARCH_TOOLS = ["ToolSearch", "WebSearch", "WebFetch", "Read"]
JD_TOOLS = ["ToolSearch", "WebSearch", "WebFetch"]
CV_TOOLS = [
    "Skill", "Read", "Edit", "Write", "Glob", "Grep", "ToolSearch", "WebFetch", "WebSearch", "Agent",
    "Bash(python3 .claude/skills/*)", "Bash(bash .claude/skills/*)",
    "Bash(python3 tools/cv-dashboard/scripts/shared_lock.py *)",
    f"Bash(node {CODEX_PLUGIN}/*)", f'Bash(node "{CODEX_PLUGIN}/*)',
    "Bash(mkdir -p cv/*)", "Bash(ls *)", "Bash(date *)",
    f"{NOTION}-search", f"{NOTION}-fetch",
]
NOTION_ROW_TOOLS = ["ToolSearch", "Read", f"{NOTION}-create-file-upload", f"{NOTION}-create-pages", f"{NOTION}-update-data-source",
                    f"{NOTION}-update-page", f"{NOTION}-fetch", f"{NOTION}-get-async-task", "Bash(curl *)"]
SUMMARY_TOOLS = ["ToolSearch", f"{NOTION}-create-pages", f"{NOTION}-fetch"]
REFERRAL_TOOLS = ["ToolSearch", "WebSearch"]  # public search results only: no WebFetch, no linkedin.com pages


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---- logging / ledger -------------------------------------------------------

class Log:
    def __init__(self, day):
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        self.path = DATA / "logs" / f"{day}.log"

    def __call__(self, msg):
        line = f"{dt.datetime.now(TZ):%H:%M:%S} {msg}"
        print(line, file=sys.stderr, flush=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class Ledger:
    """Daily progress in runs/ (production) or dry_runs/ (simulation)."""

    def __init__(self, day, dry_run=False):
        directory = DATA / ("dry_runs" if dry_run else "runs")
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{day}.json"
        try:
            self.d = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.d = {"date": day, "steps": {}, "jobs": {}}

    def step(self, name):
        return self.d["steps"].setdefault(name, {})

    def job(self, key):
        return self.d["jobs"].setdefault(key, {})

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def all_ledgers():
    for p in sorted((DATA / "runs").glob("*.json")):
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue


def job_history(key):
    """Every earlier day's record of one job: attempts and finished sub-steps (cv, notion, sheet)."""
    attempts, merged = 0, {}
    for led in all_ledgers():
        j = led.get("jobs", {}).get(key)
        if not j:
            continue
        attempts += j.get("failures", 0)
        for k in ("cv", "drive", "referrals", "notion", "submission"):
            if j.get(k):
                merged[k] = j[k]
    return attempts, merged


def last_search_day():
    days = [led["date"] for led in all_ledgers() if led.get("steps", {}).get("search", {}).get("done")]
    return max(days) if days else None


# ---- Sheet (writes can be disabled for a dry run against the live config) ----

class Sheet:
    def __init__(self, write, log):
        self.write, self.log = write, log

    def read(self, action, **kw):
        return sheet.pipeline(action, **kw) if action != "list" else sheet.request({"action": "list"})

    def update(self, action, **kw):
        if not self.write:
            self.log(f"[dry-run, Sheet 未寫入] {action}: {json.dumps(kw, ensure_ascii=False)[:300]}")
            return {"added": [{"url": r.get("url"), "row": 0} for r in kw.get("rows", [])], "updated": [], "skipped": []}
        return sheet.pipeline(action, **kw)

    def known_keys(self):
        keys = {score_jobs.url_key(u) for u in self.read("list").get("urls", [])}
        for action, field in (("pipeline_list", "rows"), ("interest_list", "rows"), ("submissions_list", "rows")):
            for r in self.read(action, all=True).get(field, []):
                keys.add(score_jobs.url_key(r.get("url")))
        return keys


# ---- headless Claude --------------------------------------------------------

def claude_env():
    env = dict(os.environ)
    for k in PARENT_SESSION_VARS:
        env.pop(k, None)
    return env


def last_json_block(text):
    blocks = re.findall(r"```json\s*(.*?)```", text or "", re.S)
    for b in reversed(blocks):
        try:
            return json.loads(b)
        except ValueError:
            continue
    m = re.search(r"(\{.*\}|\[.*\])\s*$", (text or "").strip(), re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    raise ValueError("no JSON block in the final message")


REPEAT_JSON = ("Repeat the fenced ```json block that your final report was asked to end with, exactly as you "
               "wrote it (fill it from your work if you never wrote it). Do no other work, call no tools.")


def run_claude(prompt, tools, label, log, day, timeout=1800):
    """`claude -p` without a shell, in the repo root; returns (parsed final JSON, cost)."""
    out = DATA / "logs" / f"{day}_{label}.json"
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", prompt, "--output-format", "json",
           "--model", os.environ.get("PIPELINE_MODEL", "opus"),  # never inherit the interactive /model choice
           "--permission-mode", "acceptEdits", f"--allowedTools={','.join(tools)}"]
    log(f"claude -p [{label}] start")
    t0 = time.time()
    p = subprocess.run(cmd, cwd=REPO, env=claude_env(), capture_output=True, text=True, timeout=timeout)
    out.write_text(p.stdout + ("\n--- stderr ---\n" + p.stderr if p.stderr else ""), encoding="utf-8")
    try:
        res = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"claude [{label}] exit {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}")
    cost = res.get("total_cost_usd") or 0
    log(f"claude -p [{label}] exit {p.returncode} in {time.time() - t0:.0f}s, ${cost:.2f}")
    if res.get("is_error") or p.returncode:
        raise RuntimeError(f"claude [{label}] failed: {str(res.get('result'))[:300]}")
    try:
        return last_json_block(res.get("result", "")), cost
    except ValueError:
        # The run can end on a late wake-up / background-task turn ("nothing left to do") after the message
        # that held the JSON. Resume the same session and ask for the block again (read-only). Haiku: re-reading the
        # whole session costs ~$3.6 on Opus (2026-11-03), a fraction of that on Haiku.
        sid = res.get("session_id")
        if not sid:
            raise
        log(f"claude -p [{label}] no JSON block in the final message; asking session {sid} to repeat it")
        r = subprocess.run([os.environ.get("CLAUDE_BIN", "claude"), "-p", REPEAT_JSON, "--resume", sid,
                            "--output-format", "json", "--model", os.environ.get("PIPELINE_REPEAT_MODEL", "haiku"),
                            "--permission-mode", "default", "--allowedTools=Read"],
                           cwd=REPO, env=claude_env(), capture_output=True, text=True, timeout=600)
        try:
            res2 = json.loads(r.stdout)
        except ValueError:
            raise RuntimeError(f"claude [{label}] no JSON block, and the repeat request failed: {(r.stderr or r.stdout).strip()[-300:]}")
        cost += res2.get("total_cost_usd") or 0
        return last_json_block(res2.get("result", "")), cost


# ---- prompts ----------------------------------------------------------------

def search_prompt(hours, known_file):
    return f"""/daily-job-tracker --hours {hours} --pipeline

Pipeline mode (headless daily run): follow the skill's sections 1–3 (targets, search, fields, exclusions) but do NOT
touch the AI Job Tracker artifact or the Google Sheet — the orchestrator scores and writes. URLs already tracked
(one per line, normalised) are in {known_file}: skip those jobs without fetching them.
Never invent a job: every job needs a real URL you opened or found. For each job, put the JD's responsibilities and
requirements text you read (≤ 3000 characters, in the JD's words) in "jd".
End your final message with ONE fenced ```json block:
{{"jobs": [{{"company": "", "title": "", "url": "", "city": "", "country": "德國|荷蘭|英國|愛爾蘭", "mode": "Hybrid|Remote|On-site|未註明",
"posted": "YYYY-MM-DD", "linkedin_url": "", "jd": ""}}], "skipped": [{{"company": "", "title": "", "reason": ""}}],
"sources": ["url", …]}}"""


def jd_prompt(url, note):
    return f"""Find the job description for this posting: {url}
(candidate's note: {note or '—'}). Use WebFetch on the URL. If it is blocked (e.g. LinkedIn) do not work around the
block: search for the same job (title + company) on the company's careers page or another job board and fetch that.
Do not invent anything. End with ONE fenced ```json block:
{{"found": true|false, "company": "", "title": "", "url_used": "", "jd": "<responsibilities + requirements text, ≤ 6000 chars>", "reason": "<why not found>"}}"""


def cv_prompt(job, run_id):
    hint = job.get("note") or "—"
    return f"""Headless daily pipeline run {run_id}. Produce a tailored CV and a cover letter for ONE job. NEVER submit an
application and never open an application form.

Job: {job.get('company', '')} — {job.get('title', '')}
URL: {job['url']}
Location: {job.get('city', '')} {job.get('country', '')} ({job.get('mode', '')})
Candidate's note for this job (an instruction hint; follow it where it stays truthful): {hint}

1. Run the tailor-cv skill in apply mode for this JD (Skill "tailor-cv"; read .claude/skills/tailor-cv/SKILL.md), steps 0–8
   including the Codex review (Agent subagent_type "codex:codex-rescue", review-only) exactly as the skill defines.
2. Then run the cover-letter skill for the same job (Skill "cover-letter"), steps 0–5 including the Codex score, using
   this application's dir as $APP.

Headless rules (nobody can answer questions):
- No AskUserQuestion. A step that needs the candidate's approval (library mode, NEW LEAD, `sot-leads --write-from`,
  new CONF statements, seed edits) is NOT done: list it in "pending_approvals" and continue with the library as it is.
- Facts only from cv/profile/library_seed.json (via library_db.py) and cv/profile/candidate_confirmations.md. Never edit
  either file. Never commit.
- Shared DB writes (`library_db.py record|ingest-ats|set-coverage|set-concept|init`) go through
  `python3 tools/cv-dashboard/scripts/shared_lock.py run --run {run_id} -- python3 .claude/skills/tailor-cv/scripts/library_db.py …`.
- If the JD cannot be read, or the job is excluded by the skill's rules, stop and report status "failed" with the reason.

End your final message with ONE fenced ```json block:
{{"status": "ok"|"failed", "reason": "", "jd_key": "<the --jd key>", "company": "", "title": "",
"app_dir": "cv/applications/<dir>[/v3_cards]", "cv_pdf": "<repo-relative path>", "cover_dir": "cv/cover_letters/<dir>",
"cover_letter_pdf": "<repo-relative path>", "ats": <overall ATS score number or null>, "hr": <n|null>, "hm": <n|null>,
"verdict": "", "letter_verdict": "", "pending_approvals": ["…"]}}"""


def notion_row_prompt(job, cv, today, drive=None, people=None):
    drive, people = drive or {}, people or []
    country = COUNTRY_EN.get(str(job.get("country", "")).strip().lower()) or COUNTRY_EN.get(str(job.get("country", "")).strip())
    pdfs = [p for p in (cv.get("cv_pdf"), cv.get("cover_letter_pdf")) if p]
    props = {"公司": job.get("company") or cv.get("company"), "職位": job.get("title") or cv.get("title"),
             "最遠關卡": "準備中", "城市": job.get("city", ""), "工作型態": job.get("mode") or "未註明",
             "管道": "LinkedIn" if "linkedin.com" in job["url"] else "Job board", "date:投遞日:start": today,
             "ATS": cv.get("ats"), "文件": ["CV"] + (["Cover letter"] if cv.get("cover_letter_pdf") else []),
             "職缺連結": job["url"], "下一步": "檢查 CV 與 Cover letter,確認後自行投遞;內推訊息見頁面",
             "備註": f"每日流程自動產生。應用資料夾:{cv.get('app_dir', '')}",
             "CV Drive": drive.get("cv"), "CL Drive": drive.get("cover_letter"),
             "內推候選": "\n".join(f"{p['name']} ({p['title']}) — {p['url']}" for p in people) or "公開搜尋未找到"}
    props = {k: v for k, v in props.items() if v not in (None, "")}
    if country:
        props["國家"] = country
    return f"""Create ONE row in the Notion database 投遞紀錄 (data source collection://{APPLICATIONS_DS}) with these
properties (JSON, use exactly): {json.dumps(props, ensure_ascii=False)}
If the data source has no property "CV Drive" (URL), "CL Drive" (URL) or "內推候選" (text), add the missing ones first
with notion-update-data-source (do not change any other property).

Attach these PDFs in the row's page body: {json.dumps(pdfs)} (paths relative to {REPO}).
For each PDF: call notion-create-file-upload, upload the file with ONE curl command exactly as that tool's result
instructs (curl -sS -X POST …, -F "file=@<absolute path>"), then put `<pdf source="file-upload://<ID>" />` in the page
content (create the page with the content, or add it with notion-update-page) within one hour of the upload.
After the PDFs, add this section to the page body verbatim:

{referral.notion_markdown(people)}

Do not touch any other Notion page. End with ONE fenced ```json block: {{"page_url": "<url of the new row>", "attached": <n PDFs>}}"""


def summary_prompt(title, markdown):
    return f"""Create ONE Notion page titled {json.dumps(title, ensure_ascii=False)} as a child page of the page
{GERMAN_PLAN_PAGE} (GERMAN PLAN), with exactly this content (Notion-flavoured Markdown):

{markdown}

Do not touch any other page. End with ONE fenced ```json block: {{"page_url": "<url>"}}"""


# ---- steps ------------------------------------------------------------------

def to_pipeline_row(job, sc, day):
    loc = sc.get("location") or {}
    return {"date": day, "company": job.get("company") or sc.get("company", ""), "title": job.get("title") or sc.get("title", ""),
            "url": job["url"], "type": sc.get("type", ""), "city": job.get("city") or loc.get("city", ""),
            "country": job.get("country") or loc.get("country", ""), "mode": job.get("mode") or loc.get("mode", "未註明"),
            "score": sc.get("score"), "reasons": sc.get("reasons", [])}


def step_search(ctx):
    st = ctx.ledger.step("search")
    if st.get("done") and not ctx.force:
        ctx.log(f"search: 今天已完成({st.get('added', 0)} 筆新增),略過")
        return
    hours = env_int("PIPELINE_HOURS", 48)
    last = last_search_day()
    if last and last < ctx.day:  # machine was off: widen the window to cover the gap (max 7 days)
        gap = (dt.date.fromisoformat(ctx.day) - dt.date.fromisoformat(last)).days
        hours = min(168, max(hours, gap * 24 + 24))
    known = ctx.sheet.known_keys()
    known_file = DATA / "runs" / f"{ctx.day}.known_urls.txt"
    known_file.write_text("\n".join(sorted(known)), encoding="utf-8")
    if ctx.dry_run:
        found = json.loads(Path(ctx.fixture_jobs).read_text(encoding="utf-8")) if ctx.fixture_jobs else {"jobs": []}
        found = found if isinstance(found, dict) else {"jobs": found}
        ctx.log(f"search: dry-run, {len(found['jobs'])} 筆 fixture 職缺")
    else:
        found, cost = run_claude(search_prompt(hours, known_file.relative_to(REPO) if known_file.is_relative_to(REPO) else known_file),
                                 SEARCH_TOOLS, "search", ctx.log, ctx.day, timeout=3600)
        ctx.cost += cost
    jobs = [j for j in found.get("jobs", []) if re.match(r"https?://", str(j.get("url") or ""))]
    fresh, seen = [], set(known)
    for j in jobs:
        k = score_jobs.url_key(j["url"])
        if k in seen:
            continue
        seen.add(k)
        fresh.append(j)
    ctx.log(f"search: {len(jobs)} 筆有連結,{len(fresh)} 筆是新的(視窗 {hours} 小時)")
    fetch = (lambda u: "(dry-run JD)") if ctx.dry_run else score_jobs.fetch_jd
    scored = score_jobs.score_jobs(fresh, dry_run=ctx.dry_run, log=ctx.log, fetch=fetch)
    rows = []
    for j, sc in zip(fresh, scored):
        if sc.get("error"):
            sc = {"score": None, "reasons": [sc["error"]], "type": ""}
        rows.append(to_pipeline_row(j, sc, ctx.day))
    added = ctx.sheet.update("pipeline_append", rows=rows)["added"] if rows else []
    added_keys = {score_jobs.url_key(a["url"]) for a in added}
    st.update(done=True, hours=hours, found=len(jobs), added=len(added), skipped=found.get("skipped", []),
              new=[r for r in rows if score_jobs.url_key(r["url"]) in added_keys])
    ctx.ledger.save()
    step_notion(ctx, "notion(搜尋後)")


def step_rescore(ctx):
    """Score 每日職缺 rows that have no score yet and are still undecided (e.g. the backfilled legacy rows).
    At most PIPELINE_RESCORE_MAX (default 40) per day; runs once per day."""
    st = ctx.ledger.step("rescore")
    if st.get("done") and not ctx.force:
        ctx.log(f"rescore: 今天已完成({st.get('scored', 0)} 筆),略過")
        return
    limit = env_int("PIPELINE_RESCORE_MAX", 40)
    rows = [r for r in ctx.sheet.read("pipeline_list").get("undecided", [])
            if r.get("score") in (None, "") and re.match(r"https?://", str(r.get("url") or ""))][:limit]
    if not rows:
        st.update(done=True, scored=0)
        ctx.ledger.save()
        return
    fetch = (lambda u: "(dry-run JD)") if ctx.dry_run else score_jobs.fetch_jd
    scored = score_jobs.score_jobs([{"url": r["url"], "company": r.get("company"), "title": r.get("title")} for r in rows],
                                   dry_run=ctx.dry_run, log=ctx.log, fetch=fetch)
    updates = []
    for r, sc in zip(rows, scored):
        if sc.get("error"):
            updates.append({"url": r["url"], "reasons": [sc["error"]]})
            continue
        loc = sc.get("location") or {}
        u = {"url": r["url"], "type": sc.get("type", ""), "score": sc.get("score"), "reasons": sc.get("reasons", [])}
        for k in ("city", "country", "mode"):  # fill only what the legacy row lacks
            if not r.get(k) and loc.get(k):
                u[k] = loc[k]
        updates.append(u)
    if updates:
        ctx.sheet.update("pipeline_update", rows=updates)
    done = sum(1 for u in updates if u.get("score") is not None)
    ctx.log(f"rescore: {done}/{len(rows)} 筆補上評分(上限 {limit}/天)")
    st.update(done=True, scored=done)
    ctx.ledger.save()


def step_interest(ctx):
    rows = ctx.sheet.read("interest_list").get("rows", [])
    st = ctx.ledger.step("interest")
    st.setdefault("scored", [])
    max_attempts = env_int("PIPELINE_MAX_ATTEMPTS", 2)
    for r in rows:
        key = score_jobs.url_key(r["url"])
        jrec = ctx.ledger.job(key)
        if jrec.get("interest_failed_today"):
            continue
        jd, company, title = None, r.get("company", ""), r.get("title", "")
        if ctx.dry_run:
            jd = "(dry-run JD)"
        else:
            jd = score_jobs.fetch_jd(r["url"])
            if not jd:
                try:
                    got, cost = run_claude(jd_prompt(r["url"], r.get("note")), JD_TOOLS, f"jd_{key[:40].replace('/', '_')}",
                                           ctx.log, ctx.day, timeout=900)
                    ctx.cost += cost
                    if got.get("found"):
                        jd, company, title = got.get("jd"), company or got.get("company", ""), title or got.get("title", "")
                except Exception as e:
                    ctx.log(f"interest: JD 搜尋失敗 {r['url']}: {e}")
        sc = score_jobs.score_jobs([{"url": r["url"], "company": company, "title": title, "jd": jd}],
                                   dry_run=ctx.dry_run, log=ctx.log, fetch=None)[0] if jd else {"error": "JD 無法取得"}
        if sc.get("error"):
            fails, _ = job_history(key)
            jrec["failures"] = jrec.get("failures", 0) + 1
            jrec["interest_failed_today"] = True
            jrec["last_error"] = sc["error"]
            ctx.failures.append({"url": r["url"], "step": "興趣職缺評分", "reason": sc["error"]})
            if fails + 1 >= max_attempts:
                ctx.sheet.update("interest_update", rows=[{"url": r["url"], "state": "失敗",
                                                           "reasons": [sc["error"] + ";請改貼公司官網連結,或在備註貼 JD"]}])
            ctx.ledger.save()
            continue
        loc = sc.get("location") or {}
        ctx.sheet.update("interest_update", rows=[{
            "url": r["url"], "company": company or sc.get("company", ""), "title": title or sc.get("title", ""),
            "type": sc["type"], "city": loc.get("city", ""), "country": loc.get("country", ""), "mode": loc.get("mode", "未註明"),
            "score": sc["score"], "reasons": sc["reasons"], "state": "待產 CV"}])
        st["scored"].append({"url": r["url"], "company": company or sc.get("company", ""), "score": sc["score"],
                             "reasons": sc.get("reasons", [])})
        ctx.ledger.save()
    ctx.log(f"interest: {len(rows)} 筆待處理,{len(st['scored'])} 筆已評分")
    step_notion(ctx, "notion(興趣職缺後)")


def build_queue(ctx):
    interest = ctx.sheet.read("interest_list").get("pending_cv", [])
    daily = sorted(ctx.sheet.read("pipeline_list").get("queue", []), key=lambda r: -(r.get("score") or 0))
    q = [dict(r, _tab="interest", _source="興趣") for r in interest] + [dict(r, _tab="pipeline", _source="AI 找的") for r in daily]
    seen, out = set(), []
    for r in q:
        k = score_jobs.url_key(r["url"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def fake_cv(job, day):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", job.get("company") or "Co").strip("-")
    return {"status": "ok", "jd_key": slug, "company": job.get("company"), "title": job.get("title"),
            "app_dir": f"cv/applications/{day.replace('-', '')}_{slug}_DRYRUN", "cv_pdf": "", "cover_letter_pdf": "",
            "cover_dir": "", "ats": None, "pending_approvals": [], "dry_run": True}


def step_cv(ctx):
    limit = ctx.max_cv if ctx.max_cv is not None else env_int("PIPELINE_MAX_CV", 5)
    max_attempts = env_int("PIPELINE_MAX_ATTEMPTS", 2)
    st = ctx.ledger.step("cv")
    st.setdefault("produced", [])
    queue = build_queue(ctx)
    started = sum(1 for j in ctx.ledger.d["jobs"].values() if j.get("cv_started_today"))
    ctx.log(f"cv: 佇列 {len(queue)} 筆,今天上限 {limit},已開始 {started}")
    for i, job in enumerate(queue):
        key = score_jobs.url_key(job["url"])
        rec = ctx.ledger.job(key)
        prior_fail, prior = job_history(key)
        for k, v in prior.items():
            rec.setdefault(k, v)
        if rec.get("failed_today"):
            continue
        if not rec.get("cv"):
            if started >= limit:
                ctx.log(f"cv: 已達今天上限 {limit},其餘 {len(queue) - i} 筆明天處理")
                break
            started += 1
            rec["cv_started_today"] = True
            ctx.ledger.save()
            run_id = f"pipeline-{ctx.day}-{i + 1}"
            try:
                if ctx.dry_run:
                    cv = fake_cv(job, ctx.day)
                else:
                    cv, cost = run_claude(cv_prompt(job, run_id), CV_TOOLS, f"cv_{run_id}", ctx.log, ctx.day,
                                          timeout=env_int("PIPELINE_CV_TIMEOUT", 5400))
                    ctx.cost += cost
                if cv.get("status") != "ok":
                    raise RuntimeError(cv.get("reason") or "CV 產生失敗")
                if not ctx.dry_run:
                    missing = [p for p in (cv.get("cv_pdf"), cv.get("cover_letter_pdf")) if not p or not (REPO / p).exists()]
                    if not cv.get("cv_pdf") or not (REPO / cv["cv_pdf"]).exists():
                        raise RuntimeError(f"找不到 CV PDF:{cv.get('cv_pdf')}")
                    if missing:
                        ctx.log(f"cv: 缺少檔案 {missing}(Cover letter 可能失敗),繼續")
                rec["cv"] = cv
                ctx.ledger.save()
            except Exception as e:
                fail(ctx, job, rec, prior_fail, max_attempts, f"CV/Cover letter:{e}")
                continue
        cv = rec["cv"]
        company = job.get("company") or cv.get("company") or ""
        title = job.get("title") or cv.get("title") or ""
        if "drive" not in rec:  # F1: Drive view links (same file id on re-upload); a failure keeps local paths
            try:
                rec["drive"] = {"cv": None, "cover_letter": None} if ctx.dry_run else \
                    drive_upload.upload_application(company, cv.get("cv_pdf"), cv.get("cover_letter_pdf"))
            except Exception as e:
                rec["drive"] = {"cv": None, "cover_letter": None, "error": str(e)[:200]}
                ctx.failures.append({"url": job["url"], "company": company, "step": "Google Drive", "reason": str(e)[:200]})
            ctx.ledger.save()
        drive = rec["drive"]
        if "referrals" not in rec:  # F2 + F3: public-search referral candidates + messages
            people = []
            if not ctx.dry_run:
                try:
                    got, cost = run_claude(referral.search_prompt(company, title, job.get("city", "")), REFERRAL_TOOLS,
                                           f"referral_{key[:40].replace('/', '_')}", ctx.log, ctx.day, timeout=900)
                    ctx.cost += cost
                    people = referral.parse_candidates(got)
                except Exception as e:
                    ctx.failures.append({"url": job["url"], "company": company, "step": "內推候選", "reason": str(e)[:200]})
            rec["referrals"] = referral.with_messages(people, company, title, drive.get("cv") or "")
            ctx.ledger.save()
        people = rec["referrals"]
        if not rec.get("notion"):
            try:
                if ctx.dry_run:
                    rec["notion"] = {"page_url": "", "dry_run": True}
                else:
                    got, cost = run_claude(notion_row_prompt(job, cv, ctx.day, drive, people), NOTION_ROW_TOOLS,
                                           f"notion_{key[:40].replace('/', '_')}", ctx.log, ctx.day, timeout=900)
                    ctx.cost += cost
                    rec["notion"] = got
                ctx.ledger.save()
            except Exception as e:  # the CV exists; the job stays queued and only this step is retried next run
                ctx.failures.append({"url": job["url"], "company": company, "step": "Notion 投遞紀錄", "reason": str(e)[:200]})
                ctx.log(f"cv: Notion 列失敗 {job['url']}: {e}")
                continue
        notion_url = (rec.get("notion") or {}).get("page_url") or ""
        if not rec.get("submission"):
            row = {"date": ctx.day, "company": company, "title": title, "url": job["url"], "type": job.get("type", ""),
                   "source": job["_source"], "city": job.get("city", ""), "country": job.get("country", ""),
                   "mode": job.get("mode") or "未註明", "ats": cv.get("ats"), "score": job.get("score"),
                   "requirements": [r.lstrip("• ").strip() for r in str(job.get("reasons") or "").splitlines() if r.strip()],
                   "notion": notion_url, "has_cover_letter": bool(cv.get("cover_letter_pdf")),
                   "cv": drive.get("cv") or cv.get("cv_pdf", ""), "cover_letter": drive.get("cover_letter") or cv.get("cover_letter_pdf", ""),
                   "app_dir": cv.get("app_dir", ""), "referrers": [{k: p[k] for k in ("name", "title", "url")} for p in people],
                   "referral_message": people[0]["full"] if people else "",
                   "referral_data": [{k: p[k] for k in ("name", "title", "url", "group", "why", "full", "note")} for p in people]}
            res = ctx.sheet.update("submissions_append", rows=[row])
            hit = (res.get("added") or [{}])[0].get("row") or next((s.get("row") for s in res.get("skipped", []) if s.get("row")), None)
            rec["submission"] = {"row": hit}
            ctx.ledger.save()
        upd = {"url": job["url"], "state": "已產 CV"}
        if rec["submission"].get("row"):
            upd["submission_row"] = rec["submission"]["row"]
        ctx.sheet.update("interest_update" if job["_tab"] == "interest" else "pipeline_update", rows=[upd])
        rec["done"] = True
        rec["application_notion"] = notion_url
        st["produced"].append({"url": job["url"], "company": company, "title": title, "ats": cv.get("ats"),
                               "app_dir": cv.get("app_dir"), "notion": notion_url, "cv_drive": drive.get("cv"),
                               "referrals": [p["name"] for p in people],
                               "pending_approvals": cv.get("pending_approvals", [])})
        ctx.ledger.save()


def fail(ctx, job, rec, prior_fail, max_attempts, reason):
    rec["failures"] = rec.get("failures", 0) + 1
    rec["failed_today"] = True
    rec["last_error"] = reason[:500]
    ctx.failures.append({"url": job["url"], "company": job.get("company"), "step": "CV", "reason": reason[:300]})
    ctx.log(f"cv: 失敗 {job['url']}: {reason[:300]}")
    if prior_fail + rec["failures"] >= max_attempts:
        ctx.sheet.update("interest_update" if job["_tab"] == "interest" else "pipeline_update",
                         rows=[{"url": job["url"], "state": "失敗"}])
    ctx.ledger.save()


def step_notion(ctx, label="notion"):
    """Sheet → Notion mirror of 每日職缺 / 興趣職缺 (new rows + rows whose 決定/處理狀態/… changed)."""
    res = notion_sync.sync(ctx.sheet, lambda p, t, l: run_claude(p, t, l, ctx.log, ctx.day, timeout=1200),
                           DATA / "notion_state.json", ctx.log, dry_run=ctx.dry_run or not ctx.sheet.write)
    ctx.cost += res.pop("cost", 0)
    st = ctx.ledger.step("notion")
    for tab, x in res.items():
        t = st.setdefault(tab, {"created": 0, "updated": 0, "failed": 0})
        for k in ("created", "updated", "failed"):
            t[k] += x[k]
        if x["failed"]:
            ctx.failures.append({"url": "", "step": f"Notion {tab} 同步", "reason": f"{x['failed']} 筆失敗,下次重試"})
    ctx.log(f"{label}: " + ", ".join(f"{tab} +{x['created']} ~{x['updated']}" for tab, x in res.items()))
    ctx.ledger.save()


def summary_markdown(ctx):
    led = ctx.ledger.d["steps"]
    new = sorted(led.get("search", {}).get("new", []), key=lambda r: -(r.get("score") or 0))
    produced = led.get("cv", {}).get("produced", [])
    undecided = len(ctx.sheet.read("pipeline_list").get("undecided", []))
    cfg = sheet.config() if sheet.CONFIG.exists() else {}
    lines = [f"# 每日職缺摘要 {ctx.day}", "",
             f"- 新職缺:**{len(new)}** 筆(搜尋 {led.get('search', {}).get('hours', '—')} 小時內,找到 {led.get('search', {}).get('found', 0)} 筆有連結)",
             f"- 興趣職缺評分:{len(led.get('interest', {}).get('scored', []))} 筆",
             f"- 已產 CV + Cover letter:**{len(produced)}** 筆",
             f"- 每日職缺「未確認」:**{undecided}** 筆,請在 Sheet 或 dashboard 決定 是/否",
             "- Notion 同步:" + "、".join(f"{t} 新增 {x.get('created', 0)} / 更新 {x.get('updated', 0)}"
                                         for t, x in led.get("notion", {}).items()),
             f"- 今日 Claude/Codex 花費(估):${ctx.cost:.2f}"]
    if cfg.get("sheet_url"):
        lines.append(f"- Google Sheet:{cfg['sheet_url']}")
    low = [x for x in led.get("interest", {}).get("scored", []) if isinstance(x.get("score"), (int, float)) and x["score"] <= 4]
    if low:
        lines += ["", "## ⚠️ 低分興趣職缺(仍照你的指示產 CV)"]
        lines += [f"- {x.get('company') or x['url']} · 評分 {x['score']}/10 · {'; '.join(x.get('reasons') or [])}\n  {x['url']}" for x in low]
    lines += ["", "## 前 5 名(依評分)"]
    lines += [f"{i}. **{r.get('score') or '—'}** · {r['company']} — {r['title']}({r.get('city') or ''} {r.get('country') or ''},{r.get('mode') or ''})· {r.get('type') or ''}\n   {'; '.join(r.get('reasons') or [])}\n   {r['url']}"
              for i, r in enumerate(new[:5], 1)] or ["今天沒有新職缺。"]
    lines += ["", "## 已產 CV"]
    lines += [f"- {p['company']} — {p['title']} · ATS {p.get('ats') if p.get('ats') is not None else '—'} · {p.get('app_dir') or ''}"
              + (f" · [Notion]({p['notion']})" if p.get("notion") else "")
              + (f" · [CV Drive]({p['cv_drive']})" if p.get("cv_drive") else "")
              + (f"\n  內推候選:{'、'.join(p['referrals'])}" if p.get("referrals") else "\n  內推候選:公開搜尋未找到")
              + (f"\n  待你核准:{'; '.join(p['pending_approvals'])}" if p.get("pending_approvals") else "")
              for p in produced] or ["今天沒有產生 CV。"]
    lines += ["", "## 失敗"]
    lines += [f"- {f.get('company') or ''} {f['url']} · {f['step']}:{f['reason']}" for f in ctx.failures] or ["無。"]
    skipped = led.get("search", {}).get("skipped", [])
    if skipped:
        lines += ["", "## 搜尋時略過"] + [f"- {s.get('company', '')} — {s.get('title', '')}:{s.get('reason', '')}" for s in skipped[:15]]
    return "\n".join(lines)


def step_summary(ctx):
    md = summary_markdown(ctx)
    print(md)
    st = ctx.ledger.step("summary")
    st["text"] = md
    if st.get("page_url") and not ctx.force:
        ctx.log(f"summary: Notion 頁已存在 {st['page_url']}(重跑只更新本機紀錄)")
    elif ctx.dry_run:
        ctx.log("summary: dry-run,不建立 Notion 頁")
    else:
        try:
            got, cost = run_claude(summary_prompt(f"每日職缺摘要 {ctx.day}", md), SUMMARY_TOOLS, "summary", ctx.log, ctx.day, timeout=600)
            ctx.cost += cost
            st["page_url"] = got.get("page_url")
        except Exception as e:
            ctx.log(f"summary: Notion 頁失敗: {e}")
    ctx.ledger.save()


# ---- main -------------------------------------------------------------------

class Ctx:
    pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="no Claude/Codex calls; fake scores and CV results")
    ap.add_argument("--fixture-jobs", help="dry-run: search result JSON ({jobs:[…]} or a list)")
    ap.add_argument("--date", help="run date YYYY-MM-DD (default: today, Asia/Taipei)")
    ap.add_argument("--only", choices=["search", "rescore", "interest", "cv", "notion", "summary"], action="append")
    ap.add_argument("--drive-backfill", action="store_true",
                    help="upload the existing tailored CV / cover-letter PDFs to Drive (CV for referrals/<Company>/) and exit")
    ap.add_argument("--max-cv", type=int)
    ap.add_argument("--force", action="store_true", help="redo the search / summary page even if the ledger has them")
    ap.add_argument("--live", action="store_true",
                    help="use the real Google Sheet (~/.config/ai-job-tracker/sheet.json); without it the run refuses "
                         "unless AI_JOB_TRACKER_SHEET_CONFIG names a config (tests: the fake). With --dry-run: read-only")
    a = ap.parse_args(argv)

    if a.drive_backfill:
        try:
            print(json.dumps(drive_upload.backfill(dry_run=a.dry_run), ensure_ascii=False, indent=2))
        except drive_upload.DriveError as e:
            print(f"Drive 上傳失敗:{e}")
            return 2
        return 0
    ctx = Ctx()
    ctx.day = a.date or dt.datetime.now(TZ).date().isoformat()
    ctx.dry_run, ctx.force, ctx.fixture_jobs, ctx.max_cv = a.dry_run, a.force, a.fixture_jobs, a.max_cv
    ctx.log = Log(ctx.day)
    ctx.ledger = Ledger(ctx.day, dry_run=a.dry_run)
    ctx.failures, ctx.cost = ctx.ledger.d.setdefault("failures", []), 0.0
    explicit = bool(os.environ.get("AI_JOB_TRACKER_SHEET_CONFIG"))
    if not explicit and not a.live:
        print("預設不連正式 Google Sheet:排程與手動正式執行請加 --live;測試請把 AI_JOB_TRACKER_SHEET_CONFIG 指向假伺服器。")
        return 2
    ctx.sheet = Sheet(write=not (a.dry_run and not explicit), log=ctx.log)

    DATA.mkdir(parents=True, exist_ok=True)
    lock = open(DATA / "pipeline.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        ctx.log("另一個 daily_pipeline 正在執行,結束")
        return 3
    ctx.log(f"=== daily pipeline {ctx.day}{' (dry-run)' if a.dry_run else ''} ===")
    try:
        sheet.pipeline("version")
    except Exception as e:  # expired / revoked Google login, no network, …: say so in the summary, stop
        ctx.log(f"Google Sheet 無法使用:{e}")
        ctx.failures.append({"url": "", "step": "Google Sheet", "reason": str(e)})
        md = (f"# 每日職缺摘要 {ctx.day}\n\n- ⚠️ **Google Sheet 無法使用,今天沒有執行任何步驟。**\n- 原因:{e}\n"
              "- 授權過期時:在 WSL 執行 `python3 .claude/skills/daily-job-tracker/scripts/google_auth.py --auth`;"
              "若每 7 天就過期,把 OAuth consent screen 改成 In production。")
        print(md)
        st = ctx.ledger.step("summary")
        st["text"] = md
        if not ctx.dry_run and not st.get("page_url"):
            try:
                got, cost = run_claude(summary_prompt(f"每日職缺摘要 {ctx.day}", md), SUMMARY_TOOLS, "summary", ctx.log, ctx.day, timeout=600)
                st["page_url"] = got.get("page_url")
            except Exception as e2:
                ctx.log(f"summary: Notion 頁失敗: {e2}")
        ctx.ledger.save()
        return 2
    steps = a.only or ["search", "rescore", "interest", "cv", "notion", "summary"]
    for name, fn in (("search", step_search), ("rescore", step_rescore), ("interest", step_interest), ("cv", step_cv),
                     ("notion", step_notion),
                     ("summary", step_summary)):
        if name not in steps:
            continue
        try:
            fn(ctx)
        except Exception as e:  # one step failing never blocks the others
            ctx.log(f"{name}: 步驟失敗: {e!r}")
            ctx.failures.append({"url": "", "step": name, "reason": str(e)[:300]})
    ctx.ledger.d.setdefault("cost_usd", 0)
    ctx.ledger.d["cost_usd"] = round(ctx.ledger.d["cost_usd"] + ctx.cost, 2)
    ctx.ledger.save()
    ctx.log(f"=== done, ${ctx.cost:.2f} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
