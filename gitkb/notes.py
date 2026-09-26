"""Markdown notes: rendering, front matter, and atomic writes.

Front matter is a restricted YAML subset that a ~40-line parser can read back
without a YAML library: every scalar is a JSON literal, and lists are block
sequences whose items are JSON literals (scalars or flow mappings). It is
still valid YAML for any external tool.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hashing import CanonicalCommit, PerFile
from .summarize import CommitAnalysis, FileSummary


class NoteFormatError(ValueError):
    pass


@dataclass
class ParsedNote:
    front: dict[str, Any]
    title: str
    sections: dict[str, str]
    body: str


# --- front matter ----------------------------------------------------------


def _scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def dump_front_matter(d: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in d.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise NoteFormatError("note does not start with front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise NoteFormatError("unterminated front matter")
    block = text[4:end]
    body = text[end + 5:]
    front: dict[str, Any] = {}
    current_list: list | None = None
    for lineno, line in enumerate(block.split("\n"), 2):
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current_list is None:
                raise NoteFormatError(f"line {lineno}: list item outside a list")
            current_list.append(_load(line[4:], lineno))
            continue
        key, sep, rest = line.partition(":")
        if not sep or not key or key[0] == " ":
            raise NoteFormatError(f"line {lineno}: expected `key: value`")
        rest = rest.strip()
        if rest == "":
            current_list = []
            front[key] = current_list
        else:
            current_list = None
            front[key] = _load(rest, lineno)
    return front, body


def _load(literal: str, lineno: int) -> Any:
    try:
        return json.loads(literal)
    except json.JSONDecodeError:
        raise NoteFormatError(f"line {lineno}: value is not a JSON literal: {literal!r}") from None


_H2 = re.compile(r"^## (.+)$", re.M)


def parse_note(text: str) -> ParsedNote:
    front, body = parse_front_matter(text)
    title = ""
    for line in body.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break
    sections: dict[str, str] = {}
    matches = list(_H2.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[m.group(1).strip()] = body[m.end():end].strip()
    return ParsedNote(front=front, title=title, sections=sections, body=body)


# --- rendering --------------------------------------------------------------


def note_path(knowledge_dir: Path, kind: str, sha256: str) -> Path:
    sub = "commits" if kind == "commit" else "changes"
    return knowledge_dir / sub / f"{sha256}.md"


def _bullets(items: list[str], empty: str = "- (none)") -> str:
    return "\n".join(f"- {i}" for i in items) if items else empty


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = re.search(r"[.!?](\s|$)", text)
    return text[: m.end()].strip() if m else text


def _counts(pf: PerFile) -> str:
    c = pf.change
    if c.is_binary:
        return "binary"
    return f"+{c.insertions or 0}/-{c.deletions or 0}"


def render_commit_note(
    canon: CanonicalCommit,
    analysis: CommitAnalysis,
    file_summaries: dict[str, FileSummary],
    parent_notes: dict[str, str | None],
    parent_subjects: dict[str, str],
    model: str,
    created_at: str,
    version: str,
) -> str:
    m = canon.meta
    front: dict[str, Any] = {
        "kind": "commit",
        "sha256": canon.commit_sha256,
        "git_sha": m.sha,
        "tree_sha": m.tree,
        "parents": [{"git_sha": p, "note_sha256": parent_notes.get(p)} for p in m.parents],
        "author": f"{m.author_name} <{m.author_email}>",
        "authored_at": m.authored_at,
        "committer": f"{m.committer_name} <{m.committer_email}>",
        "committed_at": m.committed_at,
        "subject": m.subject,
        "model": model,
        "hash_recipe": canon.recipe,
        "patch_bytes": len(canon.commit_text.encode("utf-8")),
        "truncated": canon.truncated,
        "tags": list(analysis.tags),
        "files": [
            {
                "path": pf.change.path,
                "old_path": pf.change.old_path,
                "status": pf.change.status,
                "insertions": pf.change.insertions,
                "deletions": pf.change.deletions,
                "binary": pf.change.is_binary,
                "truncated": pf.truncated,
                "note_sha256": pf.sha256,
            }
            for pf in canon.per_file
        ],
        "created_at": created_at,
        "gitkb_version": version,
    }

    file_lines = []
    for pf in canon.per_file:
        c = pf.change
        fs = file_summaries[c.path]
        label = f"`{c.old_path}` -> `{c.path}`" if c.old_path else f"`{c.path}`"
        file_lines.append(
            f"{label} ({c.status}, {_counts(pf)}): {_first_sentence(fs.summary)} "
            f"[note](../changes/{pf.sha256}.md)"
        )
    parent_lines = []
    for p in m.parents:
        subj = parent_subjects.get(p, "")
        text = f"{p[:7]} {subj}".strip()
        note = parent_notes.get(p)
        parent_lines.append(f"[{text}](../commits/{note}.md)" if note else f"{text} (not indexed)")

    message = m.subject + ("\n\n" + m.body if m.body else "")
    body = (
        f"# {m.short} {m.subject}\n\n"
        f"## Summary\n{analysis.summary.strip()}\n\n"
        f"## Why\n{analysis.why.strip() or '(not stated)'}\n\n"
        f"## Risks\n{analysis.risks.strip() or 'none identified'}\n\n"
        f"## Files\n{_bullets(file_lines)}\n\n"
        f"## Parents\n{_bullets(parent_lines, '- (root commit)')}\n\n"
        f"## Commit message\n```text\n{message}\n```\n"
    )
    return dump_front_matter(front) + "\n" + body


def render_change_note(
    canon: CanonicalCommit,
    pf: PerFile,
    fs: FileSummary,
    model: str,
    created_at: str,
) -> str:
    m = canon.meta
    c = pf.change
    front: dict[str, Any] = {
        "kind": "change",
        "sha256": pf.sha256,
        "git_sha": m.sha,
        "commit_note_sha256": canon.commit_sha256,
        "path": c.path,
        "old_path": c.old_path,
        "status": c.status,
        "insertions": c.insertions,
        "deletions": c.deletions,
        "binary": c.is_binary,
        "truncated": pf.truncated,
        "truncation_reason": pf.truncation_reason,
        "change_kind": fs.kind,
        "notable_symbols": list(fs.notable_symbols),
        "model": model,
        "hash_recipe": canon.recipe,
        "created_at": created_at,
    }
    symbols = _bullets([f"`{s}`" for s in fs.notable_symbols])
    body = (
        f"# {c.path} @ {m.short}\n\n"
        f"Part of commit [{m.short} {m.subject}](../commits/{canon.commit_sha256}.md)"
        f" ({c.status}, {_counts(pf)}, kind: {fs.kind})\n\n"
        f"## Summary\n{fs.summary.strip()}\n\n"
        f"## Notable symbols\n{symbols}\n"
    )
    return dump_front_matter(front) + "\n" + body


# --- files -------------------------------------------------------------------


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
