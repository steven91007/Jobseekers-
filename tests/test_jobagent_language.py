"""English-only filtering: language detection, requirement phrases, and where the filter applies."""

import json

import pytest

from jobagent import language, report, store
from jobagent.models import Job

EN = ("We are building agentic AI products for our customers. You will design and ship LLM features, "
      "work with the product team on evaluation, and own the services that run them in production. "
      "Our team in Berlin works in English and we offer visa sponsorship for this role. ") * 2
DE = ("Wir suchen eine engagierte Persönlichkeit für unser Team in München. Du entwickelst KI-Lösungen "
      "und arbeitest eng mit unseren Kunden zusammen. Wir bieten dir eine unbefristete Stelle, flexible "
      "Arbeitszeiten und die Möglichkeit, im Homeoffice zu arbeiten. Das bringst du mit: ein Studium der "
      "Informatik oder eine vergleichbare Ausbildung sowie Erfahrung mit Python. ") * 2
NL = ("Wij zoeken een AI engineer die ons team in Amsterdam komt versterken. Je werkt aan onze producten "
      "en je bent verantwoordelijk voor de ontwikkeling van nieuwe modellen. Wat wij bieden: een goed "
      "salaris, een fijne werkplek en veel ruimte voor jouw ontwikkeling. Wij hebben een open cultuur. ") * 2


def test_posting_language():
    assert language.posting_language(EN) == "en"
    assert language.posting_language(DE) == "de"
    assert language.posting_language(NL) == "nl"
    assert language.posting_language("AI Engineer, Python") == "unknown"


@pytest.mark.parametrize("text, required", [
    ("Fluent German and English required.", "german"),
    ("Languages: Fluent in German (C1), with professional proficiency in English", "german"),
    ("Very good German skills (C1) are mandatory", "german"),
    ("Business fluent German", "german"),
    ("Dutch speaking is a must", "dutch"),
    ("German is a plus.", ""),
    ("Nice to have: German (B2)", ""),
    ("You speak English; German is not required.", ""),
    ("Excellent English and preferably German presentation and writing skills", ""),
    ("Experience with German customers", ""),
    ("Fluent in English. Dutch is nice to have.", ""),
])
def test_required_language(text, required):
    assert language.required_language(text) == required


def test_check_prefers_description_then_title():
    assert language.check("AI Engineer (m/w/d)", EN).english
    assert not language.check("AI Engineer (m/w/d)", DE).english  # English title, German posting
    assert language.check("AI Engineer (m/w/d)", EN + " Fluent German (C1) required.").reason == "requires German"
    assert not language.check("KI-Entwickler für Produktintegration (m/w/d)").english  # no description
    assert language.check("Senior AI Engineer (m/w/d)").english                        # no evidence either way


def _job(sid, title, desc="", **kw):
    return Job("arbeitnow", sid, title, "Acme", "Berlin", "DE", f"https://x/{sid}",
               posted_at=kw.get("posted", ""), description=desc)


def test_filter_jobs_counts_reasons():
    kept, dropped = language.filter_jobs([_job("1", "AI Engineer", EN), _job("2", "AI Engineer", DE),
                                          _job("3", "KI-Entwickler (m/w/d)"),
                                          _job("4", "AI Engineer", EN + " German C1 required.")])
    assert [j.source_id for j in kept] == ["1"]
    assert dropped == {"description is in German": 1, "German job title": 1, "requires German": 1}


def test_report_hides_non_english_and_scorer_flags(tmp_path):
    from datetime import datetime, timedelta, timezone

    from jobagent.models import Assessment

    now = datetime.now(timezone.utc)
    posted = (now - timedelta(hours=3)).isoformat(timespec="seconds")
    conn = store.connect(tmp_path / "l.db")
    run = store.start_run(conn, "24h")
    jobs = [_job("1", "AI Engineer", EN, posted=posted), _job("2", "LLM Engineer", DE, posted=posted),
            _job("3", "ML Engineer", "", posted=posted), _job("4", "AI Platform Engineer", "", posted=posted)]
    jobs[0].company, jobs[1].company, jobs[2].company, jobs[3].company = "A", "B", "C", "D"
    jobs[3].extra = {"language_skip": "description is in Dutch"}
    store.upsert_jobs(conn, jobs, run)
    a = Assessment(fit_score=90, apply_priority="now", employer_type="ai_product_company", seniority="senior",
                   local_language_required="german", language_evidence="Deutsch C1",
                   visa_or_relocation="not_mentioned", remote_policy="hybrid", salary=None, must_haves=[],
                   gaps=[], why_fit="x", suggested_pitch="x")
    store.save_assessment(conn, "arbeitnow:3", a, model="m", prompt_version="v", trace_id=None, observation_id=None)

    hidden = {}
    rows = report.build_rows(store.open_jobs(conn, run), window_hours=24, max_age_hours=168,
                             english_only=True, hidden=hidden)
    assert [r["job_key"] for r in rows] == ["arbeitnow:1"]
    assert hidden == {"description is in German": 1, "requires German (scorer)": 1, "description is in Dutch": 1}
    all_rows = report.build_rows(store.open_jobs(conn, run), window_hours=24, max_age_hours=168)
    assert len(all_rows) == 4  # --all-languages shows everything
    # the scorer will not retry the job it already found to be Dutch
    assert "arbeitnow:4" not in store.unassessed_keys(conn, run, 10, english_only=True)
    assert "arbeitnow:4" in store.unassessed_keys(conn, run, 10)
    assert json.loads(store.get_job(conn, "arbeitnow:4")["extra"])["language_skip"]


def test_scorer_skips_llm_for_non_english(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from jobagent import config
    from jobagent.llm import scorer

    for var in ("OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(var, "")
    settings = config.load(env_file=tmp_path / "none.env")
    assert settings.english_only
    conn = store.connect(tmp_path / "s.db")
    run = store.start_run(conn, "24h")
    store.upsert_jobs(conn, [_job("9", "AI Engineer", "")], run)
    monkeypatch.setattr(scorer, "fetch_description", lambda job: setattr(job, "description", DE) or DE)
    called = []
    monkeypatch.setattr(scorer, "assess", lambda *a, **k: called.append(1))
    stats = scorer.score_jobs(SimpleNamespace(), conn, settings, ["arbeitnow:9"], "profile")
    assert called == [] and stats["scored"] == 0 and stats["skipped_non_english"] == 1
    row = store.get_job(conn, "arbeitnow:9")
    assert row["description"] == DE and json.loads(row["extra"])["language_skip"] == "description is in German"
