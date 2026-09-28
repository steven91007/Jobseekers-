"""Source adapters against canned API payloads (no network)."""

from jobagent.companies import Company
from jobagent.sources import ashby, greenhouse, html_to_text, lever
from jobagent.sources import linkedin as li_source

GREENHOUSE = {"jobs": [
    {"id": 1, "title": "Machine Learning Engineer", "location": {"name": "Berlin, Germany"},
     "absolute_url": "https://gh/1", "first_published": "2026-09-20T10:00:00-04:00",
     "updated_at": "2026-09-21T10:00:00-04:00"},
    {"id": 2, "title": "Account Executive", "location": {"name": "Berlin"},
     "absolute_url": "https://gh/2", "first_published": "2026-09-20T10:00:00-04:00"},
    {"id": 3, "title": "ML Engineer", "location": {"name": "San Francisco, CA"},
     "absolute_url": "https://gh/3", "first_published": "2026-09-20T10:00:00-04:00"},
    {"id": 4, "title": "LLM Engineer", "location": {"name": "Dublin, IE"},
     "absolute_url": "https://gh/4", "first_published": "2026-01-02T10:00:00-05:00"},
]}

ASHBY = {"jobs": [
    {"id": "a1", "title": "Software Engineer, Agents", "location": "London",
     "secondaryLocations": [{"location": "Amsterdam"}], "publishedAt": "2026-09-22T08:00:00+00:00",
     "jobUrl": "https://ashby/a1", "descriptionPlain": "Build agents.", "isListed": True,
     "workplaceType": "Hybrid", "compensation": {"compensationTierSummary": "€90K – €120K"}},
    {"id": "a2", "title": "AI Engineer", "location": "Munich", "isListed": False,
     "publishedAt": "2026-09-22T08:00:00+00:00", "jobUrl": "https://ashby/a2"},
]}

LEVER = [
    {"id": "l1", "text": "MLOps Engineer", "categories": {"location": "Remote - Europe", "allLocations": ["Remote - Europe"]},
     "country": None, "createdAt": 1790000000000, "hostedUrl": "https://lever/l1",
     "descriptionPlain": "Pipelines.", "lists": [{"text": "Requirements", "content": "<li>Python</li><li>K8s</li>"}]},
]


def test_greenhouse_filters_region_title_and_cutoff(monkeypatch):
    monkeypatch.setattr(greenhouse, "http_get_json", lambda url, params=None: GREENHOUSE)
    company = Company("Acme AI", "greenhouse", "acme", "ai_native")

    res = greenhouse.collect(company, cutoff="")
    assert [j.source_id for j in res.jobs] == ["1", "4"]
    assert res.jobs[0].region == "DE" and res.jobs[1].region == "IE"
    assert res.raw_count == 4

    recent = greenhouse.collect(company, cutoff="2026-09-01")
    assert [j.source_id for j in recent.jobs] == ["1"]


def test_ashby_uses_secondary_locations_and_skips_unlisted(monkeypatch):
    monkeypatch.setattr(ashby, "http_get_json", lambda url, params=None: ASHBY)
    res = ashby.collect(Company("Acme", "ashby", "acme", "ai_native"), cutoff="")
    assert [j.source_id for j in res.jobs] == ["a1"]
    job = res.jobs[0]
    assert job.region == "NL"
    assert job.description == "Build agents."
    assert job.extra["salary"] == "€90K – €120K"


def test_lever_remote_eu_and_description(monkeypatch):
    monkeypatch.setattr(lever, "http_get_json", lambda url, params=None: LEVER)
    res = lever.collect(Company("Acme", "lever", "acme", "ai_heavy"), cutoff="")
    assert len(res.jobs) == 1 and res.jobs[0].region == "REMOTE_EU"
    assert "Python" in res.jobs[0].description and "Requirements" in res.jobs[0].description


def test_source_error_is_reported_not_raised(monkeypatch):
    def boom(url, params=None):
        raise ConnectionError("down")

    monkeypatch.setattr(greenhouse, "http_get_json", boom)
    res = greenhouse.collect(Company("Acme", "greenhouse", "acme", "ai_native"), cutoff="")
    assert res.outcome == "ERROR" and not res.ok and "down" in res.detail


def test_linkedin_adapter_maps_regions(monkeypatch):
    from linkedin_scraper import Outcome, ScrapeResult

    raw = [
        {"job_id": "11", "title": "AI Engineer", "company": "Fin", "location": "Dublin, County Dublin, Ireland",
         "work_type": "N/A", "posted_date": "2026-09-23", "url": "https://li/11"},
        {"job_id": "12", "title": "AI Engineer", "company": "X", "location": "Cork, County Cork, Ireland",
         "work_type": "N/A", "posted_date": "2026-09-23", "url": "https://li/12"},
        {"job_id": "13", "title": "Sales Manager AI", "company": "Y", "location": "Dublin",
         "work_type": "N/A", "posted_date": "2026-09-23", "url": "https://li/13"},
        {"job_id": "14", "title": "LLM Engineer", "company": "Z", "location": "European Union",
         "work_type": "Remote", "posted_date": "2026-09-22", "url": "https://li/14"},
    ]
    monkeypatch.setattr(li_source.linkedin_scraper, "search_jobs_strict",
                        lambda **kw: ScrapeResult(jobs=raw, outcome=Outcome.OK))
    res = li_source.collect("IE", "AI Engineer")
    got = {j.source_id: (j.region, j.company_tier) for j in res.jobs}
    assert got == {"11": ("IE", "ai_native"), "14": ("REMOTE_EU", "other")}


def test_html_to_text():
    assert html_to_text("<p>Hello</p><ul><li>A</li><li>B</li></ul>").splitlines() == ["Hello", "A", "B"]
