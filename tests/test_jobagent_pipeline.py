"""Store, report and agent loop, offline. The agent runs against a fake OpenAI client."""

import json
from types import SimpleNamespace

import pytest

from jobagent import config, observability, report, store
from jobagent.models import Assessment, Job


def make_job(source, sid, title, company="Acme", region="DE", posted="2026-09-22", tier="ai_native"):
    return Job(source=source, source_id=sid, title=title, company=company, location="Berlin",
               region=region, url=f"https://x/{sid}", posted_at=posted, company_tier=tier)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for var in ("OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(var, "")
    s = config.load(env_file=tmp_path / "none.env")
    return s.__class__(**{**s.__dict__, "db_path": tmp_path / "t.db", "reports_dir": tmp_path / "r",
                          "agent_runs_dir": tmp_path / "runs", "profile_path": tmp_path / "profile.md"})


@pytest.fixture
def conn(settings):
    c = store.connect(settings.db_path)
    yield c
    c.close()


def test_dedupe_prefers_ats_over_linkedin():
    jobs = [
        make_job("linkedin", "1", "Machine Learning Engineer (m/w/d)"),
        make_job("greenhouse", "9", "Machine Learning Engineer - Berlin"),
        make_job("linkedin", "2", "Senior Machine Learning Engineer"),
        make_job("linkedin", "1", "Machine Learning Engineer (m/w/d)"),  # same key twice
    ]
    kept = store.dedupe_batch(jobs)
    assert sorted(j.job_key for j in kept) == ["greenhouse:9", "linkedin:2"]


def test_upsert_marks_new_once_and_skips_cross_source_duplicates(conn):
    run1 = store.start_run(conn, "7d")
    assert store.upsert_jobs(conn, [make_job("greenhouse", "9", "AI Engineer")], run1) == ["greenhouse:9"]
    run2 = store.start_run(conn, "7d")
    new = store.upsert_jobs(conn, [make_job("greenhouse", "9", "AI Engineer"),
                                   make_job("linkedin", "5", "AI Engineer (f/m/d)")], run2)
    assert new == []
    rows = store.open_jobs(conn, run2)
    assert [(r["job_key"], r["is_new"]) for r in rows] == [("greenhouse:9", 0)]


def _assessment(score, priority="now"):
    return Assessment(
        fit_score=score, apply_priority=priority, employer_type="ai_product_company",
        seniority="senior", local_language_required="none", language_evidence=None,
        visa_or_relocation="not_mentioned", remote_policy="hybrid", salary=None,
        must_haves=["Python"], gaps=[], why_fit="Strong match.", suggested_pitch="Line 1\nLine 2",
    )


def test_report_ranks_scored_first_and_writes_files(conn, settings):
    run = store.start_run(conn, "7d")
    store.upsert_jobs(conn, [make_job("greenhouse", "1", "AI Engineer", posted="2026-09-23"),
                             make_job("greenhouse", "2", "ML Engineer", posted="2025-01-01"),
                             make_job("linkedin", "3", "LLM Engineer", company="Other", tier="other")], run)
    store.save_assessment(conn, "greenhouse:2", _assessment(91), model="m", prompt_version="v",
                          trace_id=None, observation_id=None)
    rows = report.build_rows(store.open_jobs(conn, run), cutoff="2026-09-17")
    assert [r["job_key"] for r in rows] == ["greenhouse:2", "greenhouse:1", "linkedin:3"]
    assert rows[0]["recent"] is False and rows[1]["recent"] is True

    md = settings.reports_dir / "r.md"
    xlsx = settings.reports_dir / "r.xlsx"
    report.write_markdown(md, rows, since="7d", cutoff="2026-09-17", stats={}, briefing="## Top picks\n- x",
                          candidates=[], trace_link=None)
    report.write_excel(xlsx, rows, [])
    text = md.read_text(encoding="utf-8")
    assert "## Apply now" in text and "Still open at watchlist companies" in text and "## Agent briefing" in text
    assert xlsx.stat().st_size > 0


def test_observability_is_noop_without_keys(settings):
    assert observability.init(settings) is False
    with observability.span("x", input={"a": 1}) as sp:
        sp.update(output=1)
        sp.score(name="s", value=1)
    assert observability.create_score(trace_id="t", observation_id=None, name="n", value="v",
                                      data_type="CATEGORICAL") is False
    observability.flush()


def test_span_records_error_and_reraises(settings):
    with pytest.raises(ValueError):
        with observability.span("boom"):
            raise ValueError("x")


# --- agent loop with a scripted fake OpenAI client ----------------------------------------


class FakeResponses:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


def fn_call(name, args, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


def resp(rid, output, text=""):
    return SimpleNamespace(id=rid, output=output, output_text=text,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def test_agent_loop_dispatches_tools_and_returns_briefing(conn, settings):
    from jobagent.llm import agent, tools

    run = store.start_run(conn, "7d")
    store.upsert_jobs(conn, [make_job("greenhouse", "1", "AI Engineer")], run)
    script = [
        resp("r1", [fn_call("list_jobs", {"region": "ALL", "only_new": True, "min_fit_score": None, "limit": 5}, "c1"),
                    fn_call("add_company_candidate", {"name": "NewCo AI", "careers_url": "https://newco.ai/jobs",
                                                      "ats": "unknown", "slug": None, "regions": ["DE"],
                                                      "reason": "LLM startup hiring in Berlin"}, "c2")]),
        resp("r2", [SimpleNamespace(type="web_search_call", action=SimpleNamespace(query="AI startups Berlin"),
                                    status="completed")], text="## Top picks\n- greenhouse:1"),
    ]
    client = SimpleNamespace(responses=FakeResponses(script))
    ctx = tools.ToolContext(settings=settings, conn=conn, run_id=run, since="7d")
    out = agent.run_agent(client, ctx, {"Germany": 1}, "profile")

    assert out["briefing"].startswith("## Top picks")
    assert out["turns"] == 2 and out["tool_calls"] == 2 and out["error"] == ""
    second = client.responses.calls[1]
    assert second["previous_response_id"] == "r1"
    outputs = {i["call_id"]: json.loads(i["output"]) for i in second["input"]}
    assert outputs["c1"]["jobs"][0]["job_key"] == "greenhouse:1"
    assert outputs["c2"]["status"] == "recorded"
    assert [c["name"] for c in store.list_candidates(conn)] == ["NewCo AI"]


def test_agent_forces_final_answer_on_last_turn(conn, settings):
    from jobagent.llm import agent, tools

    s = settings.__class__(**{**settings.__dict__, "agent_max_turns": 2})
    run = store.start_run(conn, "7d")
    loop_call = fn_call("list_jobs", {"region": "DE", "only_new": False, "min_fit_score": None, "limit": 1}, "c")
    client = SimpleNamespace(responses=FakeResponses([resp("r1", [loop_call]), resp("r2", [], text="final")]))
    ctx = tools.ToolContext(settings=s, conn=conn, run_id=run, since="7d")
    out = agent.run_agent(client, ctx, {}, "p")
    assert out["briefing"] == "final"
    assert client.responses.calls[1]["tool_choice"] == "none"


def test_tool_budget_enforced(conn, settings):
    from jobagent.llm import tools

    ctx = tools.ToolContext(settings=settings, conn=conn, run_id=1, since="7d",
                            linkedin_calls=tools.LINKEDIN_CALL_BUDGET)
    assert "error" in tools.search_linkedin(ctx, "RAG", "DE", "7d")
    assert tools.add_company_candidate(ctx, "OpenAI", "", "ashby", "openai", ["DE"], "x") == {
        "status": "already_on_watchlist"}


def test_assessment_schema_is_strict_compatible():
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(Assessment)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_full_run_with_llm_phase_offline(settings, monkeypatch, tmp_path):
    """pipeline.run end to end: stubbed sources, fake OpenAI, real store/report/agent wiring."""
    from jobagent import pipeline
    from jobagent.llm import client as llm_client
    from jobagent.sources import SourceResult

    s = settings.__class__(**{**settings.__dict__, "openai_api_key": "sk-fake"})
    (tmp_path / "profile.md").write_text("Senior LLM engineer, Python, needs no visa.")

    monkeypatch.setattr(pipeline, "collect_watchlist", lambda st, regions, console: [
        SourceResult("greenhouse", "acme", jobs=[make_job("greenhouse", "1", "LLM Engineer", posted="2026-09-23")],
                     raw_count=5)])
    monkeypatch.setattr(pipeline, "collect_linkedin", lambda st, regions, since, console: [
        SourceResult("linkedin", "DE / AI Engineer",
                     jobs=[make_job("linkedin", "7", "AI Engineer", company="Other", tier="other")], raw_count=1),
        SourceResult("linkedin", "NL / AI Engineer", outcome="BLOCKED", detail="HTTP 999")])
    monkeypatch.setattr("jobagent.sources.fetch_description", lambda job: job.description)
    monkeypatch.setattr("jobagent.llm.scorer.fetch_description", lambda job: job.description)

    class Parse:
        def __init__(self):
            self.n = 0

        def __call__(self, **kw):
            self.n += 1
            return SimpleNamespace(output_parsed=_assessment(80 + self.n), status="completed")

    script = [resp("r1", [fn_call("list_jobs", {"region": "ALL", "only_new": False, "min_fit_score": 70,
                                                 "limit": 10}, "c1")]),
              resp("r2", [], text="## Top picks\n- greenhouse:1 LLM Engineer")]
    fake = SimpleNamespace(responses=SimpleNamespace(parse=Parse(), create=FakeResponses(script).create))
    monkeypatch.setattr(llm_client, "get_client", lambda st: fake)

    stats = pipeline.run(s, since="7d", console=__import__("rich.console").console.Console(quiet=True))

    assert stats["unique"] == 2 and stats["new"] == 2
    assert stats["scoring"]["scored"] == 2 and stats["scoring"]["failed"] == 0
    assert stats["agent"]["turns"] == 2 and stats["agent"]["tool_calls"] == 1
    assert stats["failed_sources"] == ["linkedin:NL / AI Engineer (BLOCKED)"]
    md = open(stats["markdown"], encoding="utf-8").read()
    assert "## Agent briefing" in md and "## Apply now" in md and "BLOCKED" in md
    assert list(s.agent_runs_dir.glob("*-run1.json"))


# --- candidate profile guard and re-scoring ------------------------------------------


def test_profile_missing_or_template_is_refused(settings, tmp_path):
    from jobagent.llm.client import ProfileError, load_profile, profile_fingerprint

    with pytest.raises(ProfileError, match="no candidate profile"):
        load_profile(settings)

    template = (config.ROOT / "profile.example.md").read_text(encoding="utf-8")
    settings.profile_path.write_text(template, encoding="utf-8")
    with pytest.raises(ProfileError, match="unedited"):
        load_profile(settings)

    settings.profile_path.write_text("Senior AI engineer, needs visa sponsorship.\n", encoding="utf-8")
    text = load_profile(settings)
    assert text.startswith("Senior AI engineer")
    assert profile_fingerprint(text) == profile_fingerprint(text + "\n\n") != profile_fingerprint("other")


def test_profile_path_can_be_absolute(tmp_path, monkeypatch):
    real = tmp_path / "elsewhere" / "me.md"
    monkeypatch.setenv("JOBAGENT_PROFILE", str(real))
    assert config.load(env_file=tmp_path / "none.env").profile_path == real
    monkeypatch.setenv("JOBAGENT_PROFILE", "profile.md")
    assert config.load(env_file=tmp_path / "none.env").profile_path == config.ROOT / "profile.md"


def test_old_database_gains_profile_column_and_stale_keys_work(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(store.SCHEMA.replace(",\n    profile_sha     TEXT\n", "\n"))
    old.commit()
    old.close()
    assert "profile_sha" not in {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(assessments)")}

    conn = store.connect(path)
    assert "profile_sha" in {r["name"] for r in conn.execute("PRAGMA table_info(assessments)")}
    run = store.start_run(conn, "7d")
    store.upsert_jobs(conn, [make_job("greenhouse", str(i), f"AI Engineer {i}", company=f"C{i}")
                             for i in range(3)], run)
    for key, sha in (("greenhouse:0", None), ("greenhouse:1", "aaa"), ("greenhouse:2", "bbb")):
        store.save_assessment(conn, key, _assessment(50), model="m", prompt_version="v",
                              trace_id=None, observation_id=None, profile_sha=sha)
    assert sorted(store.stale_assessment_keys(conn, "bbb")) == ["greenhouse:0", "greenhouse:1"]
    assert store.stale_assessment_keys(conn, "bbb", limit=1) and len(store.stale_assessment_keys(conn, "bbb", 1)) == 1


def test_full_run_with_template_profile_skips_llm(settings, monkeypatch, tmp_path):
    from jobagent import pipeline
    from jobagent.llm import client as llm_client
    from jobagent.sources import SourceResult

    s = settings.__class__(**{**settings.__dict__, "openai_api_key": "sk-fake"})
    s.profile_path.write_text((config.ROOT / "profile.example.md").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(pipeline, "collect_watchlist", lambda st, regions, console: [
        SourceResult("greenhouse", "acme", jobs=[make_job("greenhouse", "1", "LLM Engineer")], raw_count=1)])
    monkeypatch.setattr(pipeline, "collect_linkedin", lambda st, regions, since, console: [])

    def no_llm(_settings):
        raise AssertionError("the OpenAI client must not be created with a template profile")

    monkeypatch.setattr(llm_client, "get_client", no_llm)
    stats = pipeline.run(s, since="7d", console=__import__("rich.console").console.Console(quiet=True))
    assert stats["unique"] == 1
    assert stats["scoring"]["scored"] == 0 and "unedited" in stats["scoring"]["aborted"]
    assert open(stats["markdown"], encoding="utf-8").read()


def test_rescore_only_touches_assessments_from_another_profile(settings, monkeypatch):
    from jobagent import pipeline
    from jobagent.llm import client as llm_client
    from jobagent.llm.client import profile_fingerprint

    s = settings.__class__(**{**settings.__dict__, "openai_api_key": "sk-fake"})
    s.profile_path.write_text("Senior AI engineer with 6 years, needs visa sponsorship.", encoding="utf-8")
    current = profile_fingerprint(s.profile_path.read_text(encoding="utf-8"))

    conn = store.connect(s.db_path)
    run = store.start_run(conn, "7d")
    store.upsert_jobs(conn, [make_job("greenhouse", "1", "AI Engineer", company="A"),
                             make_job("greenhouse", "2", "ML Engineer", company="B")], run)
    for j in ("greenhouse:1", "greenhouse:2"):
        conn.execute("UPDATE jobs SET description='Build LLM systems.' WHERE job_key=?", (j,))
    store.save_assessment(conn, "greenhouse:1", _assessment(40, "skip"), model="m", prompt_version="v",
                          trace_id=None, observation_id=None, profile_sha=None)
    store.save_assessment(conn, "greenhouse:2", _assessment(70, "soon"), model="m", prompt_version="v",
                          trace_id=None, observation_id=None, profile_sha=current)
    conn.commit()
    conn.close()

    calls = []

    def parse(**kw):
        calls.append(kw)
        return SimpleNamespace(output_parsed=_assessment(90, "now"), status="completed")

    monkeypatch.setattr(llm_client, "get_client", lambda st: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    stats = pipeline.rescore(s, console=__import__("rich.console").console.Console(quiet=True))

    assert stats["requested"] == 1 and stats["scoring"]["scored"] == 1 and len(calls) == 1
    assert "6 years" in calls[0]["instructions"]
    assert stats["changes"] == [{"job_key": "greenhouse:1", "title": "AI Engineer", "company": "A",
                                 "old_score": 40, "new_score": 90, "old_priority": "skip", "new_priority": "now"}]
    assert pipeline.priority_matrix(stats["changes"]) == {"skip -> now": 1}
    conn = store.connect(s.db_path)
    assert {r["job_key"]: r["profile_sha"] for r in conn.execute("SELECT * FROM assessments")} == {
        "greenhouse:1": current, "greenhouse:2": current}
    assert pipeline.rescore(s, console=__import__("rich.console").console.Console(quiet=True))["requested"] == 0
