# Writing rules (bullets, Summary, headline)

## Truth
- Numbers and business value come from the candidate (library "Summary facts",
  `candidate_confirmations.md`). Notion supports wording; it never sets a metric.
- Keep a number's scope when it is part of its meaning ("per document", "on 10 PDFs").
  Drop lab detail (percentage points, n=19 cells, thresholds, test counts).
- Execution words (deployed, released, in daily use, live) only when the library or a
  confirmation says so. Staging ≠ production. "Matching" / "identical" only with a parity check.
- A result belongs to the work that caused it (placement implies causation).
- Never state or imply visa status or availability. "Open to relocate to <country>" is
  confirmed by the candidate; nothing beyond it.
- Real employer titles never change; only the headline and Summary opening carry the JD title.

## Business value first
Lead with who benefits and what improves: the decision made faster, the time cut, the
volume handled, the scale of the team or budget — then how it was built. Prefer
"Sped up delivery decisions in a €7.6B division" over "Built a LangGraph agent".

## Project cards (Delta block)
The Delta block is a scope line + 2 cards (3 only when the JD needs it; then 2 bullets each).
- **Scope line** (1 line, italic): what the candidate owns + who the users are. Library text verbatim.
- **Card title line** (≤ 2 lines): `**<Title>** — <context>`. The context answers *what it is ·
  who uses it · scale / business metric*. Title variants come from the library; a theme card that
  spans several services says so (plural, or names them) and never implies one system.
- **Capability bullets** (2–3 per card, ≤ 2 lines): `**<JD duty phrase> —** <architecture / what was
  built> ; <outcome, or decision + why>`. **The bold lead is the job's language, not the result**:
  "ML training pipelines & evaluation", "Containerization & CI/CD", "REST API design & maintenance".
  3–6 words, taken from the bullet's `leads:` list in the library; pick the variant closest to the
  JD's own task wording. Metrics move into the body (technical) or the context line (business).
- A card's bullets may come from different codebases; the card title/context must stay true for all
  of them. One bullet appears once per CV even if the library lists it under two cards.
- Result + decision + reason still applies inside the body: say what was built, then the decision
  and **why** (trade-off, risk avoided) from the library's "Decision rationales". Never invent a rationale.

## ATS literal spelling
The external check matches whole words with no synonyms, and scans Summary, experience,
education and Skills *items* only (row labels and the headline do not count). So each JD term
from `07_job_keywords.json` that the SoT supports should appear once in the JD's exact spelling:
"computer vision" (not only "computer-vision"), "data ingestion" (not "from … data"), "MinIO
(object storage)". Never add a term the SoT does not support; wording changes go through the library.
- A JD term repeated ≥ 3× (the ATS check lists them) belongs in the Summary **and** the Skills items.
- Abbreviations the JD uses in both forms appear once in each form ("continuous integration (CI/CD)").
- Adjacent experience borrows the JD's category next to the real tool ("MinIO (object storage)"),
  never the JD's tool name.

## Voice
- Plain verbs: built, moved, cut, replaced, added, shipped. Implied-subject CV style is fine.
- Banned: leveraged, spearheaded, robust, seamless, cutting-edge, state-of-the-art,
  empowering, driving, passionate, synergy, holistic, impactful, "solutions",
  "end-to-end"/"production-ready" more than once, triads for rhythm.
- No verb-less tool lists; ≤ 3 numbers and ≤ 1 parenthetical per bullet.

## Length (A4 one page, template v3: 10 pt body, single line height, photo top-right)
- Bullet ≤ ~225 chars incl. `**` markers → must render in 2 lines (layout check is the judge); bold lead 3–6 words, ending `—**`.
- Card title line ≤ ~230 chars → 2 lines. Scope line ≤ ~115 chars → 1 line.
- Summary 400–470 chars incl. markers → ≈ 4 lines; max 3 metrics (a from → to pair is one
  metric; "6 years" in the fixed opening is identity and does not count).
- Headline: `**<JD TITLE>  |  <AREA>  •  <AREA>**`, caps, fits one line beside the photo.
- Delta block: 1 scope line + 2 cards × 3 bullets (or 3 cards × 2) = 5–6 bullets.
- Skills: 6 rows, `**Category:** item · item · item`, each one rendered line; labels are the library's
  Skills categories (not JD responsibilities), ordered for the JD; items only from that category
  (no new technologies, no methods/soft skills).

## Markdown
Edit lines in place in the template copy; unchanged lines stay byte-identical (the
`patch` build matches paragraphs by normalized text). `<!-- -->` comments are stripped.
