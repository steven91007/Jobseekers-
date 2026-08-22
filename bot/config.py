"""Environment-backed configuration."""

import os
from dataclasses import dataclass
from datetime import time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    token: str
    dev_guild_id: int | None
    owner_id: int | None
    tz: ZoneInfo
    push_time: dt_time
    db_path: Path
    log_dir: Path
    max_new_per_push: int
    gap_min: float
    gap_max: float
    scrape_deadline: float
    scrape_timeout: float
    seen_retention_days: int
    log_level: str


def _int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None


def _push_time(raw: str, tz: ZoneInfo) -> dt_time:
    try:
        hour, minute = (int(part) for part in raw.strip().split(":", 1))
        return dt_time(hour=hour, minute=minute, tzinfo=tz)
    except ValueError:
        raise ConfigError(
            f"JOBBOT_PUSH_TIME must look like HH:MM (24h), got {raw!r}"
        ) from None


def load(env_file: str | Path = ".env") -> Config:
    load_dotenv(env_file)

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in "
            "(Discord Developer Portal -> your App -> Bot -> Reset Token)."
        )

    tz_name = os.getenv("JOBBOT_TZ", "Asia/Taipei").strip() or "Asia/Taipei"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        # Almost always a missing tzdata on Windows rather than a typo.
        raise ConfigError(
            f"Could not load timezone {tz_name!r}. On Windows this usually means "
            "the tzdata package is missing: pip install tzdata"
        ) from None

    deadline = _float("JOBBOT_SCRAPE_DEADLINE", 120.0)
    timeout = _float("JOBBOT_SCRAPE_TIMEOUT", 180.0)
    if deadline >= timeout:
        raise ConfigError(
            "JOBBOT_SCRAPE_DEADLINE must be smaller than JOBBOT_SCRAPE_TIMEOUT — "
            "the in-thread deadline has to fire first, because the outer timeout "
            "cannot cancel the thread."
        )

    gap_min = _float("JOBBOT_GAP_MIN", 8.0)
    gap_max = _float("JOBBOT_GAP_MAX", 20.0)
    if gap_min > gap_max:
        raise ConfigError("JOBBOT_GAP_MIN must be <= JOBBOT_GAP_MAX")

    retention = _int("JOBBOT_SEEN_RETENTION_DAYS", 90)
    if retention < 60:
        raise ConfigError(
            "JOBBOT_SEEN_RETENTION_DAYS below 60 causes re-posts — LinkedIn "
            "resurfaces older listings and they would read as new."
        )

    return Config(
        token=token,
        dev_guild_id=_int("DISCORD_DEV_GUILD_ID"),
        owner_id=_int("JOBBOT_OWNER_ID"),
        tz=tz,
        push_time=_push_time(os.getenv("JOBBOT_PUSH_TIME", "09:00"), tz),
        db_path=Path(os.getenv("JOBBOT_DB", "data/jobs.db")),
        log_dir=Path(os.getenv("JOBBOT_LOG_DIR", "logs")),
        max_new_per_push=_int("JOBBOT_MAX_NEW_PER_PUSH", 10),
        gap_min=gap_min,
        gap_max=gap_max,
        scrape_deadline=deadline,
        scrape_timeout=timeout,
        seen_retention_days=retention,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
