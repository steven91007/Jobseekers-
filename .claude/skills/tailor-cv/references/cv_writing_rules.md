# CV writing rules (used by the Opus writer and reviser)

## Non-negotiables

1. **Never invent.** Every skill, employer, title, date, metric and project must come
   from the base template, `cv/profile/`, a candidate confirmation, or a Notion page you
   actually read — and must have a row in the claim ledger with a status from
   `references/evidence_rules.md` (Verified / Template / User-reported / Inferred /
   Missing Evidence / Contradicted). Follow its measurement-integrity rules (component ≠
   pipeline, similar ≠ identical, configured ≠ executed, staging ≠ production, small
   samples stay small, percent vs percentage points, keep the original scope). If the
   JD asks for something the candidate has not done, leave it out — do not hedge it
   in with "familiar with". Record it under `Honest gaps` in the evidence map instead.
2. **Stay inside the editable scope.** By default three parts may change: the
   **Summary/Profile** (written last, by Codex, in Phase 4b), the **Delta
   work-experience bullets** (the heading line itself is fixed) and the **Technical
   Skills rows** (text only — keep the same number of rows and the single-column
   layout). Everything else — header, other roles, projects, education, certifications,
   languages — is copied verbatim from the template. If a requirement can only be shown
   elsewhere, note it under `Honest gaps / suggestions` in the evidence map instead.
3. **Keep the template's language** (English stays English, German stays German).
   If the JD is in another language than the template, stop and ask the user.
4. **Length = the template's page count.** The tailored CV must render to the same
   number of pages as the base template (a half-empty trailing page is a failure).
   Budget rule of thumb for this template: ~118 characters per rendered body line;
   profile ≈ 4 lines (400–470 chars including bold markers; written last, by Codex, from the final bullets + JD); each bullet ≤ 2 lines (≤ 215 chars including the
   bold lead, excluding `<!-- -->` comments); the Delta block has **at most 5
   bullets** (fewer is fine if every one is strong). Count characters before returning.
5. **Max 5 Delta bullets.** If the evidence supports more, merge closely related
   achievements or drop the one that serves the JD least — never pack two unrelated
   results into one bullet to keep both. Record what was dropped and why under
   `Honest gaps / suggestions` in the evidence map so the user can swap it back.

## Voice: correct, precise, human

- Every rewritten line must be something the candidate could say out loud in an
  interview and defend in detail. Explain *what was built or changed* and *what it
  achieved*, in the terms an engineer on that team would use. If a sentence only
  lists tools, versions or incidents without a result, rewrite it or cut it.
- Precision over coverage: one accurate, specific clause beats three approximate ones.
  Do not stack details to hit keywords.
- Banned filler and AI-style phrasing: "leveraged", "spearheaded", "robust",
  "seamless", "cutting-edge", "state-of-the-art", "empowering", "driving", "passionate",
  "synergy", "holistic", "impactful", abstract nouns with no object ("solutions",
  "initiatives", "capabilities"), triads added for rhythm, and the same phrase
  ("end-to-end", "production-ready") used more than once in the CV.
- Prefer plain verbs: built, moved, replaced, cut, split, added, shipped, measured, fixed.

## Tailoring technique

- Lead with what the JD leads with. Within the editable scope, order the Delta
  bullets so the must-haves (R# with weight `must`) come first (this is the writer's
  first guess — the Codex reviewer sets the final order in review round 1); put the 3 strongest
  must-have matches in the summary. Keep the Delta block at 5 bullets or
  fewer (rule 5), then check the rendered page count.
- Mirror the JD's exact terminology when it is truthful ("RAG pipelines" not
  "retrieval systems" if the JD says RAG). One natural occurrence per keyword is
  enough; no stuffing.
- Bullet formula (Impact-First): **Impact → Technical implementation → Engineering
  decision / trade-off → Validation.** Lead with the result, then how it was built, then
  — when it fits in two lines — why that approach and how it was checked. Do not force
  all four elements into a bullet if readability suffers; never pack unrelated projects
  or results into one bullet. Shape example (placeholders, not facts):
  "**Cut <stage> latency <N>× —** <what was changed, with which tool>, keeping <the
  alternative> where it was faster; validated <how, on what set> (<baseline> → <result>)."
- Emphasis follows the role archetype from `01_jd_analysis.md`: **Python platform** →
  APIs, architecture, Docker, testing, CI/CD, operations; **computer vision** →
  detection/segmentation, model optimization, inference, evaluation; **agentic AI** →
  LangGraph, RAG, tool calling, guardrails, reliability; **MLOps** → training
  pipelines, model registry, evaluation, serving, monitoring. Do not assume every JD
  needs the same projects.
- Prefer numbers from the evidence map (users, %, latency, €, team size, data
  volume). If no number exists, state scope instead ("3 services, 2 markets").
- **Small bases become percentages.** When a result is a count out of a base smaller
  than 100, write it as a percentage with the base: "11/20 → 17/20 images"
  becomes "55% → 85% (n=20)". Keep raw counts only when the base is ≥ 100 or the count
  itself is the point ("241 tests", "100+ engineers"). Never drop the base if the
  reader would otherwise assume a large sample.
- Past tense for past roles, present tense for the current role, no first-person
  pronouns, no soft adjectives ("passionate", "hard-working").
- `Template` numbers (only in the base CV, no source) may appear but may not lead the
  Profile or a bullet unless the candidate confirms their scope.
- Summary/Profile: leave it unchanged during Phases 2–4. In Phase 4b, Codex writes
  one paragraph targeting approximately 4 rendered lines and 400–470 characters including bold markers. Focus on what the candidate built, improved or delivered at Delta
  and the business or engineering results supported by the evidence. Connect these achievements to the target
  role and strongest must-have matches rather than listing tools or generic strengths.
  Use other experience only where it fills a relevant gap; never attribute it to Delta.
- For every rewritten bullet with a bold lead-in (including Summary bullets), use a
  short, precise action + main business/engineering contribution, typically 4–8 words.
  The lead should make sense on its own, e.g. **Built the CI/CD pipeline for ML services —**
  (name the environment — cloud, on-prem, air-gapped — only as the evidence map states it).
  Keep incidental implementation details, test counts, dependency names/versions and
  troubleshooting anecdotes out of the bold lead. Put supporting tools, meaningful
  metrics and outcomes in the regular text only when they help establish impact or
  JD fit; omit distracting details rather than packing every fact into one bullet.
  Technical terms such as CI/CD belong in the lead when they name the main achievement.

  Example (use only facts supported by the evidence map):
  - Avoid: **Offline-ready CI/CD with 241 containerized tests —** Built layered Docker
    images, GitLab CI and staged deploys with manual prod approval; ran code reviews
    and troubleshot a production 503 after a PaddleOCR upgrade.
  - Prefer (design-only evidence): **Designed CI/CD for ML services —** The GitLab CI
    configuration defines layered Docker builds, container tests, staging deployment and
    a manual production release job.
  - Prefer (with an execution record or candidate confirmation): "… auto-deploys to
    on-prem staging; manual approval releases to the air-gapped production server."
  Execution verbs (deploys, releases, ships, runs in production) only with an execution
  record — see evidence_rules rule 3, reader test.
  Name the environment (cloud / on-prem / air-gapped) and the stage (staging /
  production) only as the evidence states them.
- Skills block (in scope by default): rewrite row text in place to put the JD's
  must-have, **verified** technologies first and drop clearly irrelevant ones. Keep the
  same number of rows, the `**Label:** item · item` shape and the single-column
  layout; you may rename a row label to match the JD's category names. Use only
  technologies in the claim ledger as Verified or User-reported; existing Template
  technologies may be retained verbatim.
- Projects / side work count as evidence when they demonstrate a must-have the
  employment history does not.

## Document format (checked by `build_cv.py check` after every build)

A4, the template's page count (one page for the current template), body 10 pt,
section headings 11 pt, single-column Technical Skills,
≈ 90% page-height utilisation (85–98%), ATS-friendly reading order, no duplicated KPI
block, all non-target experience preserved. The tailored Delta role has at most 5
bullets of at most 2 rendered lines each; the Profile is about 4 lines. Never report the
layout as validated unless the check actually ran on the rendered PDF.

## Markdown conventions the build script understands

```
# Full Name
Line(s) directly under H1 = contact block (email · phone · city · LinkedIn · GitHub)

## Section Title            → Heading 2
### Role — Company | City | 2023-01 – present   → Heading 3 (one line)
- bullet                    → bulleted list item
plain paragraph             → Normal
**bold** and *italic*       → inline runs
---                         → ignored (visual separator in md only)
```

Do not add tables, images or HTML markup. Single-line `<!-- R# -->` comments
are stripped before building or patching; they do not appear in the output.

**When the template was extracted from a .docx** (`CV_template.md` has no `#` markers
and bullets carry a `**bold lead —**` prefix): do not add heading markers or change
line structure. Edit lines in place, keep each rewritten bullet in the same
`- **Bold lead —** regular text` shape, and leave every unchanged line byte-identical —
the `patch` build locates body paragraphs by normalized text (ignoring bold
markers, whitespace differences and leading bullet characters).
