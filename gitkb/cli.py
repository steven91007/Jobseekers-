"""`python -m gitkb` command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__, build as build_mod, db, gitio, notes
from .config import Config, ConfigError, load
from .summarize import DrySummarizer, MappingSummarizer, SummaryError, load_summaries

HEX64 = 64


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m gitkb",
        description="Knowledge base of git history: one sha256-named Markdown note per commit "
        "and per file change, indexed in SQLite.",
    )
    p.add_argument("--repo", type=Path, default=None, help="path inside the repository (default: cwd)")
    p.add_argument("--knowledge-dir", type=Path, default=None, help="override GITKB_KNOWLEDGE_DIR")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"gitkb {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("pending", help="export canonical inputs of commits that still need a summary")
    s.add_argument("--out", type=Path, default=None, help="default: <knowledge>/pending.json")
    s.add_argument("--rev", default="HEAD")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--skip-placeholders", action="store_true",
                   help="do not re-export commits that only have dry-run placeholder notes")

    s = sub.add_parser("import", help="turn a summaries JSON file into notes and index them")
    s.add_argument("file", type=Path)
    s.add_argument("--model", default=None, help="label recorded in the notes (default: from file or 'claude-code')")
    s.add_argument("--rev", default="HEAD")

    s = sub.add_parser("build", help="index existing notes; with --dry-run write placeholder notes")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--rev", default="HEAD")
    s.add_argument("--limit", type=int, default=None)

    sub.add_parser("rebuild-index", help="drop the SQLite index and rebuild it from the notes")

    s = sub.add_parser("show", help="print a note by git sha (prefix ok) or note sha256")
    s.add_argument("id")

    s = sub.add_parser("search", help="full-text search over summaries (FTS5 syntax)")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=20)

    s = sub.add_parser("log", help="list indexed commits, newest first")
    s.add_argument("--limit", type=int, default=None)

    s = sub.add_parser("history", help="list every indexed change to one file path")
    s.add_argument("path")
    return p


def _setup(args) -> tuple[Config, Path]:
    start = args.repo or Path.cwd()
    root = gitio.repo_root(start)
    cfg = load(root, knowledge_dir=args.knowledge_dir)
    return cfg, root


def _log(args):
    return print if args.verbose or args.cmd in ("build", "import", "rebuild-index") else (lambda _m: None)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(message)s")
    try:
        return _dispatch(args)
    except (ConfigError, gitio.GitError, db.DbError, SummaryError, notes.NoteFormatError) as e:
        print(f"gitkb: {e}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    cfg, root = _setup(args)
    log = _log(args)

    if args.cmd == "pending":
        conn = db.connect(cfg.db_path)
        data = build_mod.pending(
            root, cfg, conn, rev=args.rev, limit=args.limit,
            include_placeholders=not args.skip_placeholders,
        )
        out = args.out or cfg.pending_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        n = len(data["commits"])
        print(f"{n} commit(s) need a summary -> {out}")
        for c in data["commits"]:
            flag = " (placeholder)" if c["replaces_placeholder"] else ""
            print(f"  {c['short']} {c['subject']}  [{len(c['files'])} files]{flag}")
        return 0

    if args.cmd == "import":
        analyses, file_model = load_summaries(args.file)
        model = args.model or file_model or "claude-code"
        conn = db.connect(cfg.db_path)
        stats = build_mod.build(
            root, cfg, conn, MappingSummarizer(analyses, model),
            rev=args.rev, only=set(analyses), replace=True, log=log,
        )
        return _report(stats, conn)

    if args.cmd == "build":
        conn = db.connect(cfg.db_path)
        summarizer = DrySummarizer() if args.dry_run else _NoSummarizer()
        stats = build_mod.build(root, cfg, conn, summarizer, rev=args.rev, limit=args.limit, log=log)
        return _report(stats, conn)

    if args.cmd == "rebuild-index":
        if cfg.db_path.exists():
            cfg.db_path.unlink()
            for suffix in ("-wal", "-shm"):
                p = Path(str(cfg.db_path) + suffix)
                if p.exists():
                    p.unlink()
        conn = db.connect(cfg.db_path)
        n = build_mod.index_from_notes(conn, cfg.knowledge_dir, log=log)
        print(f"rebuilt index from {n} commit note(s): {db.counts(conn)}")
        return 0

    if args.cmd == "show":
        conn = db.connect(cfg.db_path)
        path = _resolve(conn, cfg, args.id)
        if path is None:
            print(f"gitkb: nothing indexed matches {args.id!r}", file=sys.stderr)
            return 1
        print(path.read_text("utf-8"), end="")
        return 0

    if args.cmd == "search":
        conn = db.connect(cfg.db_path)
        hits = db.search(conn, args.query, args.limit)
        for h in hits:
            where = f"{h.git_sha[:7]}" + (f" {h.path}" if h.path else "")
            print(f"{h.kind:6} {h.sha256[:12]}  {where}\n       {h.title}\n       {h.snippet}")
        if not hits:
            print("no matches")
        return 0

    if args.cmd == "log":
        conn = db.connect(cfg.db_path)
        for r in db.list_commits(conn, args.limit):
            flag = "" if r.model != "dry-run" else "  [placeholder]"
            print(f"{r.git_sha[:7]}  {r.authored_at[:10]}  {r.note_sha256[:12]}  {r.file_count:3} files  {r.subject}{flag}")
        return 0

    if args.cmd == "history":
        conn = db.connect(cfg.db_path)
        rows = db.changes_for_path(conn, args.path)
        for ch in rows:
            print(f"{ch.git_sha[:7]}  {ch.status}  {ch.kind:8}  {ch.note_sha256[:12]}  {ch.summary.splitlines()[0] if ch.summary else ''}")
        if not rows:
            print("no indexed changes for that path")
        return 0

    raise AssertionError(args.cmd)


class _NoSummarizer:
    """`build` without --dry-run only indexes notes that already exist."""

    model_name = "none"

    def summarize(self, canon):
        raise SummaryError(
            f"no note yet for {canon.meta.short}; run `python -m gitkb pending`, write the "
            "summaries with Claude Code (/gitkb), then `python -m gitkb import <file>`"
        )


def _report(stats: build_mod.BuildStats, conn) -> int:
    print(
        f"summarized {stats.summarized}, indexed from existing notes {stats.reindexed_from_md}, "
        f"skipped {stats.skipped}, failed {len(stats.failed)}; index now {db.counts(conn)}"
    )
    for sha, why in stats.failed:
        print(f"  {sha[:7]}: {why}")
    return 1 if stats.failed else 0


def _resolve(conn, cfg: Config, ident: str) -> Path | None:
    ident = ident.strip().lower()
    if len(ident) == HEX64:
        if db.get_commit_by_note(conn, ident):
            return notes.note_path(cfg.knowledge_dir, "commit", ident)
        if db.get_change(conn, ident):
            return notes.note_path(cfg.knowledge_dir, "change", ident)
        return None
    row = db.get_commit(conn, ident)
    return notes.note_path(cfg.knowledge_dir, "commit", row.note_sha256) if row else None
