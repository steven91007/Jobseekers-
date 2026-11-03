# Smoke run — two concurrent POST /api/claude

Date: 2026-10-31T11:54:06.834Z · wall time 12 s

Instruction (both): `Read SKILL.md and tools/cv-dashboard/data/run_board.json, reply with the other run's application, change nothing`

Board right after both POSTs: 2 running, 0 queued.

## TOMRA · run `199b7767-44e7-4e70-a765-583fae38552a`

Started 2026-10-31T11:54:06.844Z · ended 2026-10-31T11:54:18.556Z · status **succeeded**, exit **0**, cost $0.5555, 03_cv.md changed lines 0, files edited 0.

Tool calls:

- Read /mnt/e/PythonProject/LinkedinJobSearcher/.claude/skills/tailor-cv/SKILL.md
- Read /mnt/e/PythonProject/LinkedinJobSearcher/tools/cv-dashboard/data/run_board.json

Final answer:

> The other run in progress is the Ixopay application.
> 
> - **Run** 98cd03f5-e943-4a75-af74-b983480aec91, JD Ixopay, status running, started at the same moment as this run.
> - **Application dir** `cv/applications/20261025_Ixopay_Sr-AI-Engineer/v3_cards`.
> - **Its task** mirrors mine: read the skill file and the run board, reply with the other run's application, change nothing. It has no files changed and no log lines yet.
> 
> I changed nothing. No files, no database rows, no Notion pages, and no shared lock were touched. The board lists only these two runs, so there is no overlap on the TOMRA application.

## Ixopay · run `98cd03f5-e943-4a75-af74-b983480aec91`

Started 2026-10-31T11:54:06.910Z · ended 2026-10-31T11:54:18.997Z · status **succeeded**, exit **0**, cost $0.5598, 03_cv.md changed lines 0, files edited 0.

Tool calls:

- Read /mnt/e/PythonProject/LinkedinJobSearcher/.claude/skills/tailor-cv/SKILL.md
- Read /mnt/e/PythonProject/LinkedinJobSearcher/tools/cv-dashboard/data/run_board.json

Final answer:

> The other run's application is **TOMRA**, Senior Python Software Engineer.
> 
> Details from the run board:
> 
> - **Run ID** 199b7767-44e7-4e70-a765-583fae38552a, status running, started 2026-10-31 11:54 UTC.
> - **Application dir** `cv/applications/20261023_TOMRA_Senior-Python-Software-Engineer/v3_cards`.
> - **Instruction** is the same read-only task as mine, and it has changed no files so far.
> 
> Nothing was changed in this run: no edits to the library seed, confirmations, SQLite DB, the Ixopay application folder, or Notion. The shared lock was not taken since no shared write was needed.

## Coordination block of the second run's prompt

```
Coordination: you are dashboard run 98cd03f5-e943-4a75-af74-b983480aec91; up to 3 runs work in this repo at the same time (never two on the same application).
Other runs in progress:
- TOMRA (other, running): Read SKILL.md and tools/cv-dashboard/data/run_board.json, reply with the other run's application, change nothing
Read tools/cv-dashboard/data/run_board.json (every run's status, files changed so far, last log lines) before you edit any shared file and again before you finish. Shared state is cv/profile/library_seed.json, cv/profile/candidate_confirmations.md, the SQLite DB (library_db.py init / record / set-*) and the Notion rows. Adapt to other runs instead of overwriting them: if another run changed the same bullet or row, build on its result, never revert it. Shared writes go under the lock: `python3 tools/cv-dashboard/scripts/shared_lock.py hold --run 98cd03f5-e943-4a75-af74-b983480aec91 --seconds 600`, then re-read the seed / CONF files (another run may have changed them), make the edit, run library_db.py init + selftest (+ export-md, record / set-*, Notion mirror), then `python3 tools/cv-dashboard/scripts/shared_lock.py release --run 98cd03f5-e943-4a75-af74-b983480aec91`. For a single shared command use `python3 tools/cv-dashboard/scripts/shared_lock.py run --run 98cd03f5-e943-4a75-af74-b983480aec91 -- python3 .claude/skills/tailor-cv/scripts/library_db.py …`. `python3 tools/cv-dashboard/scripts/shared_lock.py status` shows who holds it. The files of this application (03_cv.md, PDFs, ATS files in its folder) are yours alone during this run; mention in your report any change you built on from another run.
```
