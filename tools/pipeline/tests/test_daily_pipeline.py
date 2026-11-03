"""daily_pipeline.py end to end on the fake Sheet: dry-run, and a full run with fake claude + fake codex."""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

from fakesheet import FakeSheet, SCRIPTS
from fake_drive import FakeDrive, serve

HERE = Path(__file__).resolve().parent
PIPE = SCRIPTS / "daily_pipeline.py"
JD = (HERE / "fixtures" / "jd_agent_engineer.txt").read_text(encoding="utf-8")
JOBS = {"jobs": [
    {"company": "Fixture Labs", "title": "AI Agent Engineer", "url": "https://jobs.fixture.example/agent", "city": "Berlin",
     "country": "德國", "mode": "Hybrid", "jd": JD},
    {"company": "Vision Co", "title": "Computer Vision Engineer", "url": "https://jobs.example.com/vision", "city": "Amsterdam",
     "country": "荷蘭", "mode": "Remote", "jd": JD},
    {"company": "Acme AI", "title": "AI Engineer", "url": "https://www.arbeitnow.com/jobs/acme-ai-engineer-123", "jd": JD},
    {"company": "Ghost", "title": "No Link", "url": "", "jd": JD},
], "skipped": [{"company": "Big Co", "title": "Staff ML Engineer", "reason": "職級 Staff"}]}


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSheet()
        self.fake.call("setup_pipeline")
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.data = t / "pipeline"
        self.jobs = t / "jobs.json"
        self.jobs.write_text(json.dumps(JOBS, ensure_ascii=False), encoding="utf-8")
        self.claude_log = t / "claude.log"
        self.drive = FakeDrive()
        self.drive_srv, drive_url = serve(self.drive)
        (t / "conf").mkdir()
        self.env = {**self.fake.env, "PIPELINE_DATA": str(self.data), "DRIVE_API_BASE": drive_url,
                    "DRIVE_ACCESS_TOKEN": "fake-token", "AI_JOB_TRACKER_CONF_DIR": str(t / "conf"), "CLAUDE_BIN": str(HERE / "fake_claude.py"),
                    "CODEX_BIN": str(HERE / "fake_codex.py"), "FAKE_CLAUDE_LOG": str(self.claude_log),
                    "FAKE_CLAUDE_JOBS": str(self.jobs), "FAKE_CLAUDE_OUT": str(t / "out"), "CLAUDECODE": "1"}

    def tearDown(self):
        self.drive_srv.shutdown()
        self.fake.close()
        self.tmp.cleanup()

    def run_(self, *args, env=None):
        p = subprocess.run([sys.executable, str(PIPE), "--date", "2026-11-01", *args], env={**self.env, **(env or {})},
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        return p

    def ledger(self, day="2026-11-01", dry_run=False):
        return json.loads((self.data / ("dry_runs" if dry_run else "runs") / f"{day}.json").read_text(encoding="utf-8"))

    def notion_ops(self):
        p = Path(str(self.claude_log) + ".notion")
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def claude_calls(self):
        return [json.loads(x) for x in self.claude_log.read_text().splitlines()] if self.claude_log.exists() else []

    def test_dry_run_full_cycle_is_idempotent(self):
        self.fake.call("interest_append", rows=[{"url": "https://jobs.example.com/interest-1", "note": "強調 MLOps"}])
        p = self.run_("--dry-run", "--fixture-jobs", str(self.jobs))
        if any(x["score"] <= 4 for x in self.ledger(dry_run=True)["steps"]["interest"]["scored"]):
            self.assertIn("低分興趣職缺", p.stdout)
        rows = self.fake.table("每日職缺")
        self.assertEqual(sorted(r["公司"] for r in rows), ["Fixture Labs", "Vision Co"])  # Acme = old tracker dup, Ghost = no url
        self.assertTrue(all(r["決定"] == "未確認" and r["處理狀態"] == "待決定" and r["評分"] for r in rows))
        interest = self.fake.table("興趣職缺")[0]
        self.assertEqual(interest["處理狀態"], "已產 CV")  # interest rows go straight to CV
        subs = self.fake.table("投遞表單")
        self.assertEqual([(s["來源"], s["連結"]) for s in subs], [("興趣", "https://jobs.example.com/interest-1")])
        self.assertIn("# 每日職缺摘要 2026-11-01", p.stdout)
        self.assertIn("「未確認」:**2**", p.stdout)
        self.assertEqual(self.claude_calls(), [])  # dry-run never calls claude
        self.assertFalse((self.data / "scores.json").exists())

        # the candidate says 是 to one job; a second run the same day: no new search, one CV, no duplicates
        self.fake.call("pipeline_update", rows=[{"url": "https://jobs.example.com/vision", "decision": "是"}])
        self.run_("--dry-run", "--fixture-jobs", str(self.jobs))
        self.run_("--dry-run", "--fixture-jobs", str(self.jobs))
        self.assertEqual(len(self.fake.table("每日職缺")), 2)
        subs = self.fake.table("投遞表單")
        self.assertEqual([s["來源"] for s in subs], ["興趣", "AI 找的"])
        self.assertEqual(subs[1]["公司"], "Vision Co")
        vision = [r for r in self.fake.table("每日職缺") if r["公司"] == "Vision Co"][0]
        self.assertEqual((vision["處理狀態"], vision["投遞表單列"]), ("已產 CV", "第 3 列"))
        self.assertEqual(len(self.ledger(dry_run=True)["steps"]["cv"]["produced"]), 2)

    def test_default_config_needs_live_flag_and_dry_run_live_is_read_only(self):
        env = dict(self.env)
        cfg = env.pop("AI_JOB_TRACKER_SHEET_CONFIG")
        home = Path(self.tmp.name) / "home"
        (home / ".config" / "ai-job-tracker").mkdir(parents=True)
        (home / ".config" / "ai-job-tracker" / "sheet.json").write_text(Path(cfg).read_text())  # stands in for the live Sheet
        env["HOME"] = str(home)
        run = lambda *a: subprocess.run([sys.executable, str(PIPE), "--date", "2026-11-01", *a], env=env,
                                        capture_output=True, text=True, timeout=120)
        log = lambda: len(json.loads(urllib.request.urlopen(self.fake.url + "/__log").read()))
        before = log()
        p = run("--dry-run", "--fixture-jobs", str(self.jobs))
        self.assertEqual(p.returncode, 2)
        self.assertIn("--live", p.stdout)
        self.assertEqual(log(), before)  # refused before any Sheets API request
        p = run("--dry-run", "--live", "--fixture-jobs", str(self.jobs))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Sheet 未寫入", p.stderr)
        self.assertEqual(self.fake.table("每日職缺"), [])
        self.assertFalse((self.data / "runs" / "2026-11-01.json").exists())
        self.assertTrue(self.ledger(dry_run=True)["steps"]["search"]["done"])
        self.run_("--only", "search")
        self.assertTrue(self.claude_calls()[0]["prompt"].startswith("/daily-job-tracker"))
        push = subprocess.run([sys.executable, str(SCRIPTS / "push_to_sheet.py"), "--pipeline-list"], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("--live", push.stderr)

    def test_full_run_with_fake_claude_and_codex(self):
        self.fake.call("interest_append", rows=[{"url": "https://www.linkedin.com/jobs/view/999", "note": "想做 CV"}])
        # three days off: the window widens to 96 h
        (self.data / "runs").mkdir(parents=True)
        (self.data / "runs" / "2026-10-29.json").write_text(json.dumps({"date": "2026-10-29", "steps": {"search": {"done": True}}, "jobs": {}}))
        p = self.run_()
        calls = self.claude_calls()
        self.assertTrue(calls[0]["prompt"].startswith("/daily-job-tracker --hours 96 --pipeline"))
        self.assertEqual(calls[0]["tools"], "ToolSearch,WebSearch,WebFetch,Read")
        self.assertFalse(any(c["env_claudecode"] for c in calls))  # parent-session vars removed
        kinds = [c["prompt"].split("\n")[0][:30] for c in calls]
        self.assertEqual(sum(k.startswith("Find the job description") for k in kinds), 1)
        self.assertEqual(sum(k.startswith("Headless daily pipeline run") for k in kinds), 1)
        cv_call = next(c for c in calls if c["prompt"].startswith("Headless daily pipeline run"))
        self.assertIn("想做 CV", cv_call["prompt"])
        self.assertIn("Agent", cv_call["tools"].split(","))
        interest = self.fake.table("興趣職缺")[0]
        self.assertEqual((interest["公司"], interest["類型"], interest["評分"], interest["處理狀態"]), ("Interest Co", "AI agent", "9", "已產 CV"))
        d = self.fake.dump()["投遞表單"]
        head = d["display"][0]
        sub = dict(zip(head, d["display"][1]))
        f = dict(zip(head, d["formulas"][1]))
        self.assertEqual((sub["ATS"], sub["CV 連結"], sub["Cover letter 連結"], sub["來源"], sub["狀態"]),
                         ("72.5", "CV PDF", "Cover letter PDF", "興趣", "準備中"))
        self.assertIn("drive.google.com/file/d/", f["CV 連結"])
        self.assertTrue(f["履歷版本"].startswith('=HYPERLINK("https://www.notion.so/fake-row-'))
        self.assertEqual(sub["履歷版本"], "v3 cards · ATS 72.5 + CL")
        self.assertEqual(sub["內推人"].splitlines()[0], "Ann Lee (Senior ML Engineer) — https://www.linkedin.com/in/ann-lee-ml")
        self.assertTrue(sub["內推訊息"].startswith("Hi Ann,\nMy name is Amo Hsu"))
        self.assertIn("drive.google.com/file/d/", sub["內推訊息"])
        notion_row = next(c for c in calls if c["prompt"].startswith("Create ONE row in the Notion database"))
        self.assertIn('"CV Drive": "https://drive.google.com/file/d/', notion_row["prompt"])
        self.assertIn("## 內推訊息", notion_row["prompt"])
        ref_call = next(c for c in calls if c["prompt"].startswith("Find 2–4 people at"))
        self.assertEqual(ref_call["tools"], "ToolSearch,WebSearch")
        pdfs = [v for v in self.drive.files.values() if v["name"].endswith(".pdf")]
        self.assertEqual((len(pdfs), all(p["anyone"] for p in pdfs)), (2, True))
        # Notion mirror: every 每日職缺 / 興趣職缺 row has its page; Sheet Notion column filled
        mirrored = {(o["tab"], o["op"]) for o in self.notion_ops()}
        self.assertEqual(mirrored, {("每日職缺", "create"), ("興趣職缺", "create"), ("興趣職缺", "update")})
        self.assertTrue(all(r["Notion"] == "Notion" for r in self.fake.table("每日職缺")))
        last_interest = [o for o in self.notion_ops() if o["tab"] == "興趣職缺"][-1]
        self.assertEqual(last_interest["properties"]["處理狀態"], "已產 CV")
        self.assertTrue(last_interest["properties"]["投遞紀錄"].startswith("https://www.notion.so/fake-row-"))
        daily = {r["公司"]: r for r in self.fake.table("每日職缺")}
        self.assertEqual(daily["Fixture Labs"]["評分"], "9")
        self.assertIn("DT1", daily["Fixture Labs"]["評分理由"])
        self.assertIn("待你核准:NEW LEAD for DT1", p.stdout)
        self.assertEqual(self.ledger()["steps"]["summary"]["page_url"], "https://www.notion.so/fake-summary")
        n = len(calls)
        self.run_()  # rerun: nothing new, the summary page is not created twice
        again = self.claude_calls()[n:]
        self.assertEqual(again, [])

    def test_notion_sync_mirrors_backfill_and_changed_decisions(self):
        self.fake.call("pipeline_backfill")
        self.run_("--only", "notion")
        ops = self.notion_ops()
        self.assertEqual([(o["tab"], o["op"], o["properties"]["決定"]) for o in ops],
                         [("每日職缺", "create", "未確認"), ("每日職缺", "create", "未確認")])
        self.assertEqual(ops[0]["properties"]["date:日期:start"], "2026-10-25")
        self.run_("--only", "notion")
        self.assertEqual(len(self.notion_ops()), 2)  # nothing changed → no Claude call
        self.fake.call("pipeline_update", rows=[{"url": "https://www.arbeitnow.com/jobs/acme-ai-engineer-123", "decision": "是"}])
        self.run_("--only", "notion")
        upd = self.notion_ops()[2:]
        self.assertEqual([(o["op"], o["properties"]["決定"], o["page_url"].startswith("https://www.notion.so/daily-")) for o in upd],
                         [("update", "是", True)])
        state = json.loads((self.data / "notion_state.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["每日職缺"]), 2)

    def test_notion_failure_is_retried_next_run(self):
        self.fake.call("pipeline_backfill")
        self.run_("--only", "notion", env={"FAKE_NOTION_FAIL": "1"})
        self.assertIn("同步", json.dumps(self.ledger()["failures"], ensure_ascii=False))
        self.assertEqual([r["Notion"] for r in self.fake.table("每日職缺")], ["", ""])
        self.run_("--only", "notion")
        self.assertEqual([r["Notion"] for r in self.fake.table("每日職缺")], ["Notion", "Notion"])

    def test_cv_failure_retries_then_marks_failed_and_respects_limit(self):
        jobs = {"jobs": [{"company": "FailCo", "title": "AI Engineer", "url": "https://jobs.example.com/fail", "jd": JD},
                         {"company": "OkCo", "title": "AI Engineer", "url": "https://jobs.example.com/ok", "jd": JD},
                         {"company": "LaterCo", "title": "AI Engineer", "url": "https://jobs.example.com/later", "jd": JD}]}
        self.jobs.write_text(json.dumps(jobs))
        self.run_("--only", "search")
        self.fake.call("pipeline_update", rows=[{"url": j["url"], "decision": "是"} for j in jobs["jobs"]])
        self.fake.call("pipeline_update", rows=[{"url": "https://jobs.example.com/fail", "score": 10}])
        self.run_("--only", "cv", env={"PIPELINE_MAX_CV": "2"})
        st = {r["公司"]: r["處理狀態"] for r in self.fake.table("每日職缺")}
        self.assertEqual(st, {"FailCo": "待決定", "OkCo": "已產 CV", "LaterCo": "待決定"})
        self.assertEqual(self.ledger()["failures"][0]["reason"], "CV/Cover letter:JD 無法讀取")
        p = subprocess.run([sys.executable, str(PIPE), "--date", "2026-11-02", "--only", "cv"], env=self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        st = {r["公司"]: r["處理狀態"] for r in self.fake.table("每日職缺")}
        self.assertEqual(st, {"FailCo": "失敗", "OkCo": "已產 CV", "LaterCo": "已產 CV"})
        self.assertEqual(len(self.fake.table("投遞表單")), 2)

    def test_unusable_sheet_stops_and_reports_in_summary(self):
        cfg = Path(self.tmp.name) / "broken.json"
        cfg.write_text(json.dumps({"api_base": "http://127.0.0.1:9", "access_token": "x"}))
        p = subprocess.run([sys.executable, str(PIPE), "--date", "2026-11-01"], env={**self.env, "AI_JOB_TRACKER_SHEET_CONFIG": str(cfg)},
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 2)
        self.assertIn("Google Sheet 無法使用,今天沒有執行任何步驟", p.stdout)
        self.assertIn("google_auth.py --auth", p.stdout)
        calls = self.claude_calls()
        self.assertEqual(len(calls), 1)  # only the Notion summary page
        self.assertTrue(calls[0]["prompt"].startswith("Create ONE Notion page titled"))

class Summary(unittest.TestCase):
    def test_low_score_interest_warning(self):
        sys.path.insert(0, str(SCRIPTS))
        import daily_pipeline as dp

        class S:
            write = True
            def read(self, action, **kw):
                return {"undecided": [1, 2]}
        ctx = type("C", (), {})()
        ctx.day, ctx.cost, ctx.failures, ctx.sheet = "2026-11-01", 1.5, [], S()
        ctx.ledger = type("L", (), {"d": {"steps": {"interest": {"scored": [
            {"url": "https://x.example/low", "company": "LowCo", "score": 3, "reasons": ["主要技術不符"]},
            {"url": "https://x.example/ok", "company": "OkCo", "score": 7, "reasons": []}]}}}})()
        md = dp.summary_markdown(ctx)
        self.assertIn("## ⚠️ 低分興趣職缺(仍照你的指示產 CV)\n- LowCo · 評分 3/10 · 主要技術不符", md)
        self.assertNotIn("OkCo · 評分", md)
        self.assertIn("「未確認」:**2**", md)
