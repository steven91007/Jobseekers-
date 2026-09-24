"""OpenAI client factory. Uses Langfuse's drop-in wrapper when tracing is live."""

import logging

from .. import observability as obs
from ..config import Settings

log = logging.getLogger(__name__)

# Flipped to False after the first 400 that rejects the `reasoning` parameter,
# so non-reasoning models (e.g. gpt-4.1) keep working without config changes.
_reasoning_supported = True


def get_client(settings: Settings):
    """OpenAI client, or None when OPENAI_API_KEY is not set."""
    if not settings.llm_enabled:
        return None
    if obs.enabled():
        from langfuse.openai import OpenAI  # traces every call as a Langfuse generation
    else:
        from openai import OpenAI
    return OpenAI(api_key=settings.openai_api_key, max_retries=3, timeout=180)


def call(method, *, effort: str = "", trace_name: str | None = None, **kwargs):
    """Call client.responses.create/parse with an optional reasoning effort.

    Retries once without `reasoning` if the model rejects it.
    """
    global _reasoning_supported
    import openai

    if obs.enabled() and trace_name:
        kwargs["name"] = trace_name
    if effort and _reasoning_supported:
        try:
            return method(reasoning={"effort": effort}, **kwargs)
        except openai.BadRequestError as e:
            if "reasoning" not in str(e).lower():
                raise
            log.warning("model rejected `reasoning`; retrying without it")
            _reasoning_supported = False
    return method(**kwargs)


def load_profile(settings: Settings) -> tuple[str, bool]:
    """(profile text, is_real). Falls back to the example template with a warning."""
    if settings.profile_path.exists():
        return settings.profile_path.read_text(encoding="utf-8"), True
    example = settings.profile_path.with_name("profile.example.md")
    if example.exists():
        log.warning("profile.md not found; scoring against profile.example.md. "
                    "Copy it to profile.md and fill in your details.")
        return example.read_text(encoding="utf-8"), False
    return "No profile provided. Assume a mid-level AI engineer.", False
