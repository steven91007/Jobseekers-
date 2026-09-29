"""Store, report and agent loop, offline. The agent runs against a fake OpenAI client."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from jobagent import config, observability, report, store
from jobagent.models import Assessment, Job


def ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def make_job(source, sid, title, company="Acme", region="DE", posted=None, tier="ai_native"):
    posted = ago(30) if posted is None else posted
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
    run = store.start_run(conn, "24h")
    store.upsert_jobs(conn, [make_job("greenhouse", "1", "AI Engineer", posted=ago(2)),
                             make_job("greenhouse", "2", "ML Engineer", posted=ago(80)),
                             make_job("linkedin", "3", "LLM Engineer", company="Other", tier="other",
                                      posted=ago(5)),
                             make_job("greenhouse", "4", "Senior AI Engineer", company="Old",
                                      posted=ago(24 * 9))], run)
    store.save_assessment(conn, "greenhouse:2", _assessment(91), model="m", prompt_version="v",
                          trace_id=None, observation_id=None)
    rows = report.build_rows(store.open_jobs(conn, run), window_hours=24, max_age_hours=24 * 7)
    # scored first; the 9-day-old posting is not listed at all
    assert [r["job_key"] for r in rows] == ["greenhouse:2", "greenhouse:1", "linkedin:3"]
    assert rows[0]["recent"] is False and rows[1]["recent"] is True
    assert rows[1]["fresh"] > rows[2]["fresh"] > rows[0]["fresh"]
    assert rows[0]["rank"] == round(0.7 * 91 + 0.3 * rows[0]["fresh"])

    md = settings.reports_dir / "r.md"
    xlsx = settings.reports_dir / "r.xlsx"
    report.write_markdown(md, rows, since="24h", max_age_days=7, stats={}, briefing="## Top picks\n- x",
                          candidates=[], trace_link=None)
    report.write_excel(xlsx, rows, [])
    text = md.read_text(encoding="utf-8")
    assert "## Apply now" in text and "## Agent briefing" in text
    assert "### Posted in the last 24h (2)" in text and "Posted earlier, within 7 days (1)" in text
    assert "Old" not in text
    assert xlsx.stat().st_size > 0


def test_ranking_prefers_fresher_and_ai_engineer_titles(conn):
    run = store.start_run(conn, "24h")
    store.upsert_jobs(conn, [make_job("greenhouse", "1", "Machine Learning Engineer", posted=ago(1)),
                             make_job("greenhouse", "2", "AI Engineer", company="B", posted=ago(20)),
                             make_job("greenhouse", "3", "Machine Learning Engineer", company="C",
                                      posted=ago(10)),
                             make_job("greenhouse", "4", "Applied AI Engineer", company="D",
                                      posted=ago(3))], run)
    rows = report.build_rows(store.open_jobs(conn, run), window_hours=24, max_age_hours=168)
    # unscored: AI Engineer titles first, then freshest first
    assert [r["job_key"] for r in rows] == ["greenhouse:4", "greenhouse:2", "greenhouse:1", "greenhouse:3"]
    # scoring order follows the same focus-then-freshness rule
    assert store.unassessed_keys(conn, run, 3, max_age_hours=168) == ["greenhouse:4", "greenhouse:2",
                                                                      "greenhouse:1"]
    # with equal fit, the fresher posting ranks higher
    for key in ("greenhouse:1", "greenhouse:3"):
        store.save_assessment(conn, key, _assessment(80), model="m", prompt_version="v",
                              trace_id=None, observation_id=None)
    rows = report.build_rows(store.open_jobs(conn, run), window_hours=24, max_age_hours=168)
    assert [r["job_key"] for r in rows[:2]] == ["greenhouse:1", "greenhouse:3"]


def test_upsert_keeps_hour_precise_time_over_date_only(conn):
    run = store.start_run(conn, "24h")
    precise = ago(3)
    store.upsert_jobs(conn, [make_job("linkedin", "1", "AI Engineer", posted=precise)], run)
    store.upsert_jobs(conn, [make_job("linkedin", "1", "AI Engineer", posted=precise[:10])], run)
    assert store.get_job(conn, "linkedin:1")["posted_at"] == precise


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
        SourceResult("greenhouse", "acme", jobs=[make_job("greenhouse", "1", "LLM Engineer", posted=ago(6))],
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

    stats = pipeline.run(s, since="24h", console=__import__("rich.console").console.Console(quiet=True))

    assert stats["unique"] == 2 and stats["new"] == 2
    assert stats["scoring"]["scored"] == 2 and stats["scoring"]["failed"] == 0
    assert stats["agent"]["turns"] == 2 and stats["agent"]["tool_calls"] == 1
    assert stats["failed_sources"] == ["linkedin:NL / AI Engineer (BLOCKED)"]
    md = open(stats["markdown"], encoding="utf-8").read()
    assert "## Agent briefing" in md and "## Apply now" in md and "BLOCKED" in md
    assert list(s.agent_runs_dir.glob("*-run1.json"))
