"""Workable, the German federal Jobsuche, Arbeitnow and Common Crawl discovery, offline."""

import gzip
from datetime import datetime, timedelta, timezone

import pytest

from jobagent import discover, store
from jobagent.companies import Company
from jobagent.normalize import is_ai_engineer_title, is_relevant_title
from jobagent.sources import arbeitnow, arbeitsagentur, detect, workable


def test_workable_regions_remote_titles_and_filters(monkeypatch):
    data = {"jobs": [
        {"title": "AI Engineer", "shortcode": "A1", "city": "Utrecht", "state": "Utrecht", "country": "Netherlands",
         "published_on": "2026-09-30", "url": "https://apply.workable.com/j/A1", "description": "<p>LLM apps</p>"},
        {"title": "Senior ML Engineer, Voice - EMEA Remote", "shortcode": "A2", "city": "Paris", "country": "France",
         "telecommuting": True, "published_on": "2026-09-30", "url": "https://apply.workable.com/j/A2"},
        {"title": "ML Engineer", "shortcode": "A3", "city": "Paris", "country": "France",
         "published_on": "2026-09-30", "url": "https://apply.workable.com/j/A3"},
        {"title": "Account Executive", "shortcode": "A4", "city": "Berlin", "country": "Germany",
         "published_on": "2026-09-30", "url": "x"},
    ]}
    seen = {}
    monkeypatch.setattr(workable, "http_get_json", lambda url, params=None: seen.setdefault("url", url) and data)
    res = workable.collect(Company("Acme", "workable", "acme", "ai_native"), cutoff="")
    assert seen["url"].endswith("/widget/accounts/acme")
    assert [(j.source_id, j.region) for j in res.jobs] == [("acme-A1", "NL"), ("acme-A2", "REMOTE_EU")]
    assert res.jobs[0].description == "LLM apps" and res.raw_count == 4


def test_detect_finds_workable_board():
    found = detect.candidates('<a href="https://apply.workable.com/acme-ai/j/ABC123/">Jobs</a>', "https://acme.ai",
                              "Acme", "ai_native")
    assert [(c.ats, c.slug) for c in found] == [("workable", "acme-ai")]


BA_PAGE = {"maxErgebnisse": 3, "ergebnisliste": [
    {"stellenangebotsTitel": "AI Engineer / KI-Entwicklerin (m/w/d)", "firma": "swb AG", "referenznummer": "1-2-S",
     "datumErsteVeroeffentlichung": "2026-09-30", "homeofficemoeglich": True,
     "stellenlokationen": [{"adresse": {"ort": "Bremen", "land": "DEUTSCHLAND"}}]},
    {"stellenangebotsTitel": "Sachbearbeiter Einkauf (m/w/d)", "firma": "X", "referenznummer": "3-4-S"},
    {"stellenangebotsTitel": "KI-Entwickler (m/w/d)", "firma": "Y", "referenznummer": "5-6-S",
     "datumErsteVeroeffentlichung": "2026-09-29", "externeURL": "https://y.de/job"},
]}


def test_arbeitsagentur_search_and_detail(monkeypatch):
    calls = []

    def fake_get(url, params=None):
        calls.append((url, params))
        if "jobdetails" in url:
            return {"stellenangebotsBeschreibung": "## Aufgaben\n- RAG"}
        return BA_PAGE

    monkeypatch.setattr(arbeitsagentur, "_get", fake_get)
    res = arbeitsagentur.collect("AI Engineer", since="24h")
    assert calls[0][1]["veroeffentlichtseit"] == 1 and calls[0][1]["was"] == "AI Engineer"
    assert [j.source_id for j in res.jobs] == ["1-2-S", "5-6-S"]
    job = res.jobs[0]
    assert job.region == "DE" and job.location == "Bremen, Germany" and job.work_type == "hybrid"
    assert job.url == "https://www.arbeitsagentur.de/jobsuche/jobdetail/1-2-S"
    assert res.jobs[1].extra["external_url"] == "https://y.de/job"
    assert arbeitsagentur.fetch_description(job) == "## Aufgaben\n- RAG"
    assert calls[-1][0].endswith("/jobdetails/MS0yLVM=")  # base64 of the reference number


def _ts(days_ago: float) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())


def test_arbeitnow_pages_until_older_than_cutoff(monkeypatch):
    pages = {
        1: {"data": [
            {"slug": "old-pinned-ad", "title": "AI Engineer", "company_name": "Ad", "location": "Berlin",
             "created_at": _ts(40), "url": "u0"},
            {"slug": "ai-eng-1", "title": "Senior AI Engineer (f/m/d)", "company_name": "Vestigas",
             "location": "München (Hybrid)", "created_at": _ts(0.2), "url": "u1", "description": "<p>Agents</p>"},
            {"slug": "sales-1", "title": "Sales Manager", "company_name": "S", "location": "Berlin",
             "created_at": _ts(0.3), "url": "u2"},
            {"slug": "ml-london", "title": "ML Engineer", "company_name": "L", "location": "London",
             "created_at": _ts(0.3), "url": "u3"},
        ], "links": {"next": "p2"}},
        2: {"data": [{"slug": "ki-1", "title": "KI-Entwickler (m/w/d)", "company_name": "K", "location": "Amsterdam",
                      "created_at": _ts(3), "url": "u4"}], "links": {"next": "p3"}},
        3: {"data": [{"slug": "ml-old", "title": "ML Engineer", "company_name": "O", "location": "Berlin",
                      "created_at": _ts(9), "url": "u5"}], "links": {"next": "p4"}},
    }
    asked = []
    monkeypatch.setattr(arbeitnow, "http_get_json",
                        lambda url, params=None: asked.append(params["page"]) or pages[params["page"]])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    res = arbeitnow.collect(cutoff_iso=cutoff, regions=["DE", "NL"], pause=0)
    assert asked == [1, 2, 3]  # page 3 is entirely too old, so paging stops there
    assert [(j.source_id, j.region) for j in res.jobs] == [("ai-eng-1", "DE"), ("ki-1", "NL")]
    assert res.jobs[0].description == "Agents" and res.outcome == "OK"


def test_arbeitnow_keeps_partial_results_when_rate_limited(monkeypatch):
    from jobagent.sources import RateLimitedError

    def fake(url, params=None):
        if params["page"] == 1:
            return {"data": [{"slug": "a", "title": "AI Engineer", "company_name": "A", "location": "Berlin",
                              "created_at": _ts(0.1), "url": "u"}], "links": {"next": "p2"}}
        raise RateLimitedError("HTTP 429")

    monkeypatch.setattr(arbeitnow, "http_get_json", fake)
    res = arbeitnow.collect(cutoff_iso="", pause=0)
    assert res.outcome == "OK" and len(res.jobs) == 1 and res.detail.startswith("partial: RATE_LIMITED")


@pytest.mark.parametrize("title, relevant, focus", [
    ("KI-Entwickler (m/w/d)", True, True),
    ("KI Engineer", True, True),
    ("Senior Entwickler Java (m/w/d)", False, False),
    ("Kiosk Manager", False, False),
])
def test_german_titles(title, relevant, focus):
    assert is_relevant_title(title, "other") is relevant
    assert is_ai_engineer_title(title) is focus


# --- Common Crawl ---------------------------------------------------------------------


class FakeCrawl:
    """A tiny cluster.idx plus gzipped index blocks, served through HTTP range requests."""

    def __init__(self, blocks: list[list[str]]):
        self.files: dict[str, bytes] = {}
        shard, idx_lines = b"", []
        for lines in blocks:
            gz = gzip.compress(("\n".join(lines) + "\n").encode())
            idx_lines.append(f"{lines[0]}\tcdx-00000.gz\t{len(shard)}\t{len(gz)}\t{len(idx_lines) + 1}")
            shard += gz
        # pad the index so the binary search really has to search
        filler = [f"aa,filler{i:05d})/ 2026\tcdx-00000.gz\t0\t1\t0" for i in range(3000)]
        self.files["cluster.idx"] = ("\n".join(filler + idx_lines) + "\n").encode()
        self.files["cdx-00000.gz"] = shard
        self.headers = {}

    def _resp(self, status, content=b"", headers=None):
        from types import SimpleNamespace
        return SimpleNamespace(status_code=status, content=content, headers=headers or {},
                               json=lambda: [{"id": "CC-TEST"}])

    def head(self, url, timeout=None):
        return self._resp(200, headers={"content-length": str(len(self.files["cluster.idx"]))})

    def get(self, url, headers=None, timeout=None):
        if url.endswith("collinfo.json"):
            return self._resp(200)
        data = self.files[url.rsplit("/", 1)[1]]
        start, end = (int(x) for x in headers["Range"].split("=")[1].split("-"))
        return self._resp(206, data[start:end + 1])


def test_crawl_slugs_reads_only_the_host_blocks():
    crawl = FakeCrawl([
        ["com,ashbyhq,jobs)/zzz 2026 {}", "com,example)/ 2026 {}"],
        ["com,workable,apply)/acme-ai/j/123 2026 {}", "com,workable,apply)/acme-ai/ 2026 {}",
         "com,workable,apply)/beta 2026 {}", "com,workable,apply)/123456 2026 {}",
         "com,workable,apply)/j/abc 2026 {}"],
        ["com,workable,apply)/gamma/ 2026 {}", "com,zeta)/ 2026 {}"],
    ])
    assert discover.crawl_slugs("workable", session=crawl) == {"acme-ai", "beta", "gamma"}


def test_prioritize_skips_known_and_prefers_ai_names():
    order = discover.prioritize({"plumbing-co", "deepmind-labs", "acme", "known", "x-ai"}, {"known"}, 3)
    assert order == ["x-ai", "deepmind-labs", "acme"]


def test_discovery_run_records_checks_and_candidates(tmp_path, monkeypatch):
    from jobagent.models import Job
    from jobagent.sources import SourceResult

    conn = store.connect(tmp_path / "d.db")
    monkeypatch.setattr(discover, "crawl_slugs", lambda ats, **kw: {"good-ai", "empty-co", "huggingface"})

    def fake_verify(ats, slugs, workers=8):
        out = []
        for slug in slugs:
            jobs = [Job("workable", "1", "AI Engineer", slug, "Berlin", "DE", "u")] if slug == "good-ai" else []
            out.append(discover.Found(Company(slug, ats, slug, "ai_heavy"),
                                      SourceResult("workable", slug, jobs=jobs, raw_count=len(jobs) + 2)))
        return out

    monkeypatch.setattr(discover, "verify", fake_verify)
    found = discover.run(conn, ["workable"], limit=10, progress=lambda m: None)
    assert [f.company.slug for f in found] == ["good-ai"]  # huggingface is already on the watchlist
    assert store.checked_slugs(conn, "workable") == {"good-ai", "empty-co"}
    cands = store.list_candidates(conn)
    assert [(c["slug"], c["regions"], c["careers_url"]) for c in cands] == [
        ("good-ai", "DE", "https://apply.workable.com/good-ai")]
    # a second run has nothing left to check
    assert discover.run(conn, ["workable"], limit=10, progress=lambda m: None) == []


def test_arbeitsagentur_retries_timeouts(monkeypatch):
    import requests

    from types import SimpleNamespace

    attempts = []

    class Flaky:
        headers = {}

        def get(self, url, params=None, timeout=None):
            attempts.append(1)
            if len(attempts) < 3:
                raise requests.ReadTimeout("slow")
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: BA_PAGE)

    monkeypatch.setattr(arbeitsagentur, "BACKOFF", (0, 0))
    monkeypatch.setattr(arbeitsagentur, "_session", lambda: Flaky())
    res = arbeitsagentur.collect("AI Engineer", since="7d")
    assert len(attempts) == 3 and res.outcome == "OK" and len(res.jobs) == 2


def test_latest_collection_falls_back_to_probing_the_data_server(monkeypatch):
    import requests

    from types import SimpleNamespace

    monkeypatch.setattr(discover.time, "sleep", lambda s: None)
    year, week, _ = (discover.date.today() - discover.timedelta(weeks=2)).isocalendar()
    want = f"CC-MAIN-{year}-{week:02d}"

    class Down:
        def get(self, url, timeout=None):
            raise requests.ConnectionError("index server down")

        def head(self, url, timeout=None):
            return SimpleNamespace(status_code=200 if want in url else 404)

    assert discover.latest_collection(Down()) == want


def test_arbeitnow_unescapes_double_escaped_html():
    assert arbeitnow._text("&lt;p data-pos=&quot;0&quot;&gt;At JetBrains&lt;/p&gt;") == "At JetBrains"
    assert arbeitnow._text("<p>Plain &amp; simple</p>") == "Plain & simple"
