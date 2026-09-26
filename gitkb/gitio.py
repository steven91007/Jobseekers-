"""Subprocess wrappers around git. Every git read in gitkb goes through here.

Output feeding the sha256 hashes must be stable across machines, so diffs are
produced with a fixed flag set and with user/system git config disabled.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "LC_ALL": "C",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
}

# Flags shared by --raw/--numstat listing and the -p patch so they agree on
# rename detection and file order. Part of the hash recipe: do not reorder.
DIFF_ARGS = [
    "-c", "core.quotePath=false",
    "-c", "diff.renames=true",
    "diff-tree", "-r", "--no-commit-id", "--no-color", "--no-ext-diff",
    "--no-textconv", "--full-index", "--find-renames=50%", "-U3",
    "--src-prefix=a/", "--dst-prefix=b/",
]


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommitMeta:
    sha: str
    tree: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: str
    committer_name: str
    committer_email: str
    committed_at: str
    subject: str
    body: str

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def first_parent(self) -> str | None:
        return self.parents[0] if self.parents else None


@dataclass(frozen=True)
class FileChange:
    path: str
    old_path: str | None
    status: str  # A M D R C T
    insertions: int | None  # None for binary
    deletions: int | None
    is_binary: bool


def run_git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    env = dict(os.environ)
    env.update(GIT_ENV)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        raise GitError("git executable not found on PATH") from None
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args[:3])}... failed ({proc.returncode}): {err}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def repo_root(start: Path) -> Path:
    out = run_git(start, "rev-parse", "--show-toplevel")
    return Path(str(out).strip())


def rev_list(repo: Path, rev: str = "HEAD") -> list[str]:
    """Commit shas reachable from `rev`, oldest first, parents before children."""
    out = str(run_git(repo, "rev-list", "--reverse", "--topo-order", rev))
    return [line for line in out.split("\n") if line]


_META_FORMAT = "%H%x00%T%x00%P%x00%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%s%x00%b"


def commit_meta(repo: Path, sha: str) -> CommitMeta:
    out = str(run_git(repo, "log", "-1", f"--format={_META_FORMAT}", sha))
    parts = out.split("\x00")
    if len(parts) != 11:
        raise GitError(f"unexpected `git log` output for {sha}")
    body = parts[10].replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return CommitMeta(
        sha=parts[0],
        tree=parts[1],
        parents=tuple(p for p in parts[2].split(" ") if p),
        author_name=parts[3],
        author_email=parts[4],
        authored_at=parts[5],
        committer_name=parts[6],
        committer_email=parts[7],
        committed_at=parts[8],
        subject=parts[9],
        body=body,
    )


def _range_args(sha: str, parent: str | None) -> list[str]:
    return ["--root", sha] if parent is None else [parent, sha]


def file_changes(repo: Path, sha: str, parent: str | None) -> list[FileChange]:
    """Files touched by `sha` relative to `parent` (first parent for merges)."""
    raw = str(run_git(repo, *DIFF_ARGS, "--raw", "-z", *_range_args(sha, parent)))
    num = str(run_git(repo, *DIFF_ARGS, "--numstat", "-z", *_range_args(sha, parent)))

    # --raw -z: ":<mode> <mode> <sha> <sha> <status>\0<path>\0" or, for R/C,
    # ":... R<score>\0<old>\0<new>\0"
    entries: list[tuple[str, str, str | None]] = []
    tokens = raw.split("\0")
    i = 0
    while i < len(tokens) and tokens[i]:
        head = tokens[i]
        status = head.split(" ")[-1]
        letter = status[0]
        if letter in ("R", "C"):
            old, new = tokens[i + 1], tokens[i + 2]
            entries.append((letter, new, old))
            i += 3
        else:
            entries.append((letter, tokens[i + 1], None))
            i += 2

    # --numstat -z: "<ins>\t<del>\t<path>\0" or "<ins>\t<del>\t\0<old>\0<new>\0"
    stats: dict[str, tuple[int | None, int | None]] = {}
    tokens = num.split("\0")
    i = 0
    while i < len(tokens) and tokens[i]:
        ins, dele, path = tokens[i].split("\t", 2)
        if path == "":
            path = tokens[i + 2]
            i += 3
        else:
            i += 1
        stats[path] = (None if ins == "-" else int(ins), None if dele == "-" else int(dele))

    result = []
    for letter, path, old in entries:
        ins, dele = stats.get(path, (None, None))
        result.append(
            FileChange(
                path=path,
                old_path=old,
                status=letter,
                insertions=ins,
                deletions=dele,
                is_binary=(ins is None and dele is None),
            )
        )
    return result


def patch_bytes(repo: Path, sha: str, parent: str | None) -> bytes:
    """Unified diff of `sha` against `parent` with the fixed, hash-stable flags."""
    return run_git(repo, *DIFF_ARGS, "-p", *_range_args(sha, parent), binary=True)  # type: ignore[return-value]


_DIFF_HEADER = re.compile(r"^diff --git ", re.M)


def split_patch(patch: str) -> list[str]:
    """Split a multi-file patch into per-file chunks, each starting at `diff --git`."""
    starts = [m.start() for m in _DIFF_HEADER.finditer(patch)]
    if not starts:
        return []
    starts.append(len(patch))
    return [patch[a:b] for a, b in zip(starts, starts[1:])]


def chunk_path(chunk: str) -> str | None:
    """Best-effort `b/` path from a chunk's first line; None when git quoted it."""
    first = chunk.split("\n", 1)[0]
    if '"' in first:
        return None
    marker = " b/"
    idx = first.rfind(marker)
    return first[idx + len(marker):] if idx >= 0 else None
