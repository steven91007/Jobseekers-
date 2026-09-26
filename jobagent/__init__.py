"""AI job agent: collects the newest AI-company jobs in Germany, the Netherlands
and Dublin, scores them against your profile, and writes a ranked digest.

Deterministic core (collect, normalize, dedupe, store, report) always runs.
The LLM layer (scorer + agent loop) turns on when OPENAI_API_KEY is set, and
Langfuse tracing turns on when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set.
"""
