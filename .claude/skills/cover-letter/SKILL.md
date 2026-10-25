---
name: cover-letter
description: Write a cover letter (動機信) for one job from the candidate's .docx template — change only the job title, the experience paragraph (matched to the CV bullets/Skills), the company/industry sentence and the destination country, build DOCX/PDF, then Codex scores it (HR fit, personalisation, CV consistency) with one revision. Separate from /tailor-cv. Use when the user says "cover letter", "動機信", "motivation letter" or gives a JD for a letter.
---

# /cover-letter

Separate from `/tailor-cv`: own template, own scripts, own output folder. It may **read** a job's
`cv/applications/*/jd.md` as input, but never writes into `cv/applications/` and never edits the CV
library.

**Glossary** — `$TPL` `cv/template/cover_letter/CHIYI_HSU_COVER_LETTER.docx` · `$D`
`cv/cover_letters/<YYYYMMDD>_<Co>_<Role>/` · `$LIB` `cv/profile/bullet_library.md` · `$CONF`
`cv/profile/candidate_confirmations.md` · **Codex** = `Agent(subagent_type: "codex:codex-rescue")`,
prompt starts `--fresh`, review-only, no Notion. `cv/` is gitignored — never commit.

## The four slots (everything else stays word-for-word)
| Slot | Where in `$TPL` | Rule |
|---|---|---|
| `title` | header line, "dedicated … who", "As a/an … with" | JD title, strip "(m/w/d)"-style tags; article a/an is fixed by the script. Over ~32 chars the header wraps → use the shortest title the JD itself uses (e.g. HelloFresh "Marketing Automation Engineer") |
| `company_line` | replaces the paragraph-3 opening "Your international orientation and diverse team fascinate me, …" | one sentence, **≤ 190 characters** (the original is 133; the page has ~1–2 spare lines): name the company, its industry/product, one concrete fact from the JD or company page, and why that draws the candidate. It must lead into the next sentence ("As a <title> with a multicultural background, I have worked closely with colleagues across …"). No new metrics, no claims beyond `$LIB`/`$CONF`. |
| `experience` | paragraph 2, "As you will see from my CV, …" | Keep the opening "As you will see from my CV, I have six years of industry experience building and deploying production AI systems, with a focus on …". The focus list = this job's CV Skills categories (top 4–5 items); the achievement sentence = this job's top 1–2 CV bullets, facts and metrics only from `$LIB` / `$CONF` (≤ 2 metrics); end with the ownership sentence. ≤ 620 chars (page room). If `cv/applications/<job>/03_cv.md` exists, read its bullets/Skills (read-only). |
| `country` | "plan to move to <country> on …" | country of the job location (remote roles: the country the JD requires) . The visa sentence follows automatically (CONF-i3): Germany / the Netherlands / Ireland → "I do not need visa sponsorship."; United Kingdom → "I will need visa sponsorship."; any other country → build stops, ask the candidate. |

Outside the slots, the template's claims (move date January 1st, 2027 — CONF-i4) are the candidate's wording — never change
them here; if Codex flags one, report it to the user.

## Steps
| # | Step | Owner | Output |
|---|---|---|---|
| 0 | Setup: JD via URL / file / text, or an existing `cv/applications/*/jd.md` (read-only) | Opus | `$D/jd.md` |
| 1 | Slots: title, company/industry facts (JD + company page if needed), `company_line`, country | Opus | `$D/slots.json` |
| 2 | Build + layout QC | script | `.docx`, `.pdf`, `layout.json` |
| 3 | Score | Codex | `$D/review.md` |
| 4 | Revise slots once (only if REVISE) → step 2 | Opus | — |
| 5 | Report | Opus | `$D/summary.md` (≤ 15 lines) |

**2 · Build**
```bash
bash SKILL_DIR/scripts/build_letter.sh build --template <abs $TPL> --slots <abs $D>/slots.json \
  --out <abs $D>/CoverLetter_AmoHsu_<Co>.docx --pdf
bash SKILL_DIR/scripts/build_letter.sh check --pdf <abs $D>/CoverLetter_AmoHsu_<Co>.pdf --json <abs $D>/layout.json
```
`WARN slot …` = a slot did not match the template (template changed?) → stop and tell the user.
Layout FAIL → shorten `company_line` (1 page, header title + e-mail on one line).

**3 · Score (Codex)** — read `jd.md`, `slots.json`, the built `.docx` text (or PDF), `$LIB`, `$CONF`.
- **Personalisation** 0–100: company + industry named correctly, concrete JD/company fact, reads as written for this job.
- **HR fit** 0–100: title match, motivation credibility, location/relocation clarity, tone, length.
- **CV consistency**: every claim in the `experience` slot must match `$LIB`/`$CONF` and this job's CV; list conflicts (template text outside the slots: report only).
- **VERDICT** `READY` | `REVISE` + ≤ 3 edits, all inside the four slots.

Output ≤ 30 lines: `PERSONALISATION`, `HR`, `CONSISTENCY`, `VERDICT`, edits.

**4 · Revise** — apply in-slot edits that stay truthful; one round only, no second review.
**5 · Report** — slots, scores, verdict, applied/rejected edits, consistency flags, paths. Reply 4–6 lines.
