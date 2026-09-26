"""Per-job fit assessment with OpenAI structured outputs."""

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor

import openai

from .. import observability as obs
from .. import store
from ..config import REGION_LABELS, Settings
from ..models import Assessment, Job
from ..sources import fetch_description
from .client import call
from .prompts import PROMPT_VERSION, SCORER_INSTRUCTIONS

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 12000
SCORER_WORKERS = 4

# Errors that mean every further call will fail too; stop the batch.
FATAL = (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)


def _job_text(job: Job) -> str:
    desc = (job.description or "").strip()[:MAX_DESCRIPTION_CHARS]
    salary = job.extra.get("salary")
    lines = [
        f"Title: {job.title}",
        f"Company: {job.company} (tier: {job.company_tier})",
        f"Location: {job.location} [{REGION_LABELS.get(job.region, job.region)}]",
        f"Posted: {job.posted_at[:10] or 'unknown'}",
        f"Workplace: {job.work_type or 'unknown'}",
    ]
    if salary:
        lines.append(f"Compensation (from job board): {salary}")
    lines.append("\nDescription:\n" + (desc or "(no description available)"))
    return "\n".join(lines)


def assess(client, settings: Settings, job: Job, profile: str) -> Assessment:
    resp = call(
        client.responses.parse,
        effort=settings.scorer_reasoning_effort,
        trace_name="scorer",
        model=settings.openai_scorer_model,
        # Stable prefix (instructions + profile) first, so OpenAI's prompt cache hits.
        instructions=f"{SCORER_INSTRUCTIONS}\n# Candidate profile\n\n{profile}",
        input=_job_text(job),
        text_format=Assessment,
        max_output_tokens=8000,
        prompt_cache_key=f"jobagent-scorer-{PROMPT_VERSION}",
    )
    parsed = resp.output_parsed
    if parsed is None:
        raise ValueError(f"no parsed output (status={resp.status}, "
                         f"incomplete={getattr(resp, 'incomplete_details', None)})")
    return parsed


def _score_one(client, settings: Settings, job: Job, profile: str) -> dict:
    """Runs in a worker thread. Returns everything the main thread needs to persist."""
    with obs.span(
        f"score {job.job_key}",
        input={"title": job.title, "company": job.company, "location": job.location,
               "url": job.url},
        metadata={"prompt_version": PROMPT_VERSION, "model": settings.openai_scorer_model},
    ) as sp:
        had_desc = bool(job.description)
        fetch_description(job)
        a = assess(client, settings, job, profile)
        sp.update(output=a.model_dump())
        sp.score(name="fit_score", value=float(a.fit_score), data_type="NUMERIC")
        sp.score(name="apply_priority", value=a.apply_priority, data_type="CATEGORICAL")
        return {
            "job": job, "assessment": a, "fetched_desc": not had_desc and bool(job.description),
            "trace_id": sp.trace_id, "observation_id": sp.id,
        }


def score_jobs(client, conn, settings: Settings, job_keys: list[str], profile: str) -> dict:
    """Score jobs in parallel; persist in the calling thread (SQLite stays single-threaded)."""
    stats = {"requested": len(job_keys), "scored": 0, "failed": 0, "aborted": ""}
    if not job_keys:
        return stats
    jobs = [store.row_to_job(store.get_job(conn, k)) for k in job_keys]

    with obs.span("score", input={"count": len(jobs)}) as batch_span:
        with ThreadPoolExecutor(SCORER_WORKERS) as pool:
            futures = [
                (job, pool.submit(contextvars.copy_context().run, _score_one,
                                  client, settings, job, profile))
                for job in jobs
            ]
            for job, fut in futures:
                if stats["aborted"]:
                    fut.cancel()
                    continue
                try:
                    res = fut.result()
                except FATAL as e:
                    stats["aborted"] = f"{type(e).__name__}: {e}"
                    log.error("scoring aborted: %s", stats["aborted"])
                    continue
                except Exception as e:
                    stats["failed"] += 1
                    log.warning("scoring %s failed: %s", job.job_key, e)
                    continue
                if res["fetched_desc"]:
                    store.save_description(conn, job.job_key, job.description, job.extra)
                store.save_assessment(
                    conn, job.job_key, res["assessment"],
                    model=settings.openai_scorer_model, prompt_version=PROMPT_VERSION,
                    trace_id=res["trace_id"], observation_id=res["observation_id"],
                )
                stats["scored"] += 1
        batch_span.update(output=stats)
    return stats
