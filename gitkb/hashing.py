"""Canonical text for each note and the sha256 that names it.

The hash covers exactly the text handed to the summarizer, so a note file
proves which input it describes. Anything that changes the input (diff flags,
size caps, exclude globs) is part of `recipe_string()` and recorded in every
note, so hashes from different recipes are never confused.
"""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass

from . import gitio
from .config import Config
from .gitio import CommitMeta, FileChange

RECIPE_ID = "gitkb-canonical-v1"

# Files whose hunks carry no reviewable meaning; only their diff header is kept.
GENERATED_PATTERNS = (
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.ipynb",
    "dist/**",
)


@dataclass(frozen=True)
class PerFile:
    change: FileChange
    text: str  # full canonical change text (header + chunk)
    chunk: str  # the chunk as it appears inside the commit text
    sha256: str
    truncated: bool
    truncation_reason: str | None


@dataclass(frozen=True)
class CanonicalCommit:
    meta: CommitMeta
    files: list[FileChange]
    commit_text: str
    commit_sha256: str
    per_file: list[PerFile]
    truncated: bool
    recipe: str


def decode_lf(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def recipe_string(cfg: Config) -> str:
    return (
        f"{RECIPE_ID};diff={' '.join(gitio.DIFF_ARGS)};"
        f"per_file_cap={cfg.per_file_cap_bytes};total_cap={cfg.total_cap_bytes};"
        f"exclude={','.join(cfg.exclude_globs)}"
    )


def header(meta: CommitMeta, kind: str, file_path: str | None = None) -> str:
    lines = ["gitkb-canonical: v1", f"gitkb-kind: {kind}"]
    if kind == "change":
        if file_path is None:
            raise ValueError("change header needs a file path")
        lines.append(f"file: {file_path}")
    lines += [
        f"commit: {meta.sha}",
        f"tree: {meta.tree}",
        f"parents: {' '.join(meta.parents)}",
        f"author: {meta.author_name} <{meta.author_email}>",
        f"author_date: {meta.authored_at}",
        f"committer: {meta.committer_name} <{meta.committer_email}>",
        f"commit_date: {meta.committed_at}",
        f"subject: {meta.subject}",
        "body:",
        meta.body,
    ]
    return "\n".join(lines) + "\n"


def commit_text(meta: CommitMeta, canonical_patch: str) -> str:
    return header(meta, "commit") + "---\n" + canonical_patch


def change_text(meta: CommitMeta, path: str, chunk: str) -> str:
    return header(meta, "change", path) + "---\n" + chunk


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def _header_only(chunk: str, marker: str) -> str:
    """Keep the diff header lines (up to the first hunk) and add a marker."""
    lines = chunk.split("\n")
    kept = []
    for line in lines:
        if line.startswith("@@"):
            break
        kept.append(line)
    text = "\n".join(kept).rstrip("\n")
    return f"{text}\n{marker}\n"


def _cap(chunk: str, cap: int) -> tuple[str, bool]:
    data = chunk.encode("utf-8")
    if len(data) <= cap:
        return chunk, False
    cut = data[:cap].rfind(b"\n")
    if cut <= 0:
        cut = cap
    kept = data[:cut].decode("utf-8", "replace").rstrip("\n")
    return f"{kept}\n[gitkb: truncated, kept {cut} of {len(data)} bytes]\n", True


def canonicalize(repo, sha: str, cfg: Config) -> CanonicalCommit:
    meta = gitio.commit_meta(repo, sha)
    parent = meta.first_parent
    files = gitio.file_changes(repo, sha, parent)
    chunks = gitio.split_patch(decode_lf(gitio.patch_bytes(repo, sha, parent)))
    if len(chunks) != len(files):
        raise gitio.GitError(
            f"{sha[:7]}: patch has {len(chunks)} file chunks but {len(files)} files were listed"
        )
    for fc, chunk in zip(files, chunks):
        got = gitio.chunk_path(chunk)
        if got is not None and got != fc.path:
            raise gitio.GitError(f"{sha[:7]}: patch order mismatch ({got!r} vs {fc.path!r})")

    reduced: list[tuple[FileChange, str, bool, str | None]] = []
    for fc, chunk in zip(files, chunks):
        if _matches(fc.path, cfg.exclude_globs):
            reason = "excluded"
        elif _matches(fc.path, GENERATED_PATTERNS):
            reason = "generated"
        else:
            reason = None
        if reason:
            ins = "?" if fc.insertions is None else fc.insertions
            dele = "?" if fc.deletions is None else fc.deletions
            chunk = _header_only(chunk, f"[gitkb: hunks omitted ({reason}), +{ins} -{dele} lines]")
            reduced.append((fc, chunk, True, reason))
            continue
        chunk, cut = _cap(chunk, cfg.per_file_cap_bytes)
        reduced.append((fc, chunk, cut, "per_file_cap" if cut else None))

    total = sum(len(c.encode("utf-8")) for _, c, _, _ in reduced)
    if total > cfg.total_cap_bytes:
        # Reduce files from the end of the patch until the total fits.
        for i in range(len(reduced) - 1, -1, -1):
            if total <= cfg.total_cap_bytes:
                break
            fc, chunk, cut, reason = reduced[i]
            if reason in ("excluded", "generated"):
                continue
            before = len(chunk.encode("utf-8"))
            chunk = _header_only(chunk, "[gitkb: hunks omitted (total_cap)]")
            total -= before - len(chunk.encode("utf-8"))
            reduced[i] = (fc, chunk, True, "total_cap")

    per_file: list[PerFile] = []
    for fc, chunk, cut, reason in reduced:
        text = change_text(meta, fc.path, chunk)
        per_file.append(PerFile(fc, text, chunk, sha256_hex(text), cut, reason))

    patch = "".join(pf.chunk for pf in per_file)
    ctext = commit_text(meta, patch)
    return CanonicalCommit(
        meta=meta,
        files=files,
        commit_text=ctext,
        commit_sha256=sha256_hex(ctext),
        per_file=per_file,
        truncated=any(pf.truncated for pf in per_file),
        recipe=recipe_string(cfg),
    )
