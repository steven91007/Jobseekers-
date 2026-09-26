"""SQLite persistence. The only module that writes SQL.

Deliberately synchronous: these are sub-millisecond local writes on tables with
tens of rows, and everything runs on the single event-loop thread, so calls are
already serialised. Only the network scrape needs to leave the loop.

Never hand a connection to a worker thread — threads do HTTP and return dicts.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id               INTEGER NOT NULL,
    channel_id             INTEGER NOT NULL,
    creator_id             INTEGER NOT NULL,
    keyword                TEXT    NOT NULL,
    location               TEXT    NOT NULL DEFAULT '',
    work_type              TEXT    NOT NULL DEFAULT '',
    job_type               TEXT    NOT NULL DEFAULT '',
    english_only           INTEGER NOT NULL DEFAULT 0,
    visa_check             INTEGER NOT NULL DEFAULT 0,
    max_results            INTEGER NOT NULL DEFAULT 15,
    active                 INTEGER NOT NULL DEFAULT 1,
    deactivated_reason     TEXT,
    deactivated_at         TEXT,
    seeded                 INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT    NOT NULL,
    last_run_at            TEXT,
    last_ok_at             TEXT,
    last_outcome           TEXT,
    last_raw_count         INTEGER NOT NULL DEFAULT 0,
    last_new_count         INTEGER NOT NULL DEFAULT 0,
    consecutive_failures   INTEGER NOT NULL DEFAULT 0,
    consecutive_empty_runs INTEGER NOT NULL DEFAULT 0,
    alert_state            TEXT    NOT NULL DEFAULT 'healthy',
    alerted_at             TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_sub_dedupe ON subscriptions (
    channel_id, lower(keyword), lower(location), work_type, job_type, english_only
);

CREATE TABLE IF NOT EXISTS seen_jobs (
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    job_id          TEXT    NOT NULL,
    first_seen_at   TEXT    NOT NULL,
    PRIMARY KEY (subscription_id, job_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS run_marker (
    run_date        TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    outcome_summary TEXT
);

CREATE TABLE IF NOT EXISTS run_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER,
    ts              TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    raw_count       INTEGER NOT NULL DEFAULT 0,
    new_count       INTEGER NOT NULL DEFAULT 0,
    posted_count    INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS ix_run_log_sub_ts ON run_log (subscription_id, ts DESC);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Subscription:
    id: int
    guild_id: int
    channel_id: int
    creator_id: int
    keyword: str
    location: str
    work_type: str
    job_type: str
    english_only: bool
    visa_check: bool
    max_results: int
    active: bool
    deactivated_reason: str | None
    seeded: bool
    created_at: str
    last_run_at: str | None
    last_ok_at: str | None
    last_outcome: str | None
    last_raw_count: int
    last_new_count: int
    consecutive_failures: int
    consecutive_empty_runs: int
    alert_state: str
    alerted_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Subscription":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            creator_id=row["creator_id"],
            keyword=row["keyword"],
            location=row["location"],
            work_type=row["work_type"],
            job_type=row["job_type"],
            english_only=bool(row["english_only"]),
            visa_check=bool(row["visa_check"]),
            max_results=row["max_results"],
            active=bool(row["active"]),
            deactivated_reason=row["deactivated_reason"],
            seeded=bool(row["seeded"]),
            created_at=row["created_at"],
            last_run_at=row["last_run_at"],
            last_ok_at=row["last_ok_at"],
            last_outcome=row["last_outcome"],
            last_raw_count=row["last_raw_count"],
            last_new_count=row["last_new_count"],
            consecutive_failures=row["consecutive_failures"],
            consecutive_empty_runs=row["consecutive_empty_runs"],
            alert_state=row["alert_state"],
            alerted_at=row["alerted_at"],
        )

    def describe(self) -> str:
        """Human-readable filter summary, e.g. `Python` in Taiwan · Remote · EN"""
        bits = [f"`{self.keyword}`"]
        if self.location:
            bits.append(f"in {self.location}")
        extras = [b for b in (self.work_type, self.job_type) if b]
        if self.english_only:
            extras.append("EN only")
        if self.visa_check:
            extras.append("visa check")
        if extras:
            bits.append("· " + " · ".join(extras))
        return " ".join(bits)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive migrations keyed on meta.schema_version.

    v2: subscriptions.visa_check (per-subscription visa sponsorship check).
    The column is added with ALTER TABLE for databases created at v1; fresh
    databases already get it from SCHEMA.
    """
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    version = int(row["value"]) if row else SCHEMA_VERSION
    if version < 2:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(subscriptions)")}
        if "visa_check" not in cols:
            conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN visa_check INTEGER NOT NULL DEFAULT 0"
            )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


# --- subscriptions --------------------------------------------------------


def add_subscription(
    conn: sqlite3.Connection,
    *,
    guild_id: int,
    channel_id: int,
    creator_id: int,
    keyword: str,
    location: str,
    work_type: str,
    job_type: str,
    english_only: bool,
    max_results: int,
    visa_check: bool = False,
) -> int | None:
    """Returns the new id, or None if an identical subscription already exists."""
    try:
        cur = conn.execute(
            """INSERT INTO subscriptions
               (guild_id, channel_id, creator_id, keyword, location, work_type,
                job_type, english_only, visa_check, max_results, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (guild_id, channel_id, creator_id, keyword, location, work_type,
             job_type, int(english_only), int(visa_check), max_results, _now()),
        )
    except sqlite3.IntegrityError:
        return None
    conn.commit()
    return cur.lastrowid


def get_subscription(conn: sqlite3.Connection, sub_id: int) -> Subscription | None:
    row = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    return Subscription.from_row(row) if row else None


def list_subscriptions(
    conn: sqlite3.Connection,
    *,
    guild_id: int | None = None,
    active_only: bool = False,
) -> list[Subscription]:
    sql = "SELECT * FROM subscriptions WHERE 1=1"
    params: list = []
    if guild_id is not None:
        sql += " AND guild_id=?"
        params.append(guild_id)
    if active_only:
        sql += " AND active=1"
    sql += " ORDER BY id"
    return [Subscription.from_row(r) for r in conn.execute(sql, params)]


def due_subscriptions(conn: sqlite3.Connection) -> list[Subscription]:
    return list_subscriptions(conn, active_only=True)


def remove_subscription(conn: sqlite3.Connection, sub_id: int) -> bool:
    cur = conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
    conn.commit()
    return cur.rowcount > 0


def set_active(
    conn: sqlite3.Connection,
    sub_id: int,
    active: bool,
    reason: str | None = None,
) -> bool:
    cur = conn.execute(
        """UPDATE subscriptions
           SET active=?, deactivated_reason=?, deactivated_at=?
           WHERE id=?""",
        (int(active), None if active else reason, None if active else _now(), sub_id),
    )
    conn.commit()
    return cur.rowcount > 0


def deactivate_guild(conn: sqlite3.Connection, guild_id: int, reason: str) -> int:
    cur = conn.execute(
        """UPDATE subscriptions SET active=0, deactivated_reason=?, deactivated_at=?
           WHERE guild_id=? AND active=1""",
        (reason, _now(), guild_id),
    )
    conn.commit()
    return cur.rowcount


# --- dedupe ---------------------------------------------------------------


def filter_new_job_ids(
    conn: sqlite3.Connection, sub_id: int, job_ids: Iterable[str]
) -> set[str]:
    job_ids = list(job_ids)
    if not job_ids:
        return set()
    placeholders = ",".join("?" * len(job_ids))
    seen = {
        r["job_id"]
        for r in conn.execute(
            f"SELECT job_id FROM seen_jobs WHERE subscription_id=? "
            f"AND job_id IN ({placeholders})",
            [sub_id, *job_ids],
        )
    }
    return set(job_ids) - seen


def mark_seen(conn: sqlite3.Connection, sub_id: int, job_ids: Iterable[str]) -> None:
    rows = [(sub_id, jid, _now()) for jid in job_ids]
    if not rows:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO seen_jobs (subscription_id, job_id, first_seen_at) "
        "VALUES (?,?,?)",
        rows,
    )
    conn.commit()


def prune_seen(conn: sqlite3.Connection, older_than_days: int = 90) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    cur = conn.execute("DELETE FROM seen_jobs WHERE first_seen_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


# --- run bookkeeping ------------------------------------------------------


def finish_run(
    conn: sqlite3.Connection,
    sub_id: int,
    *,
    outcome: str,
    raw_count: int,
    new_count: int,
    seeded: bool | None = None,
    failed: bool = False,
    empty: bool = False,
) -> None:
    now = _now()
    sets = [
        "last_run_at=?", "last_outcome=?", "last_raw_count=?", "last_new_count=?",
        "consecutive_failures=" + ("consecutive_failures+1" if failed else "0"),
        "consecutive_empty_runs=" + ("consecutive_empty_runs+1" if empty else "0"),
    ]
    params: list = [now, outcome, raw_count, new_count]
    if not failed:
        sets.append("last_ok_at=?")
        params.append(now)
    if seeded is not None:
        sets.append("seeded=?")
        params.append(int(seeded))
    params.append(sub_id)
    conn.execute(f"UPDATE subscriptions SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()


def set_alert_state(conn: sqlite3.Connection, sub_id: int, state: str) -> None:
    conn.execute(
        "UPDATE subscriptions SET alert_state=?, alerted_at=? WHERE id=?",
        (state, _now(), sub_id),
    )
    conn.commit()


def log_run(
    conn: sqlite3.Connection,
    *,
    subscription_id: int | None,
    outcome: str,
    raw_count: int = 0,
    new_count: int = 0,
    posted_count: int = 0,
    duration_ms: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO run_log
           (subscription_id, ts, outcome, raw_count, new_count, posted_count,
            duration_ms, error)
           VALUES (?,?,?,?,?,?,?,?)""",
        (subscription_id, _now(), outcome, raw_count, new_count, posted_count,
         duration_ms, error),
    )
    conn.commit()


def prune_run_log(conn: sqlite3.Connection, older_than_days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    cur = conn.execute("DELETE FROM run_log WHERE ts < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


# --- the exactly-once daily marker ---------------------------------------


def claim_run(conn: sqlite3.Connection, run_date: str) -> bool:
    """Claim today's run. False if it was already claimed.

    This unique-constraint claim -- not the timer -- is what makes the daily
    push exactly-once across sleeps, restarts and gateway reconnects.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO run_marker (run_date, started_at) VALUES (?,?)",
        (run_date, _now()),
    )
    conn.commit()
    return cur.rowcount > 0


def finish_marker(conn: sqlite3.Connection, run_date: str, summary: str) -> None:
    conn.execute(
        "UPDATE run_marker SET finished_at=?, outcome_summary=? WHERE run_date=?",
        (_now(), summary, run_date),
    )
    conn.commit()


def last_finished_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM run_marker WHERE finished_at IS NOT NULL "
        "ORDER BY run_date DESC LIMIT 1"
    ).fetchone()
