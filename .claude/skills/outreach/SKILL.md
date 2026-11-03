---
name: outreach
description: Draft messages to a person for one application — referral letter (the referrer forwards it to the hiring contact, third-person voice, two CV project cards mapped to JD duties), LinkedIn connect note (≤ 300 characters), follow-up to a recruiter or hiring manager, thank-you after an interview — from the recorded CV picks only, truth-checked against 03_cv.md and the library, Codex-reviewed, stored as drafts in the outreach table. Never sends or posts anything. Use when the user says "referral", "內推信", "outreach", "connect note", "follow up", "thank you mail", "聯絡", or names a person to contact about a job.
---

# /outreach

**Glossary** — `$D` the job's tailor-cv dir (`$DB status` → Dir; holds `jd.md`, `01_jd.md`, `03_cv.md`) · `$DB`
`python3 .claude/skills/tailor-cv/scripts/library_db.py` · `$OUT` `python3 SKILL_DIR/scripts/outreach.py` · `$PROFILE`
`cv/profile/candidate_profile.md` · `$CONF` `cv/profile/candidate_confirmations.md` · `$RULES`
`.claude/skills/tailor-cv/references/writing_rules.md` · **Codex** = `Agent(subagent_type: "codex:codex-rescue")`, prompt from
`references/codex_prompt.md` (starts `--fresh`, review-only, no Notion). `cv/` is gitignored — never commit.

**Hard rule** — this skill only drafts. It never sends an e-mail, never posts or invites on LinkedIn, never messages anyone.
The candidate sends by hand; the tracker is updated only when the candidate says it was sent.

**Principles** — the CV in `$D/03_cv.md` is what the recipient already holds, so every fact, number and unit comes from it, the
recorded picks, or `$CONF`. Keep the qualifiers: "Phase 1 proof of concept delivered on time" (never phase 2/3, never "in
production" for MaterialCheck), "stakeholder management" (not "led the plan"; region per CONF-am), "an LLM outage never stops users", "structured LLM
output", "target users" (not "200+ users"; MaterialCheck's "200+ target users" only with `CONF-o6` in `--notes` and the POC
qualifier, e.g. "200+ target users, 30 → 5 minutes per document in POC testing", never "used by 200+"). No invented relationship details (how long, where met), team names, feedback or motives;
if the relation is unknown, say only what the candidate gave. A missing fact becomes `TODO candidate:`, never a guess.

## Kinds
| Kind | Voice | Limit | Content |
|---|---|---|---|
| `referral-letter` | referrer, third person about the candidate | 200–280 words (check: fail > 300) | relation in one sentence as given; role + years; **two picked cards**, each led by its **project title**, facts from its bullets, ending with the JD duties it matches; optionally one sentence with one hook's company fact (pack excerpt; only hooks with `use: all` and `on CV: yes`) that names the company or the product; one-line ask to pass it on; signature `[Referrer's name]` |
| `connect-note` | candidate, first person | ≤ 300 characters incl. spaces | applied for the role + one concrete hook (a card fact the person's team would care about, optionally joined to one hook's company fact, in a clause that names the company or the product; skip hooks marked `on CV: no` or `use: interview-only`) + relocation date if relevant; no pitch, no ask beyond connecting |
| `follow-up` | candidate | ≤ 120 words | after N days: role applied for, one new/clear value point from the CV, one polite ask (status or a call) |
| `thank-you` | candidate | ≤ 150 words | within a day of the interview; 1–2 topics from `qa list --jd <Co> --source asked`; one sentence of fit; no new claims |

## Steps
| # | Step | Owner | Output |
|---|---|---|---|
| 0 | Resolve the application (`$DB status`) and the person: name, role, relation, channel, kind | Opus | `$D`, JD code |
| 1 | `$OUT pack --jd <Co> --kind K [--person … --role … --relation …]` | script | context (≤ 8k chars) |
| 2 | Write the draft **from the PRIOR** printed by `pack` (user-approved wording); change only what SoT forbids or the kind/JD requires | Opus | `$D/11_outreach_<kind>[_<person-slug>].md` |
| 3 | `$OUT check <draft> --jd <Co> --kind K --prior <prior file> [--cites VI1,PS1] [--notes "CONF-o2"]` → fix until OK | script | exit 0, dropped/changed sentences listed |
| 4 | Fact-check + tone review (`references/codex_prompt.md`), one revision, re-check | Codex | fixes |
| 5 | `$OUT record --jd <Co> --kind K --person … --channel … [--via <referrer>] [--role …] --file <draft>` | script | outreach row, status `draft` (runs `check` first, refuses on FAIL; same jd/kind/person/file is updated, not duplicated) |

**0 · Resolve** — JD code from `$DB status` or the user. Missing relation → ask once if interactive; otherwise write without one and add `TODO referrer: how you know Chi-yi`. For a
thank-you, run `$DB qa list --jd <Co> --source asked --full` and use only recorded topics; none recorded → keep it generic and say so.

**1 · Pack** — prints the request; `$D/outreach_overrides.md` if present (standing per-application user wording: follow it); the **PRIOR** — the latest user-approved text for this kind (`09_referral_final.md` for a referral letter, else the newest `$D/11_outreach_<kind>_*.md`, else the file of the newest outreach row) — then the JD brief, the recorded picks (scope line, card titles and contexts as submitted), each picked
bullet (code, lead, body, metric), card metrics, the company-research excerpt (`excerpt()`: hooks with `use:` and `on CV:` flags) when present — use only `use: all` + `on CV: yes` hooks, profile identity/relocation/languages, existing `outreach list --jd` rows, and for
thank-you the asked Q&A. Use only these facts. The prior is the candidate's own wording: keep it unless the SoT forbids it or the kind's limit or the JD requires a change, and say what you changed.

**3 · Check** (exit 1 on error) — length per kind; **truth check** (shared with `/interview-prep`, `interview-prep/scripts/truth.py`):
every number-like token (digits, %, ×, €, k/m/b, ranges expanded) must appear with the same unit in the cited bullets, their cards,
`03_cv.md` or the profile's identity/relocation lines; a number found only in the company facts (`company_text()`: snapshot, product team, news, hook company facts — never `02_company.md`, whose candidate lines hold library facts not on the CV) passes only when its sentence has no candidate reference in the draft's voice (he/his/Chi-yi for a referral
letter, I/my/we for the other kinds), names the company or a product (company name, product-like tokens, a `names`
list in `02_company.json`), and shares a context word next to the number with the company fact; otherwise ERROR
(`truth.company_number_ok`) (default `--cites` = the picked bullets; the corpus includes **all of 03_cv.md**, because the reader holds the CV, so Codex check A also covers a fact moved to the wrong project); a number found only in a CONF item named
in `--notes` is a warning, retracted ones are refused; **dangerous upgrades** are errors unless the same words are in that corpus (company facts never count here) or in a CONF item named in `--notes` (CONF-e1 allows "user feedback"):
phase 2/3 or "all three phases" delivered, "200+ users", "led/managed … project/plan/initiative", "cross-functional", "business stakeholders"
(always an error, CONF-e7), "user feedback", MaterialCheck (also "QA sign-off", "document AI") within 6 words of production/live/deployed wording.
With `--prior`, a hit that is already in the user's prior (all but 200+, delivered phases and business stakeholders) is a WARN "user wrote this, confirm with candidate" instead of an error; the stakeholder region follows CONF-am: "Germany and Asia" for a German company (pack prints the wording from `applications.country`), "Europe and Asia" otherwise; "Germany and Asia" on a non-German application is an error (warning if already in the prior). A unitless number that belongs to the other card's bullets warns as a possible misattribution. `check --selftest` runs these cases. Warnings: referral letter in first person or without project-title leads; other kinds in third person.

**4 · Codex** — fill `references/codex_prompt.md` and send. Apply the findings once, re-run step 3.

**5 · Record + report** — `record` wraps `$DB outreach add … --status draft` (channel `email | linkedin | referral | other`). Report in
≤ 8 lines: kind, person, length, check result, Codex findings fixed, `TODO candidate:` items, draft path, outreach row id.

**After the candidate sends it** — only on the candidate's word: `$DB outreach set <id> --status sent --sent <YYYY-MM-DD>`; a reply:
`--status replied --replied <date>`; no answer after the follow-up window: `--status no-reply`. `$DB outreach list --jd <Co>` shows
the history; a follow-up is due when a `sent` row is older than about 10 days with no reply.
