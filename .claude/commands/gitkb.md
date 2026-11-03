---
description: Summarize unsummarized git commits into the knowledge base (gitkb)
---

Update the git knowledge base under `knowledge/` for every commit that does not yet have a real summary. You, Claude Code, write the summaries; no API call is involved.

Steps:

1. Run `python -m gitkb pending`. It writes `knowledge/pending.json` and lists the commits that need a summary. If it reports 0 commits, say so and stop.
2. Read `knowledge/pending.json`. For each entry in `commits`, the `input` field holds the canonical header and unified diff that the note's sha256 is computed from; `files` lists every path you must cover. Follow `field_guide` and `answer_format` from the same file. Read the actual diff carefully rather than paraphrasing the commit message.
3. Write your answers to `knowledge/summaries.json` in the `answer_format` shape: a top-level `model` label (`claude-code/<your model id>`) and a `commits` object keyed by full git sha. Every file listed for a commit needs exactly one `files[]` entry with the same `path`. Summaries are in English.
4. Run `python -m gitkb import knowledge/summaries.json`. It renders the notes, indexes them, and reports counts. Fix and re-run if it reports failures.
5. Run `python tests/test_gitkb.py` if you changed any gitkb code, then delete `knowledge/pending.json` and `knowledge/summaries.json` (they are scratch files) and show `python -m gitkb log`.

Do not edit files under `knowledge/commits/` or `knowledge/changes/` by hand; their names are hashes of the summarized input and the importer regenerates them. If `import` warns that the hash recipe changed, stop and tell the user before continuing.

$ARGUMENTS
