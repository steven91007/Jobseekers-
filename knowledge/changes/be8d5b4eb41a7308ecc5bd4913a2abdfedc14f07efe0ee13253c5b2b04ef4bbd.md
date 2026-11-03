---
kind: "change"
sha256: "be8d5b4eb41a7308ecc5bd4913a2abdfedc14f07efe0ee13253c5b2b04ef4bbd"
git_sha: "8dc875c01d468c790874c3d083f80b167a8e557b"
commit_note_sha256: "1b418734f47999d8eed1756d6edd30596b044b1265ac98cbddd895f8076c734d"
path: "jobagent/llm/client.py"
old_path: null
status: "M"
insertions: 27
deletions: 12
binary: false
truncated: false
truncation_reason: null
change_kind: "feature"
notable_symbols:
  - "call"
  - "_summary_supported"
model: "claude-code/claude-opus-5-5"
hash_recipe: "gitkb-canonical-v1;diff=-c core.quotePath=false -c diff.renames=true diff-tree -r --no-commit-id --no-color --no-ext-diff --no-textconv --full-index --find-renames=50% -U3 --src-prefix=a/ --dst-prefix=b/;per_file_cap=65536;total_cap=400000;exclude=knowledge/**"
created_at: "2026-09-28T06:51:09+00:00"
---

# jobagent/llm/client.py @ 8dc875c

Part of commit [8dc875c Align Langfuse tracing with Langfuse best practices (#3)](../commits/1b418734f47999d8eed1756d6edd30596b044b1265ac98cbddd895f8076c734d.md) (M, +27/-12, kind: feature)

## Summary
call() requests a reasoning summary so thinking is recorded, degrades to effort-only and then to no reasoning when the API refuses, and passes trace_metadata to the Langfuse wrapper.

## Notable symbols
- `call`
- `_summary_supported`
