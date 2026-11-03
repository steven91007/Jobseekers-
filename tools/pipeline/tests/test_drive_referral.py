"""drive_upload.py (fake Drive) and referral.py (template, note, candidate parsing from a fixed search fixture)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from fakesheet import SCRIPTS
from fake_drive import FakeDrive

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import drive_upload  # noqa: E402
import referral  # noqa: E402

EXPECTED_FULL = """Hi Ann,
My name is Amo Hsu and I am a Senior AI Engineer at Delta Electronics.

The reason why I'm writing to you is I saw you are working at Scout24 as a Senior ML Engineer. I'm very interested in joining Scout24 myself as well. So I was wondering if you would be so kind to refer my candidacy.

If you don't feel comfortable doing that, it's also totally understandable. If you want to know more about me, here is my CV.
https://drive.google.com/file/d/F3/view

I'm also happy to schedule a chat with you so you can get to know me more. As well as learning how AI Automation Engineer work at Scout24.

Thank you for your time. I hope to hear back from you soon.

Best regards,
Amo Hsu"""


class Drive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cv = t / "CV_ChiYiHsu_Acme.pdf"
        self.cv.write_bytes(b"%PDF v1")
        self.fake = FakeDrive()
        self.drive = drive_upload.Drive(token="tok", http=self.fake, files_path=t / "files.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload_creates_folders_shares_and_reupload_keeps_link(self):
        link = self.drive.upload("Acme", self.cv)
        f = self.fake.files
        folders = {v["name"]: k for k, v in f.items() if v["mime"] == "application/vnd.google-apps.folder"}
        self.assertEqual(set(folders), {"CV for referrals", "Acme"})
        self.assertEqual(f[folders["Acme"]]["parents"], [folders["CV for referrals"]])
        pdf_id = next(k for k, v in f.items() if v["name"] == self.cv.name)
        self.assertTrue(f[pdf_id]["anyone"])
        self.assertEqual(f[pdf_id]["parents"], [folders["Acme"]])
        self.cv.write_bytes(b"%PDF v2")
        again = drive_upload.Drive(token="tok", http=self.fake, files_path=Path(self.tmp.name) / "files.json").upload("Acme", self.cv)
        self.assertEqual(again, link)
        self.assertEqual(f[pdf_id]["data"], b"%PDF v2")
        self.assertEqual(sum(1 for v in f.values() if v["name"] == self.cv.name), 1)

    def test_lost_mapping_still_replaces_by_name(self):
        link = self.drive.upload("Acme", self.cv)
        fresh = drive_upload.Drive(token="tok", http=self.fake, files_path=Path(self.tmp.name) / "other.json")
        self.assertEqual(fresh.upload("Acme", self.cv), link)

    def test_backfill_plan_picks_v2_letters(self):
        repo = Path(self.tmp.name) / "repo"
        for d in ("cv/applications/20261024_Scout24_X/v3_cards/CV_ChiYiHsu_Scout24.pdf",
                  "cv/applications/20261023_TOMRA_Y/v3_cards/CV_ChiYiHsu_TOMRA.pdf",
                  "cv/cover_letters/20261025_Ixopay_Z/CoverLetter_AmoHsu_Ixopay.pdf",
                  "cv/cover_letters/20261025_Ixopay_Z_v2/CoverLetter_AmoHsu_Ixopay.pdf",
                  "cv/cover_letters/20261031_Scout24_X/CoverLetter_AmoHsu_Scout24.pdf"):
            (repo / d).parent.mkdir(parents=True, exist_ok=True)
            (repo / d).write_bytes(b"%PDF")
        plan = drive_upload.backfill_plan(repo)
        self.assertEqual(plan["Ixopay"], {"cover_letter": "cv/cover_letters/20261025_Ixopay_Z_v2/CoverLetter_AmoHsu_Ixopay.pdf"})
        self.assertEqual(set(plan["Scout24"]), {"cv", "cover_letter"})
        self.assertEqual(set(plan["TOMRA"]), {"cv"})

    def test_real_repo_backfill_covers_the_existing_pdfs(self):
        plan = drive_upload.backfill_plan()
        if not plan:
            self.skipTest("no cv/ folder on this machine")
        self.assertTrue({"Scout24", "TOMRA", "Ixopay", "AIVenture", "Taktile", "Mapbox"} <= {c for c, p in plan.items() if "cv" in p})
        self.assertTrue(all(p["cover_letter"].count("_v2/") or "Scout24" in p["cover_letter"]
                            for c, p in plan.items() if "cover_letter" in p and c != "HelloFresh"))


class Referral(unittest.TestCase):
    def test_full_message_is_the_template_verbatim(self):
        p = {"name": "Ann Lee", "title": "Senior ML Engineer"}
        self.assertEqual(referral.full_message(p, "Scout24", "AI Automation Engineer", "https://drive.google.com/file/d/F3/view"),
                         EXPECTED_FULL)

    def test_connect_note_fits_280_and_names_role_and_referral(self):
        p = {"name": "Ann Lee", "title": "x"}
        for title in ("AI Automation Engineer", "Senior Machine Learning Platform Engineer, Generative AI Agents and Retrieval "
                                                "Infrastructure (m/w/d) Berlin Munich Hamburg Frankfurt Cologne Stuttgart"):
            n = referral.connect_note(p, "Scout24", title)
            self.assertLessEqual(len(n), 280)
            self.assertIn("Scout24", n)
            self.assertIn("refer", n)
            self.assertIn("connect", n)
        self.assertIn("AI Automation Engineer role", referral.connect_note(p, "Scout24", "AI Automation Engineer"))

    def test_parse_candidates_from_search_fixture(self):
        obj = json.loads((HERE / "fixtures" / "referral_search.json").read_text(encoding="utf-8"))
        got = referral.parse_candidates(obj)
        self.assertEqual([(c["name"], c["group"]) for c in got],
                         [("Ann Lee", "team"), ("Ben Wu", "team"), ("Clara Meyer", "manager"), ("Chen Yi-Ting", "taiwanese")])
        self.assertEqual(got[0]["url"], "https://www.linkedin.com/in/ann-lee-ml")
        names = {c["name"] for c in got}
        self.assertNotIn("Invented Person", names)  # url not in the search results
        self.assertNotIn("Company Page", names)  # not a /in/ profile

    def test_messages_and_notion_markdown(self):
        people = referral.with_messages([{"name": "Ann Lee", "title": "ML Engineer", "url": "u", "why": "同團隊", "group": "team"}],
                                        "Scout24", "AI Automation Engineer", "https://drive/x")
        md = referral.notion_markdown(people)
        self.assertTrue(md.startswith("## 內推訊息"))
        self.assertIn("### 1. Ann Lee — ML Engineer(推薦)", md)
        self.assertIn("https://drive/x", md)
        self.assertIn("公開搜尋沒有找到", referral.notion_markdown([]))

    def test_search_prompt_forbids_linkedin_fetching(self):
        pr = referral.search_prompt("Scout24", "AI Automation Engineer", "Berlin")
        self.assertIn("Use ONLY the WebSearch tool", pr)
        self.assertIn('site:linkedin.com/in "Scout24" Taiwan', pr)


if __name__ == "__main__":
    unittest.main()
