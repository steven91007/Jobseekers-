# Experience library DB — v3 addendum: concept layer + hybrid matching (2026-10-31)

Extends `library_db_spec.md` (v1) and `library_db_spec_v2.md`. Stdlib only for the core (WSL
`python3` 3.12); the optional dense step runs in the Windows venv (`numpy`, `onnxruntime` already
installed via Resume-Matcher). Do not change existing CLI semantics; add.

## 0. Problem (from the 6-JD run)

The keyword map is a flat alias list (303 terms). 86 of them are `SoT-supported wording`: the
candidate *can* claim them, but nothing on the CV says the word, and nothing links the term to the
bullet that proves it. Example — all of these are one concept and all are proven by DT1/D1
(LangGraph agent) + MC1, yet they are 7 unrelated rows with 3 different coverages:

    agentic workflows · AI agents · agent frameworks · workflow agents · LLM orchestration ·
    agent orchestration · multi-agent orchestration          →  LangGraph (tool) in the body only

So `match`/`pick` only score literal hits in `keywords_carried` + body, and the writer re-decides
by hand that "LangGraph" means "agentic workflow". Two things are missing:

1. a **concept node** between JD phrasing and evidence (term → concept → bullets/skills/leads);
2. a **terminology alignment** step that puts the JD's noun for the concept on the CV (in the lead)
   while the tool name stays in the body — so both the human reader and the ATS match.

## 1. Approach decision

| Option | Verdict | Why |
|---|---|---|
| GraphRAG (LLM-built graph + community summaries) | no | 26 bullets / 43 skills; non-deterministic; no explanation of *why* a bullet matched; nothing to summarise. |
| Dense RAG only (embed bullets, cosine to JD) | no as core | cannot explain or audit a match; "agentic" ≈ "autonomous" false positives → over-claiming risk; needs a model in the loop for every run. |
| word2vec / word vectors | no | needs a training corpus we do not have; sentence-transformer embeddings dominate it anyway. |
| **Hybrid: curated concept graph (tiny KG) + alias-expanded BM25 + optional dense for *suggestions only*** | **yes** | deterministic + explainable core; the concept graph is the claim-governance layer we already need (covered / SoT-supported / do-not-claim); dense similarity only proposes alias→concept for unknown JD terms, the agent confirms, the result is stored. |

The "LLM in the loop" is the agent itself (Fable at JD-analysis time, Codex at review), once per
new term, and the answer is persisted — no runtime model dependency for `pick`/`render`.

## 2. Schema additions

```
concepts(code PK, name, kind 'duty'|'skill'|'tool'|'domain'|'practice', canonical, esco,
         definition, claim 'covered'|'SoT-supported'|'gap'|'do not claim', notes, notion_id)
concept_aliases(concept_code FK, alias, source 'jd'|'esco'|'manual', jd, UNIQUE(alias COLLATE NOCASE))
concept_evidence(concept_code FK, kind 'bullet'|'skill'|'lead'|'scope'|'context', code, strength 'direct'|'implied', note)
concept_relations(src FK, rel 'is_a'|'part_of'|'implemented_by'|'enables', dst FK)
keywords  + concept_code FK NULL          -- every existing term gets one (bootstrap below)
bullet_leads + concept_code FK NULL       -- which concept a lead variant names (for `align`)
```

Seed: new top-level `concepts` list in `library_seed.json`:
```json
{"code":"AGENTIC","name":"Agentic workflows / LLM agents","kind":"practice","canonical":"agentic workflows",
 "aliases":["AI agents","agent frameworks","workflow agents","LLM orchestration","agent orchestration",
            "multi-agent orchestration","intelligent agents","agents","tool calling"],
 "evidence":[["bullet","DT1","direct"],["bullet","D1","direct"],["bullet","MC1","implied"],
             ["skill","LangGraph","direct"],["skill","LangChain","direct"],["skill","tool calling","direct"]],
 "relations":[["implemented_by","LANGGRAPH"]], "claim":"covered", "esco":"", "definition":"..."}
```
Expected size: 40–60 concepts covering all 303 terms. Tool names (LangGraph, MLflow, Docker, ONNX…)
are `kind: tool` concepts with `implemented_by` edges to the practice they prove (AGENTIC, MLOPS,
MODEL_OPTIMIZATION…). `coverage` on `keywords` becomes derived: term → concept.claim (keep the
column; `init` fills it from the concept unless a `coverage_overrides` row exists).

## 3. CLI additions

### 3.1 `concepts-bootstrap [--write-proposal path]`
One-off helper for seeding: cluster the 303 keywords by (a) shared head noun after stop-word
removal (`agent*`, `orchestrat*`, `pipeline*`…), (b) existing `keyword_bullets` / `keyword_skills`
overlap, (c) char-trigram Jaccard ≥ 0.5. Print proposal groups as JSON (`{"proposed_code":…,
"terms":[…], "shared_bullets":[…], "shared_skills":[…]}`); the agent reviews, names, and writes the
`concepts` seed section. Never writes the seed itself.

### 3.2 `concept <CODE>` · `concepts [--kind X] [--claim X] [--orphans]`
Show a concept with aliases, evidence, relations, JDs that used any alias. `--orphans` = concepts
with no evidence (true gaps) and keywords with no concept (must be 0 after bootstrap).

### 3.3 `resolve <term> [--dense]` — term → concept
Deterministic cascade, stops at first hit, prints the rule that fired:
1. exact alias (NOCASE) · 2. normalised (lower, strip plural `s`, `-`/`/`/` ` folded, `&`→`and`) ·
3. token-set match ≥ 0.67 against aliases (`tokens()` already exists) · 4. char-trigram Jaccard ≥
0.6 · 5. `--dense`: cosine of MiniLM embeddings (see §5), top-3 with scores, marked `SUGGEST` —
never auto-assigned. Unknown → `NEW` (same as `match` today).

### 3.4 `match` / `pick` — concept-aware scoring (replace literal-only)
For each JD term: `resolve` → concept → evidence. Bullet score =
```
Σ_concepts  w_type(required 3 / preferred 2 / keyword 1) × s(direct 1.0 / implied 0.5) × idf(concept)
+ BM25(bullet_doc, jd_terms)                  -- bullet_doc = body + all leads + keywords_carried
                                               --            + names/aliases of its concepts (alias expansion)
```
`idf(concept)` = log(N_bullets / n_bullets_with_concept) so "Python" (every bullet) stops dominating.
BM25 is ~40 lines of stdlib (k1 1.2, b 0.75). Print per bullet: score, concepts hit (with the JD
phrase that hit them), literal hits — the current output stays as the "literal" column. `pick`
keeps all v2 rules (archetype cards, ≤ 3 per card, DT2a/DT2b exclusion, `fit`), only the ranking
function changes. Acceptance: for the 6 recorded JDs the suggestion's ATS score is ≥ the recorded
manual pick (`ingest-ats` data is already in the DB to check this offline).

### 3.5 `align --jd <Co> [--picks "…"]` — terminology alignment (the user's "改成 JD 一樣的名詞")
For each picked bullet and each of its concepts that the JD names:
- **lead**: if a `bullet_leads` row names that concept (`concept_code`) or contains the JD alias
  verbatim → recommend that `lead_ord`. Else emit a **lead proposal** `"<JD alias as duty phrase>
  (<tool>)"` e.g. `Agentic workflow orchestration (LangGraph)`, `Model optimization & edge
  inference (ONNX Runtime)`, flagged `NEW LEAD → library mode` (append to seed `leads`, then
  `init`). The lead is where the JD noun goes; the body keeps the tool name → both match.
- **body**: only `spelling` substitutions already recorded on the keyword (`computer vision` vs
  `computer-vision`, `CI/CD` vs `CI-CD`); never semantic rewrites. Semantic body changes are a
  library-mode decision (new bullet variant with CONF).
- **skills rows**: the spelling variant / row label that equals the JD alias (v2 logic, now fed by
  concept aliases too).
- **summary hints**: list concepts the JD weights `required` that the picked lines name only via
  tools → candidates for Codex's Summary prompt (`codex_prompts.md`) so the Summary says the JD
  noun ("agentic workflows", "ML tooling") once.
Output: a table `line · concept · JD phrase · current wording · action (use lead_ord n | NEW LEAD |
spelling | summary)`, then the pick string with lead ords applied. Exit 0; it is advisory.

### 3.6 `gaps --jd <Co>`
Concepts the JD names with `claim` ≠ covered, grouped: `SoT-supported` (evidence implied → write a
lead/bullet in library mode, cite CONF), `gap` (learn / omit), `do not claim`. Replaces reading
`07_external_ats.md` "Missing terms by library status" by hand; `ats_check.py` §8 (v2) should
print concept names next to the terms.

### 3.7 `ingest-ats` — concept-aware
New terms go through `resolve`; a confident rule-1..4 hit assigns `concept_code` and inherits the
concept's `claim`; otherwise `notes = "auto: unresolved (<Co> <date>)"` and `--dense` suggestions
are printed for the agent to confirm with `set-concept <term> <CODE>` (new subcommand; also sets
`coverage` from the concept).

## 4. Pipeline changes (`SKILL.md`)
- Phase 1 (JD analysis): after `normalize-keywords`, run `match` (concept-aware) and `gaps --jd`;
  unresolved terms → agent classifies with `set-concept` (new concept only if truly new: add to seed).
- Phase 2 (picks): `pick --jd` → `align --jd` → apply `use lead_ord` actions; `NEW LEAD` proposals
  are shown to the user as a library-mode batch (one question, approve/edit), appended to the seed,
  `init`, then `assemble_cv.py --jd`. Rule "never write leads at pick time" is preserved: the lead
  is written into the library first, then picked.
- Phase 4 (after ATS): `ingest-ats` as today.
- `export-md` and `notion-payload concepts` mirror the concept table to Notion (5th database
  "Concepts", relations to Bullets/Skills/JD keyword map via `notion_id`).

## 5. Optional dense step (Windows venv, suggestions only)
`scripts/dense.py` (run via `.venv/Scripts/python.exe`): `all-MiniLM-L6-v2` ONNX (≈ 90 MB, one-time
download to `cv/profile/models/`, gitignored) through `onnxruntime` + `tokenizers`/ a bundled
WordPiece vocab; embeds concept names + aliases + definitions once into `cv/profile/concepts.npy`;
`dense.py suggest "<term>" [-k 3]` prints cosine top-k. `library_db.py resolve --dense` shells out
to it when present and prints `SUGGEST` rows; if the venv/model is absent it says so and falls back
to rules 1–4. No ATS score, pick or render ever depends on it.

## 6. Acceptance
1. `init` + `selftest` keep the v1/v2 checks green; `concepts --orphans` prints 0 keywords without
   a concept.
2. `resolve "AI agents"`, `resolve "LLM orchestration"`, `resolve "agentic workflow"` → AGENTIC
   (rules 1–3); `resolve "Agentic Decision Platform"` → the `do not claim` concept it was given.
3. `pick --jd Scout24` ranks DT1 in the top 6 and `align --jd Scout24` recommends a lead containing
   `agentic` (existing variant or NEW LEAD proposal).
4. For all 6 recorded JDs: concept-aware `pick` ATS ≥ recorded manual pick; `diff --jd TOMRA` still
   identical to `v3_cards/03_cv.md`.
5. `align --jd TOMRA` yields 0 NEW LEAD proposals for the recorded picks (the TOMRA leads are
   already JD duty phrases) — regression guard.
6. `export-md` contains a "Concepts" section; `notion-payload concepts` validates against the
   other payloads' shape.

## 7. Non-goals
No change to `build_cv.py` / `layout_check.py`; no Notion API calls from the script; dense step is
never required; bodies are never rewritten automatically.
