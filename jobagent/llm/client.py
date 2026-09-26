"""OpenAI client factory. Uses Langfuse's drop-in wrapper when tracing is live."""

import logging

from .. import observability as obs
from ..config import Settings

log = logging.getLogger(__name__)

# Flipped to False after the first 400 that rejects them, so models without
# reasoning (e.g. gpt-4.1), or orgs not allowed reasoning summaries, keep working.
_reasoning_supported = True
_summary_supported = True


def get_client(settings: Settings):
    """OpenAI client, or None when OPENAI_API_KEY is not set."""
    if not settings.llm_enabled:
        return None
    if obs.enabled():
        from langfuse.openai import OpenAI  # traces every call as a Langfuse generation
    else:
        from openai import OpenAI
    return OpenAI(api_key=settings.openai_api_key, max_retries=3, timeout=180)


def call(method, *, effort: str = "", trace_name: str | None = None,
         trace_metadata: dict | None = None, **kwargs):
    """Call client.responses.create/parse with an optional reasoning effort.

    Asks for a reasoning summary so Langfuse records the model's thinking on each
    generation. Degrades to effort-only, then to no `reasoning`, if the API refuses.
    `trace_name` / `trace_metadata` go to the Langfuse wrapper only.
    """
    global _reasoning_supported, _summary_supported
    import openai

    if obs.enabled():
        if trace_name:
            kwargs["name"] = trace_name
        if trace_metadata:
            kwargs["metadata"] = trace_metadata
    while effort and _reasoning_supported:
        reasoning = {"effort": effort}
        if _summary_supported:
            reasoning["summary"] = "auto"
        try:
            return method(reasoning=reasoning, **kwargs)
        except openai.BadRequestError as e:
            message = str(e).lower()
            if _summary_supported and "summar" in message:
                log.warning("reasoning summaries rejected; continuing without them")
                _summary_supported = False
            elif "reasoning" in message:
                log.warning("model rejected `reasoning`; retrying without it")
                _reasoning_supported = False
            else:
                raise
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
