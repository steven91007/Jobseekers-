---
name: multi-writer
description: Write one application text block (CV Summary, cover-letter slots, a new library bullet, or application-form Q&A) with two independent writers — an Opus subagent and Codex — from one self-contained prompt, then let a fresh Fable judge score both blind (truth vs SoT, JD fit, specificity, writing rules, HR-recruiter wording, hiring-manager wording) and merge the best parts. Policy comes from the 2026-10-31 experiment in cv/experiments/mw_20261031. Use when /tailor-cv step 4 or library mode, /cover-letter step 1, or an application form asks for prose; or when the user says "multi-writer", "多模型", "幾個版本".
---

# /multi-writer

One prompt file → two writers → one blind judge → merge. Everything private stays under `cv/`.

**Glossary** — `$MW` `python3 SKILL_DIR/scripts/mw.py` · `$W` the block's work dir
(`<app or letter dir>/mw/<block>/`) · **Opus writer** = `Agent(subagent_type: "general-purpose",
model: "opus")` · **Codex writer** = `Agent(subagent_type: "codex:codex-rescue")`, `--fresh`, review-only
(reply text; the main agent saves it) · **Fable judge** = `Agent(subagent_type: "general-purpose",
model: "fable")`, fresh context = blind. Writers read **only** `prompt.md`; judges read **only**
`judge_packet.md`. Never let a writer see another writer's text.

## Policy (from the experiment — 4 blocks × 3 JDs × 3 writers, blind-judged)
| Block | Writers | Why |
|---|---|---|
| CV Summary (`summary`) | Opus + Codex → judge merges | Opus won 3/3 (52/60) with 0 SoT violations; Codex (44) is JD-verbatim and strictly factual — the judge wanted Codex's sentence 3 inside Opus's text in 3/3 JDs |
| Cover-letter `experience` + `company_line` (`letter`) | Opus + Codex → judge merges | Opus won 3/3 (52); its misses were an invented product name and a missing project title — exactly what Codex (46) supplies |
| New library bullet (`bullet`) | Opus + Codex → judge merges | Largest gain from merging: Opus body (decision + why, 47) under Codex's verbatim JD lead (38); every "pick one" loses something |
| Application Q&A (`qa`) | Opus (+ Codex when the role's #1 criterion is production ownership) → judge | Opus 52 vs Codex 46; Codex kept choosing the POC story. Judge must read the **full JD** — Opus invents company colour (team names, "the same stack") |
| Any block | never Gemini | last in 11/12, 44 SoT violations in 12 outputs (invented numbers, transplanted facts, banned words), 70–300 s per call on the free tier |

Caveat recorded in the report: n = 3 JDs, one judge model; Codex cross-judged the Summary block to check for family bias (see `report.md` §Conclusions).

## Steps
| # | Step | Owner | Output |
|---|---|---|---|
| 1 | Build the prompt: `$MW prompt --block <summary\|letter\|bullet\|qa> --app <abs app dir> [--letter <abs letter dir>] [--card CODE --capability "…"] [--questions q.json] --out <abs $W>` — self-contained (task + CV + confirmations + JD brief + **full JD** + writing rules) | script | `$W/prompt.md` |
| 2 | Writers in parallel: Opus writer reads `prompt.md` → writes `$W/out_opus.md`; Codex writer reads `prompt.md` → reply saved as `$W/out_codex.md` | Opus, Codex | 2 outputs |
| 3 | `$MW pack --dir <abs $W>` → shuffled A/B packet + hidden mapping | script | `judge_packet.md`, `mapping.json` |
| 4 | Fable judge reads `judge_packet.md`, writes `$W/judge.json` (scores incl. `hr_wording`, `hm_wording`, `worst_phrase`, violations, rank, `would_merge`) | Fable | `judge.json` |
| 5 | `$MW result --dir <abs $W>` prints the ranking with names, violations and the merge note. Apply the merge **only from the two texts** (swap a sentence/lead/title; never write new claims); a violation in the winner → take that clause from the other text or drop it. Then the calling skill continues (length check, build, review) | main agent | chosen text |

Writer prompt (both writers, verbatim): *"You are one writer in a blind writing experiment. Read ONLY
`<abs $W>/prompt.md`, follow its task exactly, reply/write only in the 'Reply ONLY' format."*
Judge prompt: *"You are a blind judge. Read ONLY `<abs $W>/judge_packet.md`, follow it, write the JSON
to `<abs $W>/judge.json` (JSON object only)."*

## Rules
- The prompt is the only input: no writer browses the repo, no judge sees `mapping.json`.
- Truth beats style: a text with an SoT violation cannot win even if HR/HM wording is higher; the judge's
  `violations` list is the first thing the main agent reads.
- `worst_phrase` (HR + HM objection) goes into the calling skill's review notes so the same phrase is not
  re-introduced on revision.
- Re-running an experiment: `$MW prep --exp <dir> --config <config.json>` (blocks × JDs × writers),
  `$MW gemini` (only if `writers` includes gemini), writers as agents, `$MW pack --exp`, judges, `$MW score --exp`
  → `report.md`. Keep experiment dirs under `cv/experiments/`.
