# Evidence verification rules (used by the writer, the reviewer and the orchestrator)

Every claim that appears in a tailored CV — in the Profile, the Delta bullets or the
Skills block — must have a row in the **claim ledger** of `02_evidence_map.md` and a
status from the list below. The reviewer re-audits those statuses in every review.

## Claim statuses

| Status | Meaning | May appear in the CV? |
|---|---|---|
| **Verified** | Supported by documented implementation evidence (Notion KB page, commit, ADR, benchmark log, release note) that you actually read — for high-severity claims, re-read at review time. | Yes. |
| **Template** | Stated only in the candidate's base CV; no independent source, scope usually unknown (e.g. "10× throughput", "100+ engineers"). | Yes, worded as in the template. Reported separately from Verified. A Template **achievement or metric** may **not** lead the Profile or a bullet unless the candidate confirms its scope (then it becomes User-reported). **Identity facts** — name, titles, employers, dates, years of experience derived from those dates, education, certifications, languages — are Template by status but exempt from the lead restriction: the Profile's fixed bold opening clause (e.g. "Senior AI Engineer with 5+ years of industry experience") is template text and stays. |
| **User-reported** | Stated by the candidate (in chat, in `cv/profile/`, or under "Candidate confirmations"), not independently verified. | Yes, worded exactly as reported. Note it in the ledger. |
| **Inferred** | A reasonable interpretation of evidence, not stated anywhere. | Only if low-severity and the wording stays within what the evidence shows. Medium- or high-severity inferred claims block release. |
| **Missing Evidence** | No source establishes it. | No — at any severity it blocks release until it is removed, reworded to what the evidence shows, or confirmed by the candidate. |
| **Contradicted** | Conflicts with project records, benchmark results or another claim. | Never. Always blocks release until fixed. |

**Severity** (set by the reviewer per claim): *high* = a headline metric, a production /
deployment / ownership claim, a hard requirement, or anything an interviewer will probe
first; *medium* = supporting detail that changes the meaning if wrong; *low* = wording
or emphasis.

A claim that cannot be fixed or verified within the revision budget is labelled
**Needs Evidence** in the final report and the CV is not released as "Ready to Apply".

## Ledger format (in `02_evidence_map.md`)

```
## Claim ledger
| ID | Claim (as it appears or will appear in the CV) | Status | Source (Notion URL / template / user message / profile file) | Measurement scope (baseline · sample · conditions · environment) | Notes |
|----|---|---|---|---|---|
| C1 | 60% → ~96% field accuracy | Verified | <url> | rule-based baseline vs LLM pipeline · <n> documents · internal benchmark | percentage points, not % |
```

When a claim leaves the CV, keep its row as history and write `(removed)` in Notes —
`review_tools.py ledger` then counts it under `retired`, not as a live claim.

Fill **Measurement scope** for every number. If any part is unknown, write `unknown`
— do not guess it.

## Measurement-integrity rules (non-negotiable)

1. **Component ≠ pipeline.** A model-conversion or single-stage benchmark (e.g. ONNX vs
   Paddle on CPU) is a component result. Do not present it as an end-to-end pipeline or
   product speed-up unless the evidence measured the whole pipeline.
2. **Similar ≠ identical.** "Matching outputs" / "identical results" requires an explicit
   parity check in the evidence (e.g. 100% argmax match on a stated set). Similar
   scores or "no visible difference" support only "comparable accuracy".
3. **Configured ≠ executed.** A CI/CD configuration (pipeline file, Dockerfile, deploy
   script) does not prove it ran successfully on a production runner. Claim execution
   only when logs, release notes or the candidate confirm it.
   **Reader test:** if a hiring manager reading the line would assume a stage ran,
   shipped or reached production, the ledger must hold an execution record (log, release
   note, candidate confirmation). Otherwise use a design verb ("designed", "added a
   manual X job", "prepared") or omit the stage. Literal hedges such as "Set up X to do
   Y" or "with manual approval for Y" do **not** pass the reader test on their own.
4. **Staging ≠ production.** Say "staging" when only staging is evidenced. "Production"
   requires evidence of a production deployment or use.
5. **Small samples stay small.** A result on a small evaluation set (n < 100) is written
   as a percentage *with the base* ("55% → 85% (n=20)") and never generalised to
   "production accuracy".
6. **Percent vs percentage points.** 60% → 96% accuracy is **+36 percentage points**, not
   "+60%". Relative improvements ("6× faster", "−66% time") must state the baseline.
7. **Keep the original scope.** Preserve the baseline, sample size, dataset, hardware
   (CPU/GPU) and conditions of every number. Do not drop a qualifier that makes the
   number honest ("on CPU", "across 10 PDFs", "on an 11-document benchmark").
   Keep the qualifiers a reader needs to *interpret* the number; lab parameters (thread
   counts, call counts, IoU thresholds, seeds) belong in the ledger's Measurement scope,
   not in the CV line.
8. **Causation follows the evidence.** Attribute an improvement to the change the
   evidence names (e.g. box deduplication), not to an adjacent piece of work listed in
   the same bullet. **Placement implies causation:** a result inside a bullet reads as
   produced by that bullet's work. If the result came from a different change, either
   name that change in the same bullet or move the result to the bullet about that
   change — deleting a causal verb is not enough.
9. **Tools by their real role.** MLflow registry ≠ serving; ONNX format ≠ ONNX Runtime;
   a library you called ≠ a system you built.
10. **Logistics are never assumed.** Do not state or imply work authorization, visa
    sponsorship, relocation eligibility, availability date or language level beyond
    what the candidate has confirmed or the template already says.
11. **No new technologies.** Never add a technology, responsibility, certification or
    deployment status that is not in the ledger — not even to the Skills block.

## Orchestrator duties

- Before every review, make sure every CV claim has a ledger row; add missing rows as
  `Missing Evidence` rather than letting a claim go unaudited.
- When the candidate confirms something in chat, add it under
  `## Candidate confirmations` with the date and change the row to `User-reported`.
- Run `python3 -I SKILL_DIR/scripts/review_tools.py ledger --evidence <dir>/02_evidence_map.md`
  to get the status counts for the final report.
