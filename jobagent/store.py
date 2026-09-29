"""SQLite persistence for jobs, assessments, runs and company candidates."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz

from .models import Assessment, Job
from .normalize import company_key, title_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_key           TEXT PRIMARY KEY,
    source            TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    title             TEXT NOT NULL,
    company           TEXT NOT NULL,
    company_key       TEXT NOT NULL,
    title_key         TEXT NOT NULL,
    company_tier      TEXT NOT NULL DEFAULT 'other',
    location          TEXT NOT NULL DEFAULT '',
    region            TEXT NOT NULL,
    url               TEXT NOT NULL,
    posted_at         TEXT NOT NULL DEFAULT '',
    work_type         TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    ats_slug          TEXT NOT NULL DEFAULT '',
    extra             TEXT NOT NULL DEFAULT '{}',
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    first_seen_run_id INTEGER,
    last_seen_run_id  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_jobs_company_region ON jobs (company_key, region);
CREATE INDEX IF NOT EXISTS ix_jobs_posted ON jobs (posted_at DESC);

CREATE TABLE IF NOT EXISTS assessments (
    job_key         TEXT PRIMARY KEY REFERENCES jobs(job_key) ON DELETE CASCADE,
    fit_score       INTEGER NOT NULL,
    apply_priority  TEXT NOT NULL,
    data            TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    trace_id        TEXT,
    observation_id  TEXT,
    created_at      TEXT NOT NULL,
    profile_sha     TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    since       TEXT NOT NULL,
    stats       TEXT NOT NULL DEFAULT '{}',
    trace_id    TEXT,
    report_path TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key    TEXT NOT NULL,
    label      TEXT NOT NULL,
    comment    TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_candidates (
    name_key     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    careers_url  TEXT NOT NULL DEFAULT '',
    ats          TEXT NOT NULL DEFAULT '',
    slug         TEXT NOT NULL DEFAULT '',
    regions      TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    suggested_at TEXT NOT NULL,
    run_id       INTEGER,
    status       TEXT NOT NULL DEFAULT 'pending'
);
"""

# A LinkedIn copy of an ATS posting (same company + region) is a duplicate when
# the titles are at least this similar. token_sort_ratio ignores word order;
# gender tags and city names are stripped from title_key first.
TITLE_DUP_THRESHOLD = 90
SOURCE_PREFERENCE = {"greenhouse": 0, "ashby": 0, "lever": 0, "linkedin": 1}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was created."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(assessments)")}
    if "profile_sha" not in columns:
        # NULL marks assessments made before profiles were fingerprinted.
        conn.execute("ALTER TABLE assessments ADD COLUMN profile_sha TEXT")


# --- runs ------------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, since: str) -> int:
    cur = conn.execute("INSERT INTO runs (started_at, since) VALUES (?, ?)", (_now(), since))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, stats: dict, trace_id: str | None, report_path: str | None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, stats=?, trace_id=?, report_path=? WHERE id=?",
        (_now(), json.dumps(stats), trace_id, report_path, run_id),
    )
    conn.commit()


def last_run(conn) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()


# --- jobs ------------------------------------------------------------------------


def dedupe_batch(jobs: list[Job]) -> list[Job]:
    """Collapse duplicates within one collection pass, preferring ATS over LinkedIn."""
    by_key: dict[str, Job] = {}
    for job in jobs:
        by_key.setdefault(job.job_key, job)
    ordered = sorted(by_key.values(), key=lambda j: SOURCE_PREFERENCE.get(j.source, 2))
    kept: list[Job] = []
    buckets: dict[tuple[str, str], list[Job]] = {}
    for job in ordered:
        bucket = buckets.setdefault((company_key(job.company), job.region), [])
        tk = title_key(job.title)
        if any(fuzz.token_sort_ratio(tk, title_key(o.title)) >= TITLE_DUP_THRESHOLD for o in bucket):
            continue
        bucket.append(job)
        kept.append(job)
    return kept


def _find_duplicate(conn, job: Job) -> str | None:
    """job_key of an already-stored posting that is the same job from another source."""
    tk = title_key(job.title)
    for row in conn.execute(
        "SELECT job_key, title_key FROM jobs WHERE company_key=? AND region=? AND job_key<>?",
        (company_key(job.company), job.region, job.job_key),
    ):
        if fuzz.token_sort_ratio(tk, row["title_key"]) >= TITLE_DUP_THRESHOLD:
            return row["job_key"]
    return None


def upsert_jobs(conn, jobs: list[Job], run_id: int) -> list[str]:
    """Insert unseen jobs, refresh seen ones. Returns job_keys that are new this run."""
    now = _now()
    new_keys: list[str] = []
    for job in jobs:
        existing = conn.execute("SELECT job_key FROM jobs WHERE job_key=?", (job.job_key,)).fetchone()
        if existing is None:
            dup = _find_duplicate(conn, job)
            if dup:
                conn.execute("UPDATE jobs SET last_seen_at=?, last_seen_run_id=? WHERE job_key=?",
                             (now, run_id, dup))
                continue
            conn.execute(
                """INSERT INTO jobs (job_key, source, source_id, title, company, company_key,
                   title_key, company_tier, location, region, url, posted_at, work_type,
                   description, ats_slug, extra, first_seen_at, last_seen_at,
                   first_seen_run_id, last_seen_run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job.job_key, job.source, job.source_id, job.title, job.company,
                 company_key(job.company), title_key(job.title), job.company_tier,
                 job.location, job.region, job.url, job.posted_at, job.work_type,
                 job.description, job.ats_slug, json.dumps(job.extra), now, now, run_id, run_id),
            )
            new_keys.append(job.job_key)
        else:
            conn.execute(
                """UPDATE jobs SET last_seen_at=?, last_seen_run_id=?, title=?, location=?,
                   posted_at=COALESCE(NULLIF(?, ''), posted_at),
                   description=CASE WHEN ?<>'' THEN ? ELSE description END
                   WHERE job_key=?""",
                (now, run_id, job.title, job.location, job.posted_at,
                 job.description, job.description, job.job_key),
            )
    conn.commit()
    return new_keys


def row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        source=row["source"], source_id=row["source_id"], title=row["title"],
        company=row["company"], location=row["location"], region=row["region"],
        url=row["url"], posted_at=row["posted_at"], work_type=row["work_type"],
        description=row["description"], company_tier=row["company_tier"],
        ats_slug=row["ats_slug"], extra=json.loads(row["extra"] or "{}"),
    )


def get_job(conn, job_key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE job_key=?", (job_key,)).fetchone()


def save_description(conn, job_key: str, description: str, extra: dict | None = None) -> None:
    if extra is None:
        conn.execute("UPDATE jobs SET description=? WHERE job_key=?", (description, job_key))
    else:
        conn.execute("UPDATE jobs SET description=?, extra=? WHERE job_key=?",
                     (description, json.dumps(extra), job_key))
    conn.commit()


def open_jobs(conn, run_id: int | None = None) -> list[sqlite3.Row]:
    """Jobs seen in the given run (default: latest run), joined with any assessment."""
    if run_id is None:
        last = last_run(conn)
        run_id = last["id"] if last else 0
    return conn.execute(
        """SELECT j.*, a.fit_score, a.apply_priority, a.data AS assessment,
                  a.trace_id AS a_trace_id,
                  (j.first_seen_run_id = ?) AS is_new
           FROM jobs j LEFT JOIN assessments a ON a.job_key = j.job_key
           WHERE j.last_seen_run_id = ?""",
        (run_id, run_id),
    ).fetchall()


def unassessed_keys(conn, run_id: int, limit: int) -> list[str]:
    """Jobs from this run without an assessment, AI-native first, newest first."""
    rows = conn.execute(
        """SELECT j.job_key FROM jobs j LEFT JOIN assessments a ON a.job_key = j.job_key
           WHERE j.last_seen_run_id = ? AND a.job_key IS NULL
           ORDER BY CASE j.company_tier WHEN 'ai_native' THEN 0 WHEN 'ai_heavy' THEN 1 ELSE 2 END,
                    substr(j.posted_at, 1, 10) DESC
           LIMIT ?""",
        (run_id, limit),
    ).fetchall()
    return [r["job_key"] for r in rows]


# --- assessments -------------------------------------------------------------------


def save_assessment(conn, job_key: str, a: Assessment, *, model: str, prompt_version: str,
                    trace_id: str | None, observation_id: str | None,
                    profile_sha: str | None = None) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO assessments
           (job_key, fit_score, apply_priority, data, model, prompt_version,
            trace_id, observation_id, created_at, profile_sha)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (job_key, a.fit_score, a.apply_priority, a.model_dump_json(), model,
         prompt_version, trace_id, observation_id, _now(), profile_sha),
    )
    conn.commit()


def stale_assessment_keys(conn, profile_sha: str, limit: int | None = None) -> list[str]:
    """Assessed jobs whose score was made with another profile (or before fingerprints existed)."""
    sql = """SELECT a.job_key FROM assessments a JOIN jobs j ON j.job_key = a.job_key
             WHERE a.profile_sha IS NULL OR a.profile_sha <> ?
             ORDER BY substr(j.posted_at, 1, 10) DESC, a.job_key"""
    rows = conn.execute(sql + (" LIMIT ?" if limit else ""), (profile_sha, limit) if limit else (profile_sha,))
    return [r["job_key"] for r in rows.fetchall()]


def get_assessment(conn, job_key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM assessments WHERE job_key=?", (job_key,)).fetchone()


# --- company candidates ---------------------------------------------------------------


def add_candidate(conn, *, name: str, careers_url: str, ats: str, slug: str, regions: str,
                  reason: str, run_id: int | None) -> bool:
    cur = conn.execute(
        """INSERT OR IGNORE INTO company_candidates
           (name_key, name, careers_url, ats, slug, regions, reason, suggested_at, run_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (company_key(name) or name.lower(), name, careers_url, ats, slug, regions, reason,
         _now(), run_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_candidates(conn, status: str | None = "pending") -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM company_candidates WHERE status=? ORDER BY suggested_at DESC", (status,)
        ).fetchall()
    return conn.execute("SELECT * FROM company_candidates ORDER BY suggested_at DESC").fetchall()


# --- feedback --------------------------------------------------------------------------


def add_feedback(conn, job_key: str, label: str, comment: str | None) -> None:
    conn.execute("INSERT INTO feedback (job_key, label, comment, created_at) VALUES (?,?,?,?)",
                 (job_key, label, comment, _now()))
    conn.commit()
