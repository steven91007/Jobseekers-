# Job keywords for the external ATS check (`$D/07_job_keywords.json`)

Written by the writer (Opus) in step 1 from `jd.md`, acting as the "coding agent" provider of
Resume-Matcher. Follow its `EXTRACT_KEYWORDS_PROMPT` literally — terms in posting order, the
JD's own spelling, no synonyms, no terms the JD does not contain:

```
Extract job requirements as JSON. Output ONLY the JSON object, no other text.
{
  "company": "<hiring company>", "role": "<job title exactly as written>",
  "required_skills": [...],          # from "Expertise" / "Proficiency" / must lines
  "preferred_skills": [...],         # "Knowledge" / "willingness to learn" / nice lines
  "experience_requirements": [...], "education_requirements": [...],
  "key_responsibilities": [...],     # the task bullets, verbatim
  "keywords": [...],                 # the remaining nouns of the task bullets + culture lines
  "experience_years": <int|null>, "seniority_level": "<junior|mid|senior|lead>"
}
```

The four buckets of resumematcher.fyi's "dissect the job description" map onto the file:
required skills → `required_skills`, preferred → `preferred_skills`, responsibility verbs →
`key_responsibilities` (verbatim bullets), domain terms → `keywords`. The ATS check counts how
often each term repeats in `jd.md`; terms at ≥ 3× must appear in the Summary and the Skills items.

Rules for the lists (they drive the score):
- **Hard rule: one concept per term**, as the JD writes it ("CI/CD", "on-prem", "REST APIs"). The
  matcher is literal, so a multi-concept phrase can never be found in any CV. Split "TensorRT,
  ONNX, CUDA, cuDNN" into four terms; keep "ML/DL tooling" as one if the JD writes it so. From the
  Scout24 raw file (2026-10-24):
  - "implementation, evaluation, deployment, and operation" → "implementation" · "evaluation" ·
    "deployment" · "operation"
  - "prompt and context engineering" → "prompt engineering" · "context engineering"
  - "reliable, scalable applications in production" → "reliable" · "scalable" · "production"
  After writing the file run `library_db.py normalize-keywords <file>` (splits on ", " / and / or in
  phrases of ≥ 3 words, shares a head noun like "engineering", dedupes across the three lists in
  posting order); read the diff, then `--write`. Its splits are a heuristic: the third example
  above comes out as "reliable" · "scalable applications in production" — finish it by hand.
- A term the JD hyphenates stays hyphenated; one it does not, stays unhyphenated — the matcher is
  literal, so the CV must use the JD's spelling (writing_rules → "ATS literal spelling").
  When the JD writes both forms ("document-processing" and "document processing"), list both —
  `normalize-keywords` adds the unhyphenated one after the hyphenated term.
- 40–60 terms in total is normal; do not pad with generic words (team, work, strong).
- Save with `"_note"` naming the JD source; keep the file when the JD changes only cosmetically.
