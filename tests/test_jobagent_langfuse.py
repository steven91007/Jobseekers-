"""Live-path Langfuse test without a Langfuse server or OpenAI key.

Spans go to an in-memory OpenTelemetry exporter and OpenAI calls hit a mock
HTTP transport, so this exercises the real Langfuse SDK and the real
langfuse.openai wrapper around the real OpenAI SDK.
"""

import json
import uuid

import httpx2
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from jobagent import observability as obs
from jobagent.models import Assessment, Job

ASSESSMENT = {
    "fit_score": 88, "apply_priority": "now", "employer_type": "ai_product_company",
    "seniority": "senior", "local_language_required": "none", "language_evidence": None,
    "visa_or_relocation": "not_mentioned", "remote_policy": "hybrid", "salary": None,
    "must_haves": ["Python", "LLMs"], "gaps": [], "why_fit": "Good.", "suggested_pitch": "a\nb\nc",
}


def _responses_payload(text: str) -> dict:
    return {
        "id": "resp_1", "object": "response", "created_at": 1790000000, "status": "completed",
        "model": "gpt-test", "output": [{
            "type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "usage": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500,
                  "input_tokens_details": {"cached_tokens": 1000},
                  "output_tokens_details": {"reasoning_tokens": 100}},
    }


@pytest.fixture
def live_langfuse(monkeypatch):
    from langfuse import Langfuse

    exporter = InMemorySpanExporter()
    # Langfuse keeps one client per public key, so each test needs its own key.
    public_key = f"pk-lf-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", public_key)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
    client = Langfuse(public_key=public_key, secret_key="sk-lf-test",
                      base_url="http://127.0.0.1:9", span_exporter=exporter, flush_at=1,
                      mask_otel_spans=obs._mask_otel_spans)
    monkeypatch.setattr(obs, "_client", client)
    yield exporter
    client.flush()


def test_scorer_trace_follows_best_practices(live_langfuse, monkeypatch):
    from langfuse.openai import OpenAI
    from openai import DefaultHttpxClient

    from jobagent import config
    from jobagent.llm import scorer

    requests_seen = []

    def handler(request):
        requests_seen.append(json.loads(request.content))
        return httpx2.Response(200, json=_responses_payload(json.dumps(ASSESSMENT)))

    client = OpenAI(api_key="sk-test", http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
                    max_retries=0)
    settings = config.load()
    settings = settings.__class__(**{**settings.__dict__, "openai_scorer_model": "gpt-test",
                                     "scorer_reasoning_effort": "low"})
    job = Job(source="linkedin", source_id="x1", title="LLM Engineer", company="Acme",
              location="Berlin", region="DE", url="https://x")

    def fake_fetch(j):  # JD with recruiter contact details that must be masked
        j.description = "Build RAG. Apply via jane@acme.ai or +49 30 1234 5678."
        return j.description

    monkeypatch.setattr(scorer, "fetch_description", fake_fetch)

    with obs.trace_attributes(session_id="s1", user_id="me", tags=["jobagent"], trace_name=obs.NAMES.TRACE):
        with obs.span(obs.NAMES.ROOT) as root:
            result = scorer._score_one(client, settings, job, "profile")

    assert result["assessment"].fit_score == 88
    assert result["trace_id"] == root.trace_id and result["observation_id"]
    # thinking is requested so it lands on the generation
    assert requests_seen[0]["reasoning"] == {"effort": "low", "summary": "auto"}
    assert "name" not in requests_seen[0] and "metadata" not in requests_seen[0]

    obs.flush()
    spans = {s.name: s for s in live_langfuse.get_finished_spans()}
    names = {obs.NAMES.ROOT, obs.NAMES.SCORE_JOB, obs.NAMES.FETCH_DESCRIPTION, obs.NAMES.ASSESS_GENERATION}
    assert names <= set(spans)
    root_s, job_s = spans[obs.NAMES.ROOT], spans[obs.NAMES.SCORE_JOB]
    fetch_s, gen_s = spans[obs.NAMES.FETCH_DESCRIPTION], spans[obs.NAMES.ASSESS_GENERATION]

    # hierarchy: root > score-job > {fetch-job-description, assess-job-fit}
    assert job_s.parent.span_id == root_s.context.span_id
    assert fetch_s.parent.span_id == job_s.context.span_id
    assert gen_s.parent.span_id == job_s.context.span_id

    type_of = lambda sp: sp.attributes.get("langfuse.observation.type")
    assert (type_of(job_s), type_of(fetch_s), type_of(gen_s)) == ("chain", "retriever", "generation")

    # one string per span (merging dicts would let later spans overwrite `input`)
    attrs = "\n".join(json.dumps(dict(sp.attributes), default=str) for sp in spans.values())
    assert "gpt-test" in attrs                    # model name on the generation
    assert '"job_key": "linkedin:x1"' in attrs or "linkedin:x1" in json.dumps(dict(gen_s.attributes))

    # sensitive data is masked at export
    assert "jane@acme.ai" not in attrs and "1234 5678" not in attrs
    assert "[REDACTED EMAIL]" in json.dumps(dict(gen_s.attributes))   # JD reached the generation, masked


def test_span_error_level_recorded(live_langfuse):
    with pytest.raises(RuntimeError):
        with obs.span("collect.linkedin DE / AI Engineer"):
            raise RuntimeError("blocked")
    obs.flush()
    span = next(s for s in live_langfuse.get_finished_spans() if s.name.startswith("collect.linkedin"))
    assert span.attributes.get("langfuse.observation.level") == "ERROR"
    assert "blocked" in span.attributes.get("langfuse.observation.status_message", "")


def test_threaded_board_collection_nests_under_collect_span(live_langfuse, monkeypatch):
    """Regression: copying the context inside worker threads orphaned every board span."""
    from rich.console import Console

    from jobagent import config, pipeline
    from jobagent.sources import SourceResult

    monkeypatch.setattr(pipeline, "collect_company",
                        lambda company, cutoff: SourceResult(company.ats, company.slug, raw_count=1))
    settings = config.load()
    with obs.span(obs.NAMES.ROOT):
        with obs.span(obs.NAMES.COLLECT):
            results = pipeline.collect_watchlist(settings, ["DE"], Console(quiet=True))
    obs.flush()

    spans = live_langfuse.get_finished_spans()
    collect = next(s for s in spans if s.name == obs.NAMES.COLLECT)
    boards = [s for s in spans if s.name == obs.NAMES.COLLECT_BOARD]
    assert len(boards) == len(results) > 10
    assert all(s.parent is not None and s.parent.span_id == collect.context.span_id for s in boards)
    assert all(s.attributes.get("langfuse.observation.type") == "retriever" for s in boards)


def test_agent_region_scope_enforced(tmp_path):
    from jobagent import config, store
    from jobagent.llm import tools

    conn = store.connect(tmp_path / "t.db")
    ctx = tools.ToolContext(settings=config.load(), conn=conn, run_id=1, since="7d", regions=["IE"])
    assert "outside this run" in tools.search_linkedin(ctx, "RAG", "DE", "7d")["error"]
    assert ctx.linkedin_calls == 0
