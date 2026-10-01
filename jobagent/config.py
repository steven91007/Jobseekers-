"""Environment-backed settings and the search space (regions x role queries)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Region:
    code: str
    label: str
    linkedin_location: str


REGIONS: dict[str, Region] = {
    "DE": Region("DE", "Germany", "Germany"),
    "NL": Region("NL", "Netherlands", "Netherlands"),
    "IE": Region("IE", "Dublin", "Dublin, Ireland"),
}

# Remote roles open to people living in the EU are kept too, under their own heading.
REMOTE_EU = "REMOTE_EU"
REGION_LABELS = {**{r.code: r.label for r in REGIONS.values()}, REMOTE_EU: "Remote (EU)"}

# LinkedIn keyword searches, one per region. The focus is the "AI Engineer" role
# (building AI/LLM features into products), so most queries are variants of that title;
# ML Engineer is kept as one broader net. Order matters: when LinkedIn starts blocking,
# the circuit breaker skips the searches at the end of the list.
ROLE_QUERIES: list[str] = [
    "AI Engineer",
    "Artificial Intelligence Engineer",
    "Applied AI Engineer",
    "AI Software Engineer",
    "Generative AI Engineer",
    "LLM Engineer",
    "AI Agent Engineer",
    "Machine Learning Engineer",
]

# Extra keyword searches on the German federal job board, where many employers
# post in German ("KI" is German for AI).
GERMAN_QUERIES: list[str] = ["KI Engineer", "KI Entwickler"]

# Search window -> hours. Nothing older than max_age_days (default 7) is ever listed.
SINCE_CHOICES = {"24h": 24, "7d": 168}
DEFAULT_SINCE = "24h"


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    openai_scorer_model: str
    agent_reasoning_effort: str
    scorer_reasoning_effort: str
    agent_web_search: bool
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_base_url: str
    langfuse_user_id: str
    db_path: Path
    reports_dir: Path
    agent_runs_dir: Path
    profile_path: Path
    max_score_per_run: int
    agent_max_turns: int
    linkedin_per_query: int
    linkedin_per_query_24h: int
    max_age_days: int
    freshness_half_life_hours: float
    freshness_weight: float
    english_only: bool
    linkedin_gap_min: float
    linkedin_gap_max: float
    scrape_deadline: float
    ats_workers: int

    @property
    def max_age_hours(self) -> int:
        return max(1, self.max_age_days) * 24

    def per_query(self, since: str) -> int:
        return self.linkedin_per_query_24h if since == "24h" else self.linkedin_per_query

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


def load(env_file: str | Path | None = None) -> Settings:
    load_dotenv(env_file or ROOT / ".env")
    model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-5"
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=model,
        openai_scorer_model=os.getenv("OPENAI_SCORER_MODEL", "").strip() or model,
        agent_reasoning_effort=os.getenv("JOBAGENT_AGENT_REASONING", "medium").strip(),
        scorer_reasoning_effort=os.getenv("JOBAGENT_SCORER_REASONING", "low").strip(),
        agent_web_search=os.getenv("JOBAGENT_AGENT_WEB_SEARCH", "1").strip() != "0",
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
        langfuse_base_url=(
            os.getenv("LANGFUSE_BASE_URL", "").strip()
            or os.getenv("LANGFUSE_HOST", "").strip()
            or "https://cloud.langfuse.com"
        ),
        langfuse_user_id=os.getenv("JOBAGENT_USER_ID", "").strip() or "me",
        db_path=ROOT / os.getenv("JOBAGENT_DB", "data/jobagent.db"),
        reports_dir=ROOT / os.getenv("JOBAGENT_REPORTS_DIR", "reports"),
        agent_runs_dir=ROOT / "data" / "agent_runs",
        # Relative paths are resolved against the repository; absolute ones (and ~) are used as
        # given, so worktrees and other checkouts can share one profile.
        profile_path=ROOT / Path(os.getenv("JOBAGENT_PROFILE", "").strip() or "profile.md").expanduser(),
        max_score_per_run=_int("JOBAGENT_MAX_SCORE_PER_RUN", 40),
        agent_max_turns=_int("JOBAGENT_AGENT_MAX_TURNS", 12),
        linkedin_per_query=_int("JOBAGENT_LINKEDIN_PER_QUERY", 25),
        # A 24h window is small, so fetch deep enough to list every posting in it.
        linkedin_per_query_24h=_int("JOBAGENT_LINKEDIN_PER_QUERY_24H", 100),
        max_age_days=_int("JOBAGENT_MAX_AGE_DAYS", 7),
        freshness_half_life_hours=_float("JOBAGENT_FRESHNESS_HALF_LIFE_HOURS", 24.0),
        freshness_weight=min(1.0, max(0.0, _float("JOBAGENT_FRESHNESS_WEIGHT", 0.3))),
        # Keep only postings written in English that do not require German or Dutch.
        english_only=os.getenv("JOBAGENT_ENGLISH_ONLY", "1").strip() != "0",
        linkedin_gap_min=_float("JOBAGENT_LINKEDIN_GAP_MIN", 2.0),
        linkedin_gap_max=_float("JOBAGENT_LINKEDIN_GAP_MAX", 5.0),
        scrape_deadline=_float("JOBAGENT_SCRAPE_DEADLINE", 90.0),
        ats_workers=_int("JOBAGENT_ATS_WORKERS", 8),
    )
