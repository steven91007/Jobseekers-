"""OpenAI client factory. Uses Langfuse's drop-in wrapper when tracing is live."""

import hashlib
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


class ProfileError(RuntimeError):
    """No usable candidate profile: scoring against it would rank jobs for somebody else."""


# The first instruction line of profile.example.md. A profile that still contains it
# was copied from the template and never filled in.
TEMPLATE_MARKER = "Copy this file to `profile.md` and replace every line with your own details."


def load_profile(settings: Settings) -> str:
    """The candidate profile text. Raises ProfileError when it is missing or still the template.

    There is deliberately no fallback to profile.example.md: scores computed against
    the template describe a fictional candidate and look plausible, so the mistake
    goes unnoticed (it did, for 88 assessments).
    """
    path = settings.profile_path
    if not path.exists():
        raise ProfileError(
            f"no candidate profile at {path}. Copy profile.example.md to profile.md and describe "
            "yourself, or point JOBAGENT_PROFILE at your profile (absolute paths work)."
        )
    text = path.read_text(encoding="utf-8")
    if TEMPLATE_MARKER in text:
        raise ProfileError(
            f"{path} is still the unedited profile.example.md template. Replace it with your own "
            "background, skills, languages and visa situation before scoring."
        )
    if not text.strip():
        raise ProfileError(f"{path} is empty.")
    return text


def profile_fingerprint(text: str) -> str:
    """Short hash stored with each assessment, so scores made with an older profile can be found."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]
