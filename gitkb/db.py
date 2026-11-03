"""SQLite index over the notes. Rebuildable from the Markdown files at any time.

Same conventions as bot/db.py: synchronous, one writer, WAL, Row factory.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS commits (
    git_sha         TEXT PRIMARY KEY,
    note_sha256     TEXT NOT NULL UNIQUE,
    tree_sha        TEXT NOT NULL,
    author_name     TEXT NOT NULL,
    author_email    TEXT NOT NULL,
    authored_at     TEXT NOT NULL,
    committer_name  TEXT NOT NULL,
    committer_email TEXT NOT NULL,
    committed_at    TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    summary_short   TEXT NOT NULL,
    why             TEXT NOT NULL DEFAULT '',
    risks           TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',
    model           TEXT NOT NULL,
    hash_recipe     TEXT NOT NULL,
    truncated       INTEGER NOT NULL DEFAULT 0,
    patch_bytes     INTEGER NOT NULL,
    file_count      INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    indexed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_commits_authored ON commits (authored_at);

CREATE TABLE IF NOT EXISTS commit_parents (
    git_sha    TEXT NOT NULL REFERENCES commits(git_sha) ON DELETE CASCADE,
    parent_sha TEXT NOT NULL,
    position   INTEGER NOT NULL,
    PRIMARY KEY (git_sha, position)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_parents_parent ON commit_parents (parent_sha);

CREATE TABLE IF NOT EXISTS changes (
    note_sha256       TEXT PRIMARY KEY,
    git_sha           TEXT NOT NULL REFERENCES commits(git_sha) ON DELETE CASCADE,
    position          INTEGER NOT NULL,
    path              TEXT NOT NULL,
    old_path          TEXT,
    status            TEXT NOT NULL CHECK (status IN ('A','M','D','R','C','T')),
    insertions        INTEGER,
    deletions         INTEGER,
    is_binary         INTEGER NOT NULL DEFAULT 0,
    truncated         INTEGER NOT NULL DEFAULT 0,
    truncation_reason TEXT,
    kind              TEXT NOT NULL,
    summary           TEXT NOT NULL,
    notable_symbols   TEXT NOT NULL DEFAULT '[]',
    UNIQUE (git_sha, path)
);
CREATE INDEX IF NOT EXISTS ix_changes_path ON changes (path);
CREATE INDEX IF NOT EXISTS ix_changes_commit ON changes (git_sha, position);

CREATE TABLE IF NOT EXISTS links (
    from_sha256 TEXT NOT NULL,
    to_sha256   TEXT NOT NULL,
    relation    TEXT NOT NULL CHECK (relation IN ('commit_change','commit_parent')),
    PRIMARY KEY (from_sha256, to_sha256, relation)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_links_to ON links (to_sha256);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    sha256 UNINDEXED, kind UNINDEXED, git_sha UNINDEXED, path UNINDEXED,
    title, summary, body,
    tokenize = 'porter unicode61'
);
"""


class DbError(RuntimeError):
    pass


@dataclass
class CommitRow:
    git_sha: str
    note_sha256: str
    authored_at: str
    author_name: str
    subject: str
    summary_short: str
    why: str
    risks: str
    tags: list[str]
    model: str
    hash_recipe: str
    truncated: bool
    file_count: int
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CommitRow":
        return cls(
            git_sha=row["git_sha"],
            note_sha256=row["note_sha256"],
            authored_at=row["authored_at"],
            author_name=row["author_name"],
            subject=row["subject"],
            summary_short=row["summary_short"],
            why=row["why"],
            risks=row["risks"],
            tags=json.loads(row["tags"]),
            model=row["model"],
            hash_recipe=row["hash_recipe"],
            truncated=bool(row["truncated"]),
            file_count=row["file_count"],
            created_at=row["created_at"],
        )


@dataclass
class ChangeRow:
    note_sha256: str
    git_sha: str
    position: int
    path: str
    old_path: str | None
    status: str
    insertions: int | None
    deletions: int | None
    is_binary: bool
    truncated: bool
    truncation_reason: str | None
    kind: str
    summary: str
    notable_symbols: list[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ChangeRow":
        return cls(
            note_sha256=row["note_sha256"],
            git_sha=row["git_sha"],
            position=row["position"],
            path=row["path"],
            old_path=row["old_path"],
            status=row["status"],
            insertions=row["insertions"],
            deletions=row["deletions"],
            is_binary=bool(row["is_binary"]),
            truncated=bool(row["truncated"]),
            truncation_reason=row["truncation_reason"],
            kind=row["kind"],
            summary=row["summary"],
            notable_symbols=json.loads(row["notable_symbols"]),
        )


@dataclass
class SearchHit:
    sha256: str
    kind: str
    git_sha: str
    path: str | None
    title: str
    snippet: str


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as e:
        if "fts5" in str(e).lower():
            raise DbError(
                "this Python's SQLite lacks the FTS5 extension needed for `search`; "
                "use a Python build linked against a full SQLite"
            ) from None
        raise
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
    conn.commit()
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def indexed_models(conn: sqlite3.Connection) -> dict[str, str]:
    """git_sha -> model label for every indexed commit."""
    return {r["git_sha"]: r["model"] for r in conn.execute("SELECT git_sha, model FROM commits")}


def insert_commit_bundle(
    conn: sqlite3.Connection,
    commit: dict,
    parents: list[str],
    changes: list[dict],
    links: list[tuple[str, str, str]],
    fts_rows: list[dict],
) -> None:
    """Replace everything known about one commit in a single transaction."""
    git_sha = commit["git_sha"]
    with conn:
        conn.execute("DELETE FROM notes_fts WHERE git_sha=?", (git_sha,))
        old = conn.execute("SELECT note_sha256 FROM commits WHERE git_sha=?", (git_sha,)).fetchone()
        if old:
            conn.execute("DELETE FROM links WHERE from_sha256=?", (old["note_sha256"],))
        conn.execute("DELETE FROM commits WHERE git_sha=?", (git_sha,))  # cascades
        cols = ", ".join(commit)
        conn.execute(
            f"INSERT INTO commits ({cols}) VALUES ({', '.join('?' for _ in commit)})",
            tuple(commit.values()),
        )
        conn.executemany(
            "INSERT INTO commit_parents (git_sha, parent_sha, position) VALUES (?, ?, ?)",
            [(git_sha, p, i) for i, p in enumerate(parents)],
        )
        for ch in changes:
            cols = ", ".join(ch)
            conn.execute(
                f"INSERT INTO changes ({cols}) VALUES ({', '.join('?' for _ in ch)})",
                tuple(ch.values()),
            )
        conn.executemany(
            "INSERT OR IGNORE INTO links (from_sha256, to_sha256, relation) VALUES (?, ?, ?)", links
        )
        conn.executemany(
            "INSERT INTO notes_fts (sha256, kind, git_sha, path, title, summary, body) "
            "VALUES (:sha256, :kind, :git_sha, :path, :title, :summary, :body)",
            fts_rows,
        )


def get_commit(conn: sqlite3.Connection, sha_or_prefix: str) -> CommitRow | None:
    rows = conn.execute(
        "SELECT * FROM commits WHERE git_sha LIKE ? ORDER BY git_sha", (sha_or_prefix + "%",)
    ).fetchall()
    if len(rows) > 1:
        raise DbError(f"ambiguous prefix {sha_or_prefix!r}: {len(rows)} commits match")
    return CommitRow.from_row(rows[0]) if rows else None


def get_commit_by_note(conn: sqlite3.Connection, sha256: str) -> CommitRow | None:
    row = conn.execute("SELECT * FROM commits WHERE note_sha256=?", (sha256,)).fetchone()
    return CommitRow.from_row(row) if row else None


def get_change(conn: sqlite3.Connection, sha256: str) -> ChangeRow | None:
    row = conn.execute("SELECT * FROM changes WHERE note_sha256=?", (sha256,)).fetchone()
    return ChangeRow.from_row(row) if row else None


def changes_for_commit(conn: sqlite3.Connection, git_sha: str) -> list[ChangeRow]:
    rows = conn.execute(
        "SELECT * FROM changes WHERE git_sha=? ORDER BY position", (git_sha,)
    ).fetchall()
    return [ChangeRow.from_row(r) for r in rows]


def changes_for_path(conn: sqlite3.Connection, path: str) -> list[ChangeRow]:
    rows = conn.execute(
        "SELECT c.* FROM changes c JOIN commits k ON k.git_sha = c.git_sha "
        "WHERE c.path=? OR c.old_path=? ORDER BY k.authored_at",
        (path, path),
    ).fetchall()
    return [ChangeRow.from_row(r) for r in rows]


def links_from(conn: sqlite3.Connection, sha256: str) -> list[tuple[str, str]]:
    return [
        (r["to_sha256"], r["relation"])
        for r in conn.execute("SELECT to_sha256, relation FROM links WHERE from_sha256=?", (sha256,))
    ]


def links_to(conn: sqlite3.Connection, sha256: str) -> list[tuple[str, str]]:
    return [
        (r["from_sha256"], r["relation"])
        for r in conn.execute("SELECT from_sha256, relation FROM links WHERE to_sha256=?", (sha256,))
    ]


def search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[SearchHit]:
    try:
        rows = conn.execute(
            "SELECT sha256, kind, git_sha, path, title, "
            "snippet(notes_fts, -1, '[', ']', '...', 14) AS snip "
            "FROM notes_fts WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?",
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError as e:
        raise DbError(f"bad search query: {e}") from None
    return [SearchHit(r["sha256"], r["kind"], r["git_sha"], r["path"], r["title"], r["snip"]) for r in rows]


def list_commits(conn: sqlite3.Connection, limit: int | None = None) -> list[CommitRow]:
    sql = "SELECT * FROM commits ORDER BY authored_at DESC, git_sha"
    rows = conn.execute(sql + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    return [CommitRow.from_row(r) for r in rows]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("commits", "changes", "links", "commit_parents", "notes_fts")
    }


def reset_index(conn: sqlite3.Connection) -> None:
    with conn:
        for t in ("notes_fts", "links", "changes", "commit_parents", "commits"):
            conn.execute(f"DELETE FROM {t}")
