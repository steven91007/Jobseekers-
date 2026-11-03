"""score_jobs.py with a mock codex (no model calls)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fakesheet import SCRIPTS

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import score_jobs as sj  # noqa: E402

JD = (HERE / "fixtures" / "jd_agent_engineer.txt").read_text(encoding="utf-8")


class ScoreJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "scores.json"
        self.log = Path(self.tmp.name) / "codex.log"
        self.env = mock.patch.dict(os.environ, {"CODEX_BIN": str(HERE / "fake_codex.py"), "FAKE_CODEX_LOG": str(self.log)})
        self.env.start()
        self.profile = mock.patch.object(sj, "build_profile", return_value="DT1 deliverytrack LangGraph agent …")
        self.profile.start()

    def tearDown(self):
        self.env.stop()
        self.profile.stop()
        self.tmp.cleanup()

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_(self, rows, **kw):
        return sj.score_jobs(rows, cache_path=self.cache, fetch=lambda u: None, log=lambda m: None, **kw)

    def test_fixture_jd_scores_and_caches(self):
        rows = [{"company": "Fixture Labs", "title": "AI Agent Engineer", "url": "https://jobs.fixture.example/agent?utm=1", "jd": JD}]
        r = self.run_(rows)[0]
        self.assertEqual((r["type"], r["score"], r["band"], r["location"]["mode"], r["min_years"]), ("AI agent", 9, "apply", "Hybrid", 4))
        self.assertEqual(len(r["reasons"]), 2)
        self.assertEqual(self.calls(), [{"n_jobs": 1, "has_profile": True, "has_cards": True}])
        again = self.run_([{**rows[0], "url": "https://jobs.fixture.example/agent"}])[0]
        self.assertTrue(again["cached"])
        self.assertEqual(len(self.calls()), 1)
        changed = self.run_([{**rows[0], "jd": JD + "\nNew: Kubernetes required."}])[0]
        self.assertNotIn("cached", changed)
        self.assertEqual(len(self.calls()), 2)

    def test_batches_of_five_and_caps(self):
        titles = ["AI Engineer", "Staff AI Engineer", "KI Entwickler Deutsch", "Computer Vision Engineer", "Skip me",
                  "Agent Dev", "Backend"]
        rows = [{"company": f"C{i}", "title": t, "url": f"https://jobs.example.com/{i}", "jd": JD} for i, t in enumerate(titles)]
        res = self.run_(rows)
        self.assertEqual([c["n_jobs"] for c in self.calls()], [5, 2])
        self.assertEqual(res[1]["score"], 4)
        self.assertIn("職級或年資超出條件", res[1]["reasons"][-1])
        self.assertEqual((res[2]["score"], res[2]["reasons"][-1]), (3, "要求德語"))
        self.assertEqual((res[3]["type"], res[3]["band"], res[3]["location"]["country"]), ("ML learning", "maybe", "荷蘭"))
        self.assertIn("評分失敗", res[4]["error"])
        self.assertNotIn(sj.url_key(rows[4]["url"]), json.loads(self.cache.read_text()))

    def test_missing_jd_and_url(self):
        res = self.run_([{"title": "x", "url": "https://www.linkedin.com/jobs/view/123"}, {"title": "no url", "url": ""}])
        self.assertIn("JD 無法取得", res[0]["error"])
        self.assertEqual(res[1]["error"], "沒有職缺連結")
        self.assertEqual(self.calls(), [])
        self.assertIsNone(sj.fetch_jd("https://de.linkedin.com/jobs/view/123"))

    def test_failed_codex_batch_does_not_raise(self):
        with mock.patch.dict(os.environ, {"FAKE_CODEX_FAIL": "1"}):
            res = self.run_([{"title": "AI Engineer", "url": "https://jobs.example.com/f", "jd": JD}])
        self.assertIn("評分失敗", res[0]["error"])

    def test_dry_run_never_calls_codex_and_is_not_reused(self):
        rows = [{"title": "LLM Agent Engineer", "url": "https://jobs.example.com/d", "jd": JD}]
        r = self.run_(rows, dry_run=True)[0]
        self.assertEqual((r["source"], r["type"]), ("dry-run", "AI agent"))
        self.assertTrue(1 <= r["score"] <= 10)
        self.assertEqual(self.calls(), [])
        real = self.run_(rows)[0]  # a dry-run cache entry never stands in for a real score
        self.assertEqual(real["source"], "codex")

    def test_url_key_matches_apps_script(self):
        self.assertEqual(sj.url_key("https://de.linkedin.com/jobs/view/ai-engineer-at-x-4012345678/?trk=1"), "li:4012345678")
        self.assertEqual(sj.url_key("https://www.Example.com/jobs/1/?a=b#x"), "example.com/jobs/1")

    def test_html_to_text(self):
        t = sj.html_to_text("<html><head><title>t</title></head><body><script>x()</script><h1>AI Engineer</h1><p>Python &amp; LLM</p></body></html>")
        self.assertEqual(t, "AI Engineer\nPython & LLM")


if __name__ == "__main__":
    unittest.main()
