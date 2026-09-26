"""Langfuse tracing, as an optional layer that can never break a run.

When LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are unset, or the SDK fails to
start, every helper here becomes a no-op. The OpenAI client in llm/client.py is
swapped for Langfuse's drop-in wrapper only when tracing is live, so every
scorer and agent call appears as a *generation* with tokens and cost.

Trace shape for one `jobagent run` (names are stable; see NAMES below):

    run-job-search                      span       root: request in, top jobs + briefing out
    ├── collect-jobs                    span
    │   ├── collect-job-board           retriever  one per watchlist company (metadata: company, ats)
    │   └── collect-linkedin-jobs       retriever  one per region x query (metadata: region, query)
    ├── store-jobs                      span       collected -> unique -> new
    ├── score-jobs                      span
    │   └── score-job                   chain      one per job (+ scores fit_score, apply_priority)
    │       ├── fetch-job-description   retriever  when the JD had to be fetched
    │       └── assess-job-fit          generation OpenAI structured output, with reasoning summary
    ├── research-jobs                   agent      task prompt in, briefing out
    │   ├── research-agent-step         generation one per turn (metadata: turn)
    │   └── list_jobs / search_linkedin / web_search ...   retriever|tool, siblings of the turn
    └── write-report                    span

Run-specific values (job keys, companies, regions, turn numbers) go in metadata,
never in names, so dashboards and evaluators can target a name across runs.
"""

import logging
import os
import re
from contextlib import contextmanager
from typing import Any, Iterator

from .config import Settings

log = logging.getLogger(__name__)


class NAMES:
    """Observation names. Treat as an API: evaluators and dashboards match on them."""

    TRACE = "run-job-search"
    ROOT = "run-job-search"
    COLLECT = "collect-jobs"
    COLLECT_BOARD = "collect-job-board"
    COLLECT_LINKEDIN = "collect-linkedin-jobs"
    STORE = "store-jobs"
    SCORE_BATCH = "score-jobs"
    SCORE_JOB = "score-job"
    FETCH_DESCRIPTION = "fetch-job-description"
    ASSESS_GENERATION = "assess-job-fit"
    AGENT = "research-jobs"
    AGENT_GENERATION = "research-agent-step"
    WEB_SEARCH = "web_search"
    REPORT = "write-report"


# Agent tools that only read data are retrievers; tools that change state are tools.
TOOL_TYPES = {
    "list_jobs": "retriever",
    "get_job_detail": "retriever",
    "check_company_board": "retriever",
    "search_linkedin": "tool",        # also stores new jobs
    "add_company_candidate": "tool",
    "web_search": "tool",
}

# --- masking ---------------------------------------------------------------------
# Job descriptions carry recruiter emails and phone numbers, and a profile may
# carry the candidate's own. Only +country-code phone numbers are matched, so
# 10-digit LinkedIn / Greenhouse job ids in URLs survive.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s./-]?\(?\d{1,5}\)?){2,5}\d")
_SECRET = re.compile(r"\b(?:sk|pk|rk)-(?:lf-|proj-|ant-)?[A-Za-z0-9_-]{16,}\b")


# OpenAI returns opaque encrypted reasoning blobs; they are noise in the UI.
_ENCRYPTED = re.compile(r'("encrypted_content":\s*)"[^"]{40,}"')


def mask_text(value: str) -> str:
    value = _ENCRYPTED.sub(r'\1"[omitted]"', value)
    value = _SECRET.sub("[REDACTED KEY]", value)
    value = _EMAIL.sub("[REDACTED EMAIL]", value)
    return _PHONE.sub("[REDACTED PHONE]", value)


def _mask_otel_spans(*, params):
    """Langfuse export-stage masking hook. Must never raise: an exception drops the batch."""
    try:
        from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

        patches = {}
        for identifier, span in params.spans.items():
            replacements = {}
            for key, value in span.attributes.items():
                if isinstance(value, str):
                    masked = mask_text(value)
                    if masked != value:
                        replacements[key] = masked
            if replacements:
                patches[identifier] = OtelSpanPatch(set_attributes=replacements)
        return MaskOtelSpansResult(span_patches=patches) if patches else None
    except Exception:  # leave the batch unmasked rather than losing it
        return None


_client = None
_warned = False


def enabled() -> bool:
    return _client is not None


def init(settings: Settings) -> bool:
    """Start the Langfuse client if configured. Returns True when tracing is live."""
    global _client
    if _client is not None:
        return True
    if not settings.langfuse_enabled:
        log.info("Langfuse disabled (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set)")
        return False
    # The OpenAI wrapper looks the client up through these, so keep them consistent.
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    os.environ["LANGFUSE_BASE_URL"] = settings.langfuse_base_url
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            # production | development: keeps experiments out of real dashboards
            environment=os.getenv("JOBAGENT_ENV", "").strip() or "production",
            mask_otel_spans=(
                _mask_otel_spans if os.getenv("JOBAGENT_LANGFUSE_MASK", "1").strip() != "0" else None
            ),
        )
    except Exception as e:  # never fatal
        _warn(f"Langfuse failed to start, tracing off: {e}")
        _client = None
    return _client is not None


def auth_check() -> tuple[bool, str]:
    if _client is None:
        return False, "disabled"
    try:
        return (True, "ok") if _client.auth_check() else (False, "auth_check returned False")
    except Exception as e:
        return False, str(e)


def _warn(msg: str) -> None:
    global _warned
    if not _warned:
        log.warning(msg)
        _warned = True


# --- spans -------------------------------------------------------------------


class _NoopObservation:
    trace_id = None
    id = None

    def update(self, **kwargs) -> None:
        pass

    def score(self, **kwargs) -> None:
        pass

    def score_trace(self, **kwargs) -> None:
        pass


class _LiveObservation:
    def __init__(self, obs):
        self._obs = obs
        self.trace_id = getattr(obs, "trace_id", None)
        self.id = getattr(obs, "id", None)

    def update(self, **kwargs) -> None:
        try:
            self._obs.update(**kwargs)
        except Exception as e:
            _warn(f"Langfuse update failed: {e}")

    def score(self, **kwargs) -> None:
        try:
            self._obs.score(**kwargs)
        except Exception as e:
            _warn(f"Langfuse score failed: {e}")

    def score_trace(self, **kwargs) -> None:
        try:
            self._obs.score_trace(**kwargs)
        except Exception as e:
            _warn(f"Langfuse score_trace failed: {e}")


NOOP = _NoopObservation()


@contextmanager
def span(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: Any = None,
) -> Iterator[_LiveObservation | _NoopObservation]:
    """Open a Langfuse observation as the current span. Exceptions are recorded as ERROR."""
    if _client is None:
        yield NOOP
        return
    try:
        cm = _client.start_as_current_observation(
            name=name, as_type=as_type, input=input, metadata=metadata
        )
        obs = cm.__enter__()
    except Exception as e:
        _warn(f"Langfuse span failed to open, continuing untraced: {e}")
        yield NOOP
        return

    handle = _LiveObservation(obs)
    try:
        yield handle
    except BaseException as exc:
        handle.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}"[:1000])
        try:
            cm.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception as e:
            _warn(f"Langfuse span failed to close: {e}")


@contextmanager
def trace_attributes(
    *,
    session_id: str,
    user_id: str,
    tags: list[str],
    trace_name: str,
    metadata: dict[str, Any] | None = None,
    version: str | None = None,
) -> Iterator[None]:
    """Trace-level attributes (session, user, tags) propagated to every child span."""
    if _client is None:
        yield
        return
    try:
        from langfuse import propagate_attributes

        cm = propagate_attributes(
            session_id=session_id,
            user_id=user_id,
            tags=tags,
            trace_name=trace_name,
            metadata={k: str(v)[:200] for k, v in (metadata or {}).items()},
            version=version,
        )
        cm.__enter__()
    except Exception as e:
        _warn(f"Langfuse propagate_attributes failed: {e}")
        yield
        return
    try:
        yield
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass


# --- scores and lifecycle -------------------------------------------------------


def create_score(
    *,
    trace_id: str,
    observation_id: str | None,
    name: str,
    value: float | str,
    data_type: str,
    comment: str | None = None,
) -> bool:
    """Attach a score to an existing trace/observation (used by `jobagent feedback`)."""
    if _client is None:
        return False
    try:
        _client.create_score(
            trace_id=trace_id,
            observation_id=observation_id,
            name=name,
            value=value,
            data_type=data_type,
            comment=comment,
        )
        return True
    except Exception as e:
        _warn(f"Langfuse create_score failed: {e}")
        return False


def trace_url(trace_id: str | None) -> str | None:
    if _client is None or not trace_id:
        return None
    try:
        return _client.get_trace_url(trace_id=trace_id)
    except Exception:
        return None


def flush() -> None:
    """Send buffered events. Short CLI processes exit before the background thread would."""
    if _client is None:
        return
    try:
        _client.flush()
    except Exception as e:
        _warn(f"Langfuse flush failed: {e}")
