"""Summary data model and the summarizers that fill it.

Summaries are written by Claude Code in an interactive session (see
`.claude/commands/gitkb.md`): `python -m gitkb pending` exports the canonical
text of every unsummarized commit, Claude Code writes a summaries JSON file,
and `python -m gitkb import` turns it into notes. No API calls happen here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .hashing import CanonicalCommit

log = logging.getLogger("gitkb.summarize")

FILE_KINDS = ("feature", "bugfix", "refactor", "docs", "config", "test", "chore", "other")

# Shape of one entry in the summaries file, keyed by full git sha:
#   {"summary": str, "why": str, "risks": str, "tags": [str],
#    "files": [{"path": str, "summary": str, "kind": FILE_KINDS, "notable_symbols": [str]}]}
FIELD_GUIDE = """\
summary: two to four sentences on what the commit changes.
why: the motivation as far as the diff and message reveal it; say so if not evident.
risks: behavioural changes, possible regressions, follow-ups; or "none identified".
tags: a few short lowercase topic words.
files: exactly one entry per file listed for the commit, using the same path.
  summary: what changed in that file.  kind: one of %s.
  notable_symbols: functions, classes or config keys touched.
Markers like [gitkb: ...] mean content was omitted before you saw it; treat it as unknown.\
""" % ", ".join(FILE_KINDS)


class SummaryError(RuntimeError):
    """No usable summary is available for this commit."""


@dataclass
class FileSummary:
    path: str
    summary: str
    kind: str = "other"
    notable_symbols: list[str] = field(default_factory=list)


@dataclass
class CommitAnalysis:
    summary: str
    why: str
    risks: str
    tags: list[str]
    files: list[FileSummary]

    @classmethod
    def from_dict(cls, data: Any, label: str = "") -> "CommitAnalysis":
        if not isinstance(data, dict):
            raise SummaryError(f"{label}: summary entry is not an object")
        try:
            files = []
            for item in _list(data.get("files")):
                if not isinstance(item, dict):
                    raise SummaryError(f"{label}: files[] entry is not an object")
                kind = str(item.get("kind", "other"))
                if kind not in FILE_KINDS:
                    log.warning("%s: unknown kind %r for %s; using 'other'", label, kind, item.get("path"))
                    kind = "other"
                files.append(
                    FileSummary(
                        path=str(item["path"]),
                        summary=str(item["summary"]),
                        kind=kind,
                        notable_symbols=[str(s) for s in _list(item.get("notable_symbols"))],
                    )
                )
            return cls(
                summary=str(data["summary"]),
                why=str(data.get("why", "")),
                risks=str(data.get("risks", "")),
                tags=[str(t) for t in _list(data.get("tags"))],
                files=files,
            )
        except KeyError as e:
            raise SummaryError(f"{label}: summary is missing field {e}") from None


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


class Summarizer(Protocol):
    model_name: str

    def summarize(self, canon: CanonicalCommit) -> CommitAnalysis: ...


class DrySummarizer:
    """Deterministic placeholder output, used by tests and `build --dry-run`."""

    model_name = "dry-run"

    def summarize(self, canon: CanonicalCommit) -> CommitAnalysis:
        m = canon.meta
        return CommitAnalysis(
            summary=f"[dry-run placeholder] Commit {m.short}: {m.subject}",
            why="[dry-run placeholder] Not analysed; import real summaries to replace this.",
            risks="[dry-run placeholder]",
            tags=["dry-run"],
            files=[
                FileSummary(
                    path=pf.change.path,
                    summary=f"[dry-run placeholder] {pf.change.status} {pf.change.path}",
                    kind="other",
                    notable_symbols=[],
                )
                for pf in canon.per_file
            ],
        )


class MappingSummarizer:
    """Serves summaries written by Claude Code from a summaries JSON file."""

    def __init__(self, analyses: dict[str, CommitAnalysis], model_name: str = "claude-code"):
        self.analyses = analyses
        self.model_name = model_name

    def summarize(self, canon: CanonicalCommit) -> CommitAnalysis:
        try:
            return self.analyses[canon.meta.sha]
        except KeyError:
            raise SummaryError(f"no summary provided for {canon.meta.short}") from None


def load_summaries(path: Path) -> tuple[dict[str, CommitAnalysis], str | None]:
    """Parse a summaries file -> ({git_sha: analysis}, model label or None)."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        raise SummaryError(f"summaries file not found: {path}") from None
    except json.JSONDecodeError as e:
        raise SummaryError(f"{path}: invalid JSON: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("commits"), dict):
        raise SummaryError(f'{path}: expected {{"commits": {{<git_sha>: {{...}}}}}}')
    out = {}
    for sha, entry in data["commits"].items():
        if not isinstance(sha, str) or len(sha) != 40:
            raise SummaryError(f"{path}: key {sha!r} is not a full 40-char git sha")
        out[sha] = CommitAnalysis.from_dict(entry, sha[:7])
    model = data.get("model")
    return out, (str(model) if model else None)


def reconcile(analysis: CommitAnalysis, canon: CanonicalCommit) -> dict[str, FileSummary]:
    """Map every file in the commit to a summary; never drop a file."""
    by_path: dict[str, FileSummary] = {}
    for fs in analysis.files:
        p = fs.path
        if p.startswith("a/") or p.startswith("b/"):
            p = p[2:]
        by_path.setdefault(p, fs)

    out: dict[str, FileSummary] = {}
    for pf in canon.per_file:
        path = pf.change.path
        fs = by_path.pop(path, None)
        if fs is None and pf.change.old_path:
            fs = by_path.pop(pf.change.old_path, None)
        if fs is None:
            log.warning("%s: no summary for %s; using placeholder", canon.meta.short, path)
            fs = FileSummary(path=path, summary="(no summary was provided for this file)", kind="other")
        out[path] = fs
    for extra in by_path:
        log.warning("%s: summary mentions unknown file %s (ignored)", canon.meta.short, extra)
    return out
