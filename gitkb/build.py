"""Build, export, import and reindex. The Markdown notes are the source of
truth; the SQLite rows are always derived by parsing them (`_index_commit_note`
is the only path that writes index rows, for builds and rebuilds alike)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__, db, gitio, hashing, notes
from .config import Config
from .summarize import FIELD_GUIDE, Summarizer, SummaryError, reconcile


@dataclass
class BuildStats:
    summarized: int = 0
    reindexed_from_md: int = 0
    skipped: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


Log = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


# --- indexing from notes ------------------------------------------------------


def _index_commit_note(conn: sqlite3.Connection, path: Path, knowledge_dir: Path) -> str:
    """Parse one commit note (and its change notes) into index rows. Returns git sha."""
    note = notes.parse_note(path.read_text("utf-8"))
    f = note.front
    if f.get("kind") != "commit":
        raise notes.NoteFormatError(f"{path.name}: not a commit note")
    if f["sha256"] != path.stem:
        raise notes.NoteFormatError(f"{path.name}: front matter sha256 does not match filename")

    author_name, author_email = _split_ident(f["author"])
    committer_name, committer_email = _split_ident(f["committer"])
    message = note.sections.get("Commit message", "")
    body = _unfence(message)
    subject = f["subject"]
    body = body[len(subject):].lstrip("\n") if body.startswith(subject) else body

    commit = {
        "git_sha": f["git_sha"],
        "note_sha256": f["sha256"],
        "tree_sha": f["tree_sha"],
        "author_name": author_name,
        "author_email": author_email,
        "authored_at": f["authored_at"],
        "committer_name": committer_name,
        "committer_email": committer_email,
        "committed_at": f["committed_at"],
        "subject": subject,
        "body": body,
        "summary_short": note.sections.get("Summary", ""),
        "why": note.sections.get("Why", ""),
        "risks": note.sections.get("Risks", ""),
        "tags": json.dumps(f.get("tags", []), ensure_ascii=False),
        "model": f["model"],
        "hash_recipe": f["hash_recipe"],
        "truncated": int(bool(f.get("truncated"))),
        "patch_bytes": int(f.get("patch_bytes", 0)),
        "file_count": len(f.get("files", [])),
        "created_at": f["created_at"],
        "indexed_at": _now(),
    }
    parents = [p["git_sha"] for p in f.get("parents", [])]
    links = [
        (f["sha256"], p["note_sha256"], "commit_parent")
        for p in f.get("parents", [])
        if p.get("note_sha256")
    ]
    fts = [
        {
            "sha256": f["sha256"],
            "kind": "commit",
            "git_sha": f["git_sha"],
            "path": None,
            "title": note.title,
            "summary": commit["summary_short"],
            "body": note.body,
        }
    ]
    changes = []
    for pos, entry in enumerate(f.get("files", [])):
        cpath = notes.note_path(knowledge_dir, "change", entry["note_sha256"])
        try:
            cnote = notes.parse_note(cpath.read_text("utf-8"))
        except FileNotFoundError:
            raise notes.NoteFormatError(
                f"{path.name}: change note {entry['note_sha256'][:12]} for {entry['path']} is missing"
            ) from None
        cf = cnote.front
        if cf.get("kind") != "change" or cf.get("git_sha") != f["git_sha"]:
            raise notes.NoteFormatError(f"{cpath.name}: does not belong to commit {f['git_sha'][:7]}")
        changes.append(
            {
                "note_sha256": cf["sha256"],
                "git_sha": f["git_sha"],
                "position": pos,
                "path": cf["path"],
                "old_path": cf.get("old_path"),
                "status": cf["status"],
                "insertions": cf.get("insertions"),
                "deletions": cf.get("deletions"),
                "is_binary": int(bool(cf.get("binary"))),
                "truncated": int(bool(cf.get("truncated"))),
                "truncation_reason": cf.get("truncation_reason"),
                "kind": cf.get("change_kind", "other"),
                "summary": cnote.sections.get("Summary", ""),
                "notable_symbols": json.dumps(cf.get("notable_symbols", []), ensure_ascii=False),
            }
        )
        links.append((f["sha256"], cf["sha256"], "commit_change"))
        fts.append(
            {
                "sha256": cf["sha256"],
                "kind": "change",
                "git_sha": f["git_sha"],
                "path": cf["path"],
                "title": cnote.title,
                "summary": cnote.sections.get("Summary", ""),
                "body": cnote.body,
            }
        )
    db.insert_commit_bundle(conn, commit, parents, changes, links, fts)
    return f["git_sha"]


def _split_ident(ident: str) -> tuple[str, str]:
    name, sep, email = ident.rpartition(" <")
    return (name, email[:-1]) if sep and email.endswith(">") else (ident, "")


def _unfence(section: str) -> str:
    s = section.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.endswith("```"):
            s = s[:-3]
    return s.strip("\n")


def index_from_notes(conn: sqlite3.Connection, knowledge_dir: Path, log: Log = _quiet) -> int:
    """Drop the index and rebuild it from every commit note on disk."""
    db.reset_index(conn)
    paths = sorted((knowledge_dir / "commits").glob("*.md"))
    # Oldest first so parent links resolve; the note carries authored_at.
    paths.sort(key=lambda p: notes.parse_front_matter(p.read_text("utf-8"))[0].get("authored_at", ""))
    n = 0
    for p in paths:
        sha = _index_commit_note(conn, p, knowledge_dir)
        log(f"indexed {sha[:7]} from {p.name[:12]}")
        n += 1
    return n


# --- build --------------------------------------------------------------------


def build(
    repo: Path,
    cfg: Config,
    conn: sqlite3.Connection,
    summarizer: Summarizer,
    *,
    rev: str = "HEAD",
    limit: int | None = None,
    only: set[str] | None = None,
    replace: bool = False,
    log: Log = _quiet,
) -> BuildStats:
    """Summarize unindexed commits oldest-first and index them.

    `only` restricts work to those git shas (used by `import`); `replace`
    re-summarizes commits that already have a note (used to replace dry-run
    placeholders or to update a summary).
    """
    stats = BuildStats()
    recipe = hashing.recipe_string(cfg)
    known = db.get_meta(conn, "hash_recipe")
    if known and known != recipe:
        log(f"warning: hash recipe changed since the index was created; new notes will not "
            f"share hashes with old ones\n  old: {known}\n  new: {recipe}")
    indexed = db.indexed_models(conn)
    processed = 0
    for sha in gitio.rev_list(repo, rev):
        if only is not None and sha not in only:
            continue
        if limit is not None and processed >= limit:
            break
        if sha in indexed and not replace:
            stats.skipped += 1
            continue
        processed += 1
        canon = hashing.canonicalize(repo, sha, cfg)
        cpath = notes.note_path(cfg.knowledge_dir, "commit", canon.commit_sha256)
        if cpath.exists() and not replace:
            _index_commit_note(conn, cpath, cfg.knowledge_dir)
            stats.reindexed_from_md += 1
            log(f"{canon.meta.short} indexed from existing note")
            continue
        try:
            analysis = summarizer.summarize(canon)
        except SummaryError as e:
            stats.failed.append((sha, str(e)))
            log(f"{canon.meta.short} FAILED: {e}")
            continue
        _write_notes(conn, cfg, canon, analysis, summarizer.model_name)
        stats.summarized += 1
        log(f"{canon.meta.short} summarized ({len(canon.per_file)} files) -> {canon.commit_sha256[:12]}")
    with conn:
        db.set_meta(conn, "hash_recipe", recipe)
        db.set_meta(conn, "gitkb_version", __version__)
    return stats


def _write_notes(conn, cfg: Config, canon: hashing.CanonicalCommit, analysis, model: str) -> None:
    file_summaries = reconcile(analysis, canon)
    parent_notes: dict[str, str | None] = {}
    parent_subjects: dict[str, str] = {}
    for p in canon.meta.parents:
        row = db.get_commit(conn, p)
        parent_notes[p] = row.note_sha256 if row else None
        parent_subjects[p] = row.subject if row else gitio.commit_meta(cfg.repo_root, p).subject
    created = _now()
    for pf in canon.per_file:
        notes.write_atomic(
            notes.note_path(cfg.knowledge_dir, "change", pf.sha256),
            notes.render_change_note(canon, pf, file_summaries[pf.change.path], model, created),
        )
    cpath = notes.note_path(cfg.knowledge_dir, "commit", canon.commit_sha256)
    notes.write_atomic(
        cpath,
        notes.render_commit_note(
            canon, analysis, file_summaries, parent_notes, parent_subjects, model, created, __version__
        ),
    )
    _index_commit_note(conn, cpath, cfg.knowledge_dir)


# --- pending export -------------------------------------------------------------


def pending(
    repo: Path,
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    rev: str = "HEAD",
    limit: int | None = None,
    include_placeholders: bool = True,
) -> dict:
    """Canonical inputs of every commit that still needs a real summary."""
    indexed = db.indexed_models(conn)
    items = []
    for sha in gitio.rev_list(repo, rev):
        model = indexed.get(sha)
        if model is not None and not (include_placeholders and model == "dry-run"):
            continue
        canon = hashing.canonicalize(repo, sha, cfg)
        cpath = notes.note_path(cfg.knowledge_dir, "commit", canon.commit_sha256)
        if model is None and cpath.exists():
            continue  # a note exists; `build` will index it without a new summary
        items.append(
            {
                "git_sha": sha,
                "short": canon.meta.short,
                "subject": canon.meta.subject,
                "note_sha256": canon.commit_sha256,
                "replaces_placeholder": model == "dry-run",
                "files": [
                    {
                        "path": pf.change.path,
                        "old_path": pf.change.old_path,
                        "status": pf.change.status,
                        "binary": pf.change.is_binary,
                        "truncated": pf.truncated,
                        "note_sha256": pf.sha256,
                    }
                    for pf in canon.per_file
                ],
                "input": canon.commit_text,
            }
        )
        if limit is not None and len(items) >= limit:
            break
    return {
        "gitkb_version": __version__,
        "hash_recipe": hashing.recipe_string(cfg),
        "field_guide": FIELD_GUIDE,
        "answer_format": {
            "model": "<label for who wrote the summaries, e.g. claude-code/claude-fable-5-1>",
            "commits": {
                "<git_sha>": {
                    "summary": "...", "why": "...", "risks": "...", "tags": ["..."],
                    "files": [{"path": "...", "summary": "...", "kind": "feature", "notable_symbols": ["..."]}],
                }
            },
        },
        "commits": items,
    }
