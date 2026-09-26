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
                      base_url="http://127.0.0.1:9", span_exporter=exporter, flush_at=1)
    monkeypatch.setattr(obs, "_client", client)
    yield exporter
    client.flush()


def test_scorer_generation_nests_under_run_trace(live_langfuse, monkeypatch):
    from langfuse.openai import OpenAI

    from jobagent import config
    from jobagent.llm import scorer

    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(200, json=_responses_payload(json.dumps(ASSESSMENT))))
    from openai import DefaultHttpxClient

    client = OpenAI(api_key="sk-test", http_client=DefaultHttpxClient(transport=transport), max_retries=0)
    settings = config.load()
    settings = settings.__class__(**{**settings.__dict__, "openai_scorer_model": "gpt-test",
                                     "scorer_reasoning_effort": ""})
    job = Job(source="ashby", source_id="x1", title="LLM Engineer", company="Acme",
              location="Berlin", region="DE", url="https://x", description="Build RAG.")

    with obs.trace_attributes(session_id="s1", user_id="me", tags=["jobagent"], trace_name="jobagent.run"):
        with obs.span("jobagent.run", as_type="agent") as root:
            result = scorer._score_one(client, settings, job, "profile")

    assert isinstance(result["assessment"], Assessment) and result["assessment"].fit_score == 88
    assert result["trace_id"] == root.trace_id and result["observation_id"]

    obs.flush()
    spans = {s.name: s for s in live_langfuse.get_finished_spans()}
    assert {"jobagent.run", "score ashby:x1", "scorer"} <= set(spans)
    run, score, gen = spans["jobagent.run"], spans["score ashby:x1"], spans["scorer"]
    assert score.parent.span_id == run.context.span_id
    assert gen.parent.span_id == score.context.span_id
    assert gen.attributes.get("langfuse.observation.type") == "generation"
    assert "gpt-test" in json.dumps(dict(gen.attributes))
    assert run.attributes.get("session.id") == "s1" or run.attributes.get("langfuse.session.id") == "s1"


def test_span_error_level_recorded(live_langfuse):
    with pytest.raises(RuntimeError):
        with obs.span("collect.linkedin DE / AI Engineer"):
            raise RuntimeError("blocked")
    obs.flush()
    span = next(s for s in live_langfuse.get_finished_spans() if s.name.startswith("collect.linkedin"))
    assert span.attributes.get("langfuse.observation.level") == "ERROR"
    assert "blocked" in span.attributes.get("langfuse.observation.status_message", "")
