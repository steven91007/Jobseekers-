"""New board adapters, JSON-LD parsing and board detection, against canned payloads."""

import json
from datetime import date

from jobagent.companies import Company
from jobagent.sources import (detect, jsonld, personio, recruitee, smartrecruiters, teamtailor,
                              workday)

PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
<position><id>11</id><office>Munich</office><additionalOffices><office>Berlin</office></additionalOffices>
 <name>Machine Learning Engineer</name>
 <jobDescriptions><jobDescription><name>Your tasks</name><value><![CDATA[<p>Train models</p><ul><li>PyTorch</li></ul>]]></value></jobDescription></jobDescriptions>
 <schedule>full-time</schedule><seniority>experienced</seniority><createdAt>2026-09-20T10:00:00+00:00</createdAt></position>
<position><id>12</id><office>London</office><name>ML Engineer</name><createdAt>2026-09-20T10:00:00+00:00</createdAt></position>
<position><id>13</id><office>Berlin</office><name>Office Manager</name><createdAt>2026-09-20T10:00:00+00:00</createdAt></position>
</workzag-jobs>"""

TEAMTAILOR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>
<item><title>AI Engineer</title><description>&lt;p&gt;Build agents&lt;/p&gt;</description>
 <pubDate>Mon, 21 Sep 2026 08:58:57 +0200</pubDate><link>https://careers.acme.ai/jobs/1-ai-engineer</link>
 <remoteStatus>hybrid</remoteStatus><guid>g1</guid>
 <tt:locations><tt:location><tt:name>Amsterdam</tt:name><tt:city>Amsterdam</tt:city><tt:country>Netherlands</tt:country></tt:location></tt:locations></item>
<item><title>Account Executive</title><pubDate>Mon, 21 Sep 2026 08:58:57 +0200</pubDate><link>x</link><guid>g2</guid>
 <tt:locations><tt:location><tt:city>Amsterdam</tt:city><tt:country>Netherlands</tt:country></tt:location></tt:locations></item>
</channel></rss>"""

JSONLD_PAGE = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Acme"},
 {"@type":"JobPosting","title":"Senior LLM Engineer","datePosted":"2026-09-22","url":"https://acme.ai/jobs/42",
  "identifier":{"@type":"PropertyValue","value":"42"},"description":"&lt;p&gt;RAG &amp;amp; agents&lt;/p&gt;",
  "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Dublin","addressCountry":"IE"}},
  "baseSalary":{"@type":"MonetaryAmount","currency":"EUR","value":{"minValue":90000,"maxValue":120000,"unitText":"YEAR"}}}]}
</script></head><body></body></html>"""

LISTING_PAGE = """<html><body>
<a href="/jobs/ml-engineer-berlin">Machine Learning Engineer</a>
<a href="/jobs/sales">Account Executive</a>
<a href="https://other.com/jobs/ai">AI Engineer elsewhere</a>
<a href="/about">About us</a>
<iframe src="https://boards.greenhouse.io/embed/job_board?for=acmeai"></iframe>
</body></html>"""


def test_personio(monkeypatch):
    monkeypatch.setattr(personio, "http_get_text", lambda url, params=None, browser=True: (PERSONIO_XML, url))
    res = personio.collect(Company("Acme", "personio", "acme", "ai_native"), cutoff="")
    assert [j.source_id for j in res.jobs] == ["acme-11"]
    job = res.jobs[0]
    assert job.region == "DE" and job.url == "https://acme.jobs.personio.de/job/11"
    assert "Train models" in job.description and "PyTorch" in job.description
    assert res.raw_count == 3


def test_recruitee(monkeypatch):
    data = {"offers": [
        {"id": 5, "title": "Python Software Engineer - AI team", "status": "published", "city": "Utrecht",
         "country": "Netherlands", "location": "Utrecht, Netherlands", "locations": [],
         "published_at": "2026-09-18 13:27:28 UTC", "careers_url": "https://jobs.acme.nl/o/py",
         "description": "<p>Python</p>", "requirements": "<p>5 years</p>", "hybrid": True,
         "salary": {"min": "60000", "max": "80000", "currency": "EUR", "period": "year"}},
        {"id": 6, "title": "Customer Success Manager", "status": "published", "location": "Utrecht, Netherlands"},
    ]}
    monkeypatch.setattr(recruitee, "http_get_json", lambda url, params=None: data)
    res = recruitee.collect(Company("Acme", "recruitee", "acme", "ai_native"), cutoff="")
    assert len(res.jobs) == 1
    job = res.jobs[0]
    assert job.region == "NL" and job.posted_at == "2026-09-18T13:27:28+00:00"
    assert job.work_type == "hybrid" and "5 years" in job.description
    assert job.extra["salary"].startswith("60000-80000 EUR")


def test_smartrecruiters_filters_by_country_and_fetches_detail(monkeypatch):
    calls = []

    def fake(url, params=None):
        calls.append((url, params))
        if "/postings/" in url:
            return {"postingUrl": "https://jobs.smartrecruiters.com/Acme/1-ml",
                    "jobAd": {"sections": {"jobDescription": {"title": "Job", "text": "<p>Build ML</p>"}}}}
        if params["country"] == "de":
            return {"totalFound": 1, "content": [{"id": "1", "name": "Senior Data Scientist",
                                                  "releasedDate": "2026-09-20T10:00:00.000Z",
                                                  "location": {"city": "Berlin", "country": "de"}}]}
        return {"totalFound": 0, "content": []}

    monkeypatch.setattr(smartrecruiters, "http_get_json", fake)
    res = smartrecruiters.collect(Company("Acme", "smartrecruiters", "Acme", "ai_heavy"), cutoff="")
    assert {p["country"] for _, p in calls} == {"de", "nl", "ie"}
    assert len(res.jobs) == 1 and res.jobs[0].location == "Berlin, Germany"
    assert "Build ML" in smartrecruiters.fetch_description(res.jobs[0])
    assert res.jobs[0].url == "https://jobs.smartrecruiters.com/Acme/1-ml"


def test_workday_board_parsing_and_dates():
    assert workday.parse_board("https://acme.wd3.myworkdayjobs.com/en-US/Careers") == (
        "acme.wd3.myworkdayjobs.com", "acme", "Careers")
    today = date(2026, 9, 27)
    assert workday.posted_from_text("Posted Today", today) == "2026-09-27"
    assert workday.posted_from_text("Posted Yesterday", today) == "2026-09-26"
    assert workday.posted_from_text("Posted 3 Days Ago", today) == "2026-09-24"
    assert workday.posted_from_text("Posted 30+ Days Ago", today) == "2026-08-28"
    assert workday.posted_from_text("", today) == ""


def test_workday_resolves_multi_location_via_detail(monkeypatch):
    postings = [
        {"title": "Senior AI Engineer", "externalPath": "/job/Dublin/AI_1", "locationsText": "Ireland, Dublin",
         "postedOn": "Posted 2 Days Ago"},
        {"title": "Principal ML Engineer", "externalPath": "/job/x/ML_2", "locationsText": "3 Locations",
         "postedOn": "Posted Today"},
        {"title": "ML Engineer", "externalPath": "/job/US/ML_3", "locationsText": "USA, CA, Pleasanton",
         "postedOn": "Posted Today"},
        {"title": "Account Executive", "externalPath": "/job/Dublin/AE_4", "locationsText": "Ireland, Dublin",
         "postedOn": "Posted Today"},
    ]
    monkeypatch.setattr(workday, "http_post_json", lambda url, body: {"total": len(postings), "jobPostings": postings})
    monkeypatch.setattr(workday, "http_get_json", lambda url: {"jobPostingInfo": {
        "location": "USA, New York", "additionalLocations": ["Germany, Berlin"], "startDate": "2026-09-25",
        "jobDescription": "<p>LLMs</p>", "externalUrl": "https://acme.wd3.myworkdayjobs.com/Careers/job/x/ML_2"}})
    c = Company("Acme", "workday", "acme", "ai_heavy", url="https://acme.wd3.myworkdayjobs.com/Careers")
    res = workday.collect(c, cutoff="")
    got = {j.source_id: (j.region, j.posted_at) for j in res.jobs}
    assert set(got) == {"acme-AI_1", "acme-ML_2"}
    assert got["acme-ML_2"] == ("DE", "2026-09-25")
    assert next(j for j in res.jobs if j.source_id == "acme-ML_2").description == "LLMs"


def test_teamtailor(monkeypatch):
    monkeypatch.setattr(teamtailor, "http_get_text", lambda url, params=None, browser=True: (TEAMTAILOR_RSS, url))
    res = teamtailor.collect(Company("Acme", "teamtailor", "acme", "ai_native", url="https://careers.acme.ai"), "")
    assert len(res.jobs) == 1
    job = res.jobs[0]
    assert job.region == "NL" and job.posted_at == "2026-09-21T06:58:57+00:00"
    assert job.work_type == "hybrid" and job.description == "Build agents"


def test_jsonld_posting_on_page(monkeypatch):
    monkeypatch.setattr(jsonld, "http_get_text", lambda url, params=None, browser=True: (JSONLD_PAGE, url))
    res = jsonld.collect(Company("Acme", "jsonld", "acme", "ai_native", url="https://acme.ai/careers"), "")
    assert len(res.jobs) == 1
    job = res.jobs[0]
    assert (job.title, job.region, job.source_id) == ("Senior LLM Engineer", "IE", "acme-42")
    assert job.description == "RAG & agents" and job.extra["salary"] == "90000-120000 EUR YEAR"


def test_jsonld_follows_relevant_same_site_links(monkeypatch):
    fetched = []

    def fake(url, params=None, browser=True):
        fetched.append(url)
        return (LISTING_PAGE, url) if url.endswith("/careers") else (JSONLD_PAGE, url)

    monkeypatch.setattr(jsonld, "http_get_text", fake)
    res = jsonld.collect(Company("Acme", "jsonld", "acme", "ai_native", url="https://acme.ai/careers"), "")
    assert fetched == ["https://acme.ai/careers", "https://acme.ai/jobs/ml-engineer-berlin"]
    assert len(res.jobs) == 1


def test_jsonld_reports_js_rendered_pages(monkeypatch):
    monkeypatch.setattr(jsonld, "http_get_text", lambda url, params=None, browser=True: ("<html></html>", url))
    res = jsonld.collect(Company("Acme", "jsonld", "acme", "ai_native", url="https://acme.ai/careers"), "")
    assert res.outcome == "EMPTY" and "JavaScript" in res.detail


def test_detect_finds_embedded_boards():
    page = LISTING_PAGE + """
      <a href="https://acme.wd3.myworkdayjobs.com/en-US/Careers/job/x">x</a>
      <script src="https://jobs.ashbyhq.com/acme-labs/embed"></script>
      <a href="https://acme.jobs.personio.com/">Personio</a>"""
    found = {(c.ats, c.slug, c.url) for c in detect.candidates(page, "https://acme.ai/careers", "Acme", "ai_native")}
    assert ("greenhouse", "acmeai", "") in found
    assert ("ashby", "acme-labs", "") in found
    assert ("personio", "acme", "https://acme.jobs.personio.com") in found
    assert ("workday", "acme", "https://acme.wd3.myworkdayjobs.com/en-US/Careers") in found
    assert any(c[0] == "jsonld" for c in found)          # relevant same-site job links exist
    assert not any(c[1] == "embed" for c in found)


def test_guess_slugs():
    assert detect.guess_slugs("Black Forest Labs", "https://bfl.ai/careers")[:3] == [
        "bfl", "blackforestlabs", "black-forest-labs"]


def test_company_line_roundtrip():
    c = Company("Acme", "workday", "acme", "ai_heavy", url="https://acme.wd3.myworkdayjobs.com/Careers")
    assert detect.company_line(c) == (
        'Company("Acme", "workday", "acme", "ai_heavy", url="https://acme.wd3.myworkdayjobs.com/Careers"),')
