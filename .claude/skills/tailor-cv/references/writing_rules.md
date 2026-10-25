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

## Senior bullets: result + decision + reason
Each bullet = result (lead) → what was built → the decision and **why** (trade-off, risk
avoided), using the library's confirmed "Decision rationales". Trim incidental detail to
make room for the why; never invent a rationale.

## Voice
- Plain verbs: built, moved, cut, replaced, added, shipped. Implied-subject CV style is fine.
- Banned: leveraged, spearheaded, robust, seamless, cutting-edge, state-of-the-art,
  empowering, driving, passionate, synergy, holistic, impactful, "solutions",
  "end-to-end"/"production-ready" more than once, triads for rhythm.
- No verb-less tool lists; ≤ 3 numbers and ≤ 1 parenthetical per bullet.

## Length (one A4 page, template layout)
- Bullet ≤ ~225 chars incl. `**` markers → must render in 2 lines (layout check is the judge); bold lead 4–8 words, ending `—**`.
- Summary 400–470 chars incl. markers → ≈ 4 lines; max 3 metrics (a from → to pair is one
  metric; "6 years" in the fixed opening is identity and does not count).
- Headline: `**<JD TITLE>  |  <AREA>  •  <AREA>**`, caps, fits one line.
- Delta block: exactly 5 bullets.
- Skills: 6 rows, `**Category:** item · item · item`, each one rendered line; labels are the library's
  Skills categories (not JD responsibilities), ordered for the JD; items only from that category
  (no new technologies, no methods/soft skills).

## Markdown
Edit lines in place in the template copy; unchanged lines stay byte-identical (the
`patch` build matches paragraphs by normalized text). `<!-- -->` comments are stripped.
