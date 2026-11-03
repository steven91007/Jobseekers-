"""Apps Script pipeline actions, run against the real apps_script.gs on the fake web app."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fakesheet import FakeSheet, SCRIPTS

PUSH = SCRIPTS / "push_to_sheet.py"


class SheetActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeSheet()

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def setUp(self):
        self.fake.reset()
        self.assertTrue(self.fake.call("setup_pipeline")["ok"])

    def push(self, *args, rows=None):
        extra = []
        if rows is not None:
            f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            json.dump(rows, f, ensure_ascii=False)
            f.close()
            extra = [f.name]
        p = subprocess.run([sys.executable, "-I", str(PUSH), *args, *extra], env=self.fake.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_setup_is_idempotent_and_keeps_old_tabs(self):
        before = self.fake.dump()
        self.fake.call("setup_pipeline")
        after = self.fake.dump()
        for name in ("求職追蹤表", "求職追蹤表_AI 投遞", "投遞總覽"):
            self.assertEqual(before[name]["display"], after[name]["display"])
        self.assertEqual(after["每日職缺"]["display"][0][:4], ["日期", "公司", "職位", "連結"])
        self.assertEqual(after["每日職缺"]["frozen"], 1)
        self.assertEqual(after["興趣職缺"]["display"][0][:3], ["日期", "連結", "備註（人工）"])
        self.assertEqual(after["投遞表單"]["display"][0][:5], ["Industry", "順序", "狀態", "公司", "Position"])
        self.assertIn("FILTER('興趣職缺'!A2:N", after["興趣職缺_依日期"]["formulas"][3][0])
        self.assertEqual(after["每日職缺_依日期"]["validations"][0][1], "DATE_IS_VALID_DATE")

    def test_append_dedupes_against_every_tab(self):
        rows = [
            {"company": "New Co", "title": "AI Agent Engineer", "url": "https://jobs.example.com/new-1?utm=x",
             "type": "AI agent", "city": "Munich", "country": "德國", "mode": "hybrid", "score": 8,
             "reasons": ["LangGraph agent 經驗直接對應", "Python 後端"], "date": "2026-11-01"},
            {"company": "Acme AI", "title": "AI Engineer", "url": "https://arbeitnow.com/jobs/acme-ai-engineer-123/"},
            {"company": "Beta", "title": "x", "url": "https://de.linkedin.com/jobs/view/4012345678"},
            {"company": "Scout24", "title": "AI Automation Engineer", "url": "https://other.site/scout"},
            {"company": "No Link", "title": "Ghost", "url": ""},
            {"company": "New Co", "title": "AI Agent Engineer", "url": "https://jobs.example.com/new-1"},
        ]
        out = json.loads(self.push("--pipeline-append", rows=rows))
        self.assertEqual([a["url"] for a in out["added"]], ["https://jobs.example.com/new-1?utm=x"])
        reasons = [(s.get("tab"), s["reason"]) for s in out["skipped"]]
        self.assertEqual(reasons, [("求職追蹤表_AI 投遞", "duplicate"), ("求職追蹤表_AI 投遞", "duplicate"),
                                   ("求職追蹤表", "duplicate"), (None, "no url"), ("每日職缺", "duplicate")])
        row = self.fake.table("每日職缺")[0]
        self.assertEqual((row["日期"], row["工作型態"], row["評分"], row["決定"], row["處理狀態"]),
                         ("2026-11-01", "Hybrid", "8", "未確認", "待決定"))
        self.assertEqual(row["評分理由"], "• LangGraph agent 經驗直接對應\n• Python 後端")
        # an old-tracker row is not in 每日職缺 until the backfill
        again = json.loads(self.push("--pipeline-append", rows=rows[:1]))
        self.assertEqual(again["added"], [])

    def test_backfill_copies_prepared_rows_once(self):
        r = self.fake.call("pipeline_backfill")
        self.assertEqual(len(r["added"]), 2)
        r2 = self.fake.call("pipeline_backfill")
        self.assertEqual(r2["added"], [])
        self.assertEqual(len(self.fake.table("求職追蹤表_AI 投遞")), 4)  # originals stay
        rows = self.fake.table("每日職缺")
        self.assertEqual([(x["公司"], x["日期"], x["城市"], x["國家"], x["類型"], x["評分"]) for x in rows],
                         [("Acme AI", "2026-10-25", "Berlin", "德國", "", ""), ("Beta GmbH", "2026-10-26", "", "德國", "", "")])

    def test_list_update_and_validation(self):
        self.fake.call("pipeline_backfill")
        url = "https://www.arbeitnow.com/jobs/acme-ai-engineer-123"
        lst = json.loads(self.push("--pipeline-list"))
        self.assertEqual((len(lst["queue"]), len(lst["undecided"])), (0, 2))
        upd = json.loads(self.push("--pipeline-update", rows=[
            {"url": url + "?ref=1", "decision": "是", "note": "強調 RAG", "score": 9, "type": "AI agent"},
            {"url": "https://nope.example/x", "decision": "是"},
            {"url": url, "decision": "maybe"},
        ]))
        self.assertEqual((len(upd["updated"]), upd["not_found"], len(upd["rejected"])), (1, ["https://nope.example/x"], 1))
        lst = self.fake.call("pipeline_list", all=True)
        self.assertEqual([q["company"] for q in lst["queue"]], ["Acme AI"])
        self.assertEqual(lst["queue"][0]["note"], "強調 RAG")
        self.assertEqual(lst["queue"][0]["score"], 9)
        self.assertEqual(len(lst["rows"]), 2)
        self.fake.call("pipeline_update", rows=[{"url": url, "state": "已產 CV", "submission_row": 2,
                                                  "notion": "https://www.notion.so/abc"}])
        q = self.fake.call("pipeline_list", all=True)
        acme = [r for r in q["rows"] if r["company"] == "Acme AI"][0]
        self.assertEqual((acme["state"], acme["notion_url"], acme["submission_row"]), ("已產 CV", "https://www.notion.so/abc", "第 2 列"))
        self.assertTrue(acme["submission_row_url"].startswith("#gid="))
        self.assertEqual(q["queue"], [])

    def test_interest_flow(self):
        out = json.loads(self.push("--interest-append", rows=[{"url": "https://jobs.example.com/i-1", "note": "想試試"},
                                                              {"url": "https://jobs.example.com/i-1"}]))
        self.assertEqual((len(out["added"]), out["skipped"][0]["reason"]), (1, "duplicate"))
        lst = json.loads(self.push("--interest-list"))
        self.assertEqual([(r["url"], r["note"]) for r in lst["rows"]], [("https://jobs.example.com/i-1", "想試試")])
        self.fake.call("interest_update", rows=[{"url": "https://jobs.example.com/i-1", "company": "I Co", "title": "ML Engineer",
                                                  "type": "ML learning", "score": 6, "state": "待產 CV"}])
        lst = self.fake.call("interest_list")
        self.assertEqual((lst["rows"], [r["company"] for r in lst["pending_cv"]]), ([], ["I Co"]))
        bad = self.fake.call("interest_update", rows=[{"url": "https://jobs.example.com/i-1", "state": "待決定"}])
        self.assertEqual(len(bad["rejected"]), 1)

    def test_submissions_follow_tracker_layout(self):
        url = "https://jobs.example.com/sub-1"
        out = json.loads(self.push("--submissions-append", rows=[{
            "date": "2026-11-02", "company": "Sub Co", "title": "AI Engineer", "url": url, "type": "AI agent",
            "source": "AI 找的", "city": "Berlin", "country": "德國", "mode": "hybrid", "score": 8, "ats": 75.8,
            "requirements": ["LLM agents", "Python"], "notion": "https://www.notion.so/row1", "has_cover_letter": True,
            "cv": "https://drive.google.com/file/d/CV1/view", "cover_letter": "https://drive.google.com/file/d/CL1/view",
            "app_dir": "cv/applications/20261102_SubCo_AI-Engineer"}]))
        self.assertEqual(out["added"][0]["row"], 2)
        dup = self.fake.call("submissions_append", rows=[{"url": url + "?x=1", "company": "Sub Co"}])
        self.assertEqual(dup["skipped"][0]["reason"], "duplicate")
        d = self.fake.dump()
        main_head = d["求職追蹤表"]["display"][0]
        sub = d["投遞表單"]
        head = sub["display"][0]
        self.assertEqual(head[:len(main_head)], main_head)  # the candidate's columns, unchanged
        self.assertEqual(head[len(main_head):], ["日期", "連結", "類型", "來源", "城市", "國家", "工作型態", "ATS", "應用資料夾", "Notion", "CV 連結",
                                                 "Cover letter 連結", "內推訊息", "內推資料"])
        row = dict(zip(head, sub["display"][1]))
        self.assertEqual((row["順序"], row["狀態"], row["公司"], row["Position"], row["地點"]),
                         ("1", "準備中", "Sub Co", "AI Engineer", "Berlin, 德國 / Hybrid"))
        self.assertEqual(row["應徵條件"], "AI agent · 評分 8/10\n• LLM agents\n• Python")
        self.assertEqual((row["城市"], row["國家"], row["工作型態"]), ("Berlin", "德國", "Hybrid"))
        self.assertEqual(row["求職過程"], "- 11/02 CV + cover letter 產生 (ATS 75.8)")
        self.assertEqual(row["履歷版本"], "v3 cards · ATS 75.8 + CL")
        f = dict(zip(head, sub["formulas"][1]))
        self.assertEqual(f["履歷版本"], '=HYPERLINK("https://www.notion.so/row1","v3 cards · ATS 75.8 + CL")')
        self.assertEqual(sub["links"][1][head.index("Position")], url)
        v = dict(zip(head, sub["validations"][1]))
        self.assertEqual((v["狀態"], v["內推狀態"]), ("VALUE_IN_LIST", "VALUE_IN_LIST"))
        # dashboard submit: 狀態 + a dated 求職過程 line; NOT appended to 求職追蹤表_AI 投遞
        r = self.fake.call("set_status", rows=[{"url": url, "status": "履歷送出", "added": "2026-11-03"}])
        self.assertEqual((len(r["submissions_updated"]), r["not_found"]), (1, []))
        row = self.fake.table("投遞表單")[0]
        self.assertEqual(row["狀態"], "履歷送出")
        self.assertEqual(row["求職過程"].splitlines()[-1], "- 11/03 Sent (CV dashboard)")
        self.assertEqual(len(self.fake.table("求職追蹤表_AI 投遞")), 4)
        lst = self.fake.call("submissions_list")["rows"][0]
        self.assertEqual((lst["url"], lst["company"], lst["title"], lst["city"], lst["country"], lst["mode"], lst["cv_url"],
                          lst["resume_version_url"], lst["status"], lst["source"], lst["date"]),
                         (url, "Sub Co", "AI Engineer", "Berlin", "德國", "Hybrid", "https://drive.google.com/file/d/CV1/view",
                          "https://www.notion.so/row1", "履歷送出", "AI 找的", "2026-11-02"))

    def test_referrals_update_and_sent(self):
        url = "https://jobs.example.com/ref-1"
        self.fake.call("submissions_append", rows=[{"company": "Ref Co", "title": "ML Engineer", "url": url, "date": "2026-11-02"}])
        people = [{"name": "Ann Lee", "title": "ML Engineer", "url": "https://www.linkedin.com/in/annlee", "why": "同團隊",
                   "full": "Hi Ann,…", "note": "Hi Ann, …"},
                  {"name": "Bo Chen", "title": "Engineering Manager", "url": "https://www.linkedin.com/in/bochen", "why": "主管"}]
        u = self.fake.call("submissions_update", rows=[{"url": url, "referrers": people, "referral_message": "Hi Ann,…",
                                                        "referral_data": people, "process_line": "找到 2 位內推候選", "date": "2026-11-02"}])
        self.assertEqual(len(u["updated"]), 1)
        row = self.fake.table("投遞表單")[0]
        self.assertEqual(row["內推人"], "Ann Lee (ML Engineer) — https://www.linkedin.com/in/annlee\n"
                                       "Bo Chen (Engineering Manager) — https://www.linkedin.com/in/bochen")
        self.assertEqual(row["內推訊息"], "Hi Ann,…")
        self.assertEqual(self.fake.call("submissions_list")["rows"][0]["referrals"][0]["name"], "Ann Lee")
        s = self.fake.call("referral_sent", rows=[{"url": url, "name": "Ann Lee", "date": "2026-11-04"}])
        self.assertEqual(len(s["updated"]), 1)
        row = self.fake.table("投遞表單")[0]
        self.assertEqual(row["內推狀態"], "⚠️等待回覆")
        self.assertEqual(row["求職過程"].splitlines()[-1], "- 11/04 Sent referral request to Ann Lee")

    def test_set_status_cli_accepts_live_flag(self):
        url = "https://jobs.example.com/live-1"
        self.fake.call("submissions_append", rows=[{"company": "Live Co", "title": "AI Engineer", "url": url, "date": "2026-11-02"}])
        for args in (("--set-status",), ("--live", "--set-status")):
            out = self.push(*args, rows=[{"url": url, "status": "履歷送出"}]) if args[0] == "--set-status" else None
            if out is None:
                f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
                json.dump([{"url": url, "status": "第一關面試"}], f, ensure_ascii=False)
                f.close()
                p = subprocess.run([sys.executable, "-I", str(PUSH), "--set-status", f.name, "--live"], env=self.fake.env,
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stderr)
                out = p.stdout
            self.assertRegex(out, r"^updated=0 added=0 skipped\(duplicate\)=0 rejected=0 submissions_updated=1")
        self.assertEqual(self.fake.table("投遞表單")[0]["狀態"], "第一關面試")

    def test_unknown_action_is_refused(self):
        r = self.fake.call("pipeline_frobnicate", rows=[{"url": "https://x.example/1", "company": "X", "title": "Y"}])
        self.assertEqual(r, {"ok": False, "error": "unknown action: pipeline_frobnicate"})
        self.assertEqual(len(self.fake.table("求職追蹤表_AI 投遞")), 4)


class Transport(unittest.TestCase):
    def test_http_retries_429_then_succeeds(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        hits = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.headers.get("Authorization"))
                code = 429 if len(hits) < 3 else 200
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b'{"ok": 1}' if code == 200 else b'{"error": "quota"}')

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        sys.path.insert(0, str(SCRIPTS))
        import google_auth
        waits = []
        http = google_auth.Http(token="t", sleep=waits.append)
        self.assertEqual(http.request("GET", f"http://127.0.0.1:{srv.server_address[1]}/x"), {"ok": 1})
        srv.shutdown()
        self.assertEqual((hits, waits), (["Bearer t"] * 3, [1, 2]))

    def test_missing_google_login_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "sheet.json"
            cfg.write_text(json.dumps({"api_base": "http://127.0.0.1:9"}))  # no access_token → real OAuth path
            env = {**os.environ, "AI_JOB_TRACKER_SHEET_CONFIG": str(cfg), "AI_JOB_TRACKER_CONF_DIR": d}
            p = subprocess.run([sys.executable, "-I", str(PUSH), "--pipeline-list"], env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue("google_auth.py --auth" in p.stderr or "python3-google-auth-oauthlib" in p.stderr, p.stderr)


if __name__ == "__main__":
    unittest.main()
