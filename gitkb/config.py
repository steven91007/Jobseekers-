"""Environment-backed configuration (same shape as bot/config.py)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv is in requirements.txt, but the tool must not die without it
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover

    def load_dotenv(*_a, **_k) -> bool:
        return False


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    repo_root: Path
    knowledge_dir: Path
    per_file_cap_bytes: int
    total_cap_bytes: int
    exclude_globs: tuple[str, ...]

    @property
    def db_path(self) -> Path:
        return self.knowledge_dir / "index.db"

    @property
    def pending_path(self) -> Path:
        return self.knowledge_dir / "pending.json"


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def _str(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def load(
    repo_root: Path,
    env_file: str | Path = ".env",
    *,
    knowledge_dir: str | Path | None = None,
) -> Config:
    env_path = Path(env_file)
    if not env_path.is_absolute():
        env_path = repo_root / env_path
    load_dotenv(env_path)

    kdir = Path(knowledge_dir) if knowledge_dir else Path(_str("GITKB_KNOWLEDGE_DIR", "knowledge"))
    if not kdir.is_absolute():
        kdir = repo_root / kdir

    per_file = _int("GITKB_PER_FILE_CAP_BYTES", 65536)
    total = _int("GITKB_TOTAL_CAP_BYTES", 400000)
    if per_file <= 0 or total <= 0:
        raise ConfigError("GITKB_PER_FILE_CAP_BYTES and GITKB_TOTAL_CAP_BYTES must be positive")

    excludes = tuple(
        g.strip() for g in _str("GITKB_EXCLUDE_GLOBS", "knowledge/**").split(",") if g.strip()
    )

    return Config(
        repo_root=repo_root,
        knowledge_dir=kdir,
        per_file_cap_bytes=per_file,
        total_cap_bytes=total,
        exclude_globs=excludes,
    )
