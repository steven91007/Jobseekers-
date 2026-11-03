# Library fit — how `morning.py fit` scores a JD

A cheap, deterministic triage score for the morning list: "how much of this JD can the experience library
honestly claim?" It is **not** the tailor-cv ATS score and not the Codex 1–10 score of the daily pipeline
(that one also weighs seniority, location and visa). The digest shows both columns side by side.

## Vocabulary (what the scan looks for)

Read-only from `cv/profile/experience.sqlite` (opened `mode=ro`), in this order (later sources override the
coverage of an earlier one for the same term):

1. **Concept aliases + canonical names** (`concept_aliases`, `concepts.canonical`) → the concept's `claim`.
2. **Skills** (`skills.name` + `variants`, parentheses dropped, "certificate only" skipped) → `covered`.
3. **Keywords** (`keywords.term`) → the term's own `coverage` (the writer's per-term decision, e.g.
   TensorRT = gap inside the covered MODEL_OPT concept). Keywords noted `auto: unclassified` are skipped
   (never classified, so neither credit nor penalty).
4. **`tech_terms.txt`** → a library concept only on an exact / normalised alias hit (resolve rules 1–2),
   otherwise "not in library" (credit 0). Fuzzy rules 3–4 are not used: a trigram hit on an unknown tool
   would invent coverage.

Never scored:
- concepts noted "exclude from scoring" (ROLE_WORDS) and LANG_LOC (location and language words);
- the everyday JD words in `generic_terms.txt` (production, design, cost, quality, latency, scalable,
  testing, backend, software engineering …). Almost every JD names them, so they inflated summaries to
  about 94 %.

## Credit per item

| Claim / coverage | Credit |
|---|---|
| covered | 1.0 |
| SoT-supported wording | 0.75 |
| gap · do not claim · not in library | 0 |

An item is the concept when the term's coverage equals the concept claim; a term whose coverage differs
from its concept (a gap member of a covered concept) is its own item.

## Lines and sections

The JD is split into lines. A text with fewer than 3 line breaks is split on sentence ends and `;`. That
covers the daily-pipeline ledger, which keeps a one-line JD digest of 1–3k characters. An inline heading
of 6 words or fewer ("Must-haves: …", "Major Responsibilities: …", "Nice to have: …") switches the
section, and the rest of the sentence is scored. Otherwise the nearest heading above a line sets its
section:

| Section | Weight | Heading signals |
|---|---|---|
| required | 3 | requirements, qualifications, must-have, essential, you'll thrive if, what you bring, your profile, skills, traits |
| duties | 2 | what you'll do, responsibilities, your role, in this role, typical tasks |
| preferred | 1 | nice to have, bonus, plus, preferred, ideally |
| other | 1 | text before any heading |
| skip | 0 | what we offer, benefits, about us, company description, EEO, application process |

A line containing "willingness to learn", "familiarity", "exposure to", "a plus", "ideally"… is capped at
the preferred weight. Each line that names at least one term is **one requirement**, the way a recruiter
reads a bullet. Its credit is the mean credit of its items. A hit masks its span, longest term first, so
"edge" inside "edge deployment" is not counted twice.

```
fit % = 100 × Σ(line weight × line credit) / Σ(line weight)
```

**Too thin.** With fewer than 8 scored items, `fit` is `null` and prints "–" with "summary too thin". The
digest says the same. Run `fit` on the full JD instead.

**Top missing** = items with credit < 1, ranked by the weight they lose across lines
(`weight × (1 − credit) / items in the line`). The digest lists hard gaps (credit 0) before SoT-wording items.

## Calibration (2026-11-02, after the Opus review)

| JD | Full `jd.md` | Ledger-style summary (`scripts/fixtures/*_ledger.txt`) |
|---|---|---|
| TOMRA Senior Python Software Engineer | 73 % | 75 % |
| Mapbox Senior AI Engineer, Location AI | 55 % | 52 % |

**Top missing:**
- **TOMRA:** cloud (do not claim), edge deployment, data security, CUDA / cuDNN.
- **Mapbox:** automotive / voice, distributed processing framework, distributed request path, error
  budgets, SDKs, geospatial.

**Why TOMRA must rank first:** the TOMRA CV reached ATS 80. Mapbox's must-haves include distributed data
engineering and geospatial data, which the library cannot claim. `morning.py selftest` checks the order on
both texts.

**History:**
- Per-term counting tied both JDs at 58 %, because a line listing four tools counted four times.
- Before the review, summaries were scored as one "other" line: TOMRA 59 %, Mapbox 52 %. Generic words
  pushed the day's postings to 93–94 %.

## Limits

- Literal matching: a duty described in words that are not in the vocabulary scores nothing. When a
  posting's missing list looks thin or odd, run `/tailor-cv` Phase 1 (`library_db.py match` / `gaps`) for
  the real analysis.
- No raw JD is stored for new postings. The pipeline keeps only the search run's one-line digest, and
  `scores.json` keeps only a hash. A full `jd.md` exists only once `/tailor-cv` ran.
- Both calibration JDs were already ingested into the keyword table, so their scores are in-sample. Fresh
  postings depend more on the concept aliases and `tech_terms.txt`.
- Never add terms or change coverage to make a posting look better. Coverage decisions belong to
  `/tailor-cv` library mode and the candidate.
