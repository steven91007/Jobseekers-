"""Langfuse tracing, as an optional layer that can never break a run.

When LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are unset, or the SDK fails to
start, every helper here becomes a no-op. The OpenAI client in llm/client.py is
swapped for Langfuse's drop-in wrapper only when tracing is live, so every
scorer and agent call appears as a *generation* with tokens and cost.

Trace shape for one `jobagent run`:

    run  (agent-type root; session = run date, tags = jobagent)
    ├── collect
    │   ├── collect.linkedin DE / "AI Engineer"      (per search)
    │   └── collect.greenhouse anthropic              (per company)
    ├── store                                         (raw -> kept -> new)
    ├── score
    │   └── score linkedin:1234  (+ scores fit_score, apply_priority)
    │       └── OpenAI responses generation
    ├── agent
    │   ├── OpenAI responses generation (per turn)
    │   └── tool.search_linkedin / tool.web_search ... (per tool call)
    └── report
"""

import logging
import os
from contextlib import contextmanager
from functools import wraps
from typing import Any, Iterator

from .config import Settings

log = logging.getLogger(__name__)

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
            environment=os.getenv("JOBAGENT_ENV", "local"),
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


def traced(name: str | None = None, *, as_type: str = "span"):
    """Decorator form of span(); records arguments as input but not the return value."""

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            with span(name or fn.__name__, as_type=as_type, input=kwargs or None):
                return fn(*args, **kwargs)

        return wrapper

    return deco


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
