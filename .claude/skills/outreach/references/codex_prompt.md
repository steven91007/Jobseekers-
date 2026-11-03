# Codex review prompt (step 4)

Send through `Agent(subagent_type: "codex:codex-rescue")`. Line 1 is `--fresh`, line 2 the review-only guard.
Fill `{D}` (application dir), `{DRAFT}` (draft path), `{KIND}` (referral-letter | connect-note | follow-up | thank-you),
`{PERSON}`, `{RELATION}`. Replace nothing else.

```
--fresh
Review-only. Do NOT edit or write any files. Do NOT use Notion. There is no AGENTS.md — do not search for it.
Project root: /mnt/e/PythonProject/LinkedinJobSearcher
Application dir: {D}
Read, in this order: {D}/jd.md · {D}/01_jd.md · {D}/03_cv.md · {DRAFT} · cv/profile/candidate_profile.md ·
cv/profile/candidate_confirmations.md · .claude/skills/tailor-cv/references/writing_rules.md ·
cv/profile/bullet_library.generated.md
Source of truth = 03_cv.md (the CV the recruiter holds) + bullet_library.generated.md + candidate_confirmations.md + candidate_profile.md.
The draft is a {KIND} to {PERSON} ({RELATION}). It will be sent by a human, never by a tool.

A. FACT-CHECK against 03_cv.md and the SoT, sentence by sentence. Flag: any claim, number or unit the CV does not carry; upgraded wording
   (stakeholder management → "led the plan"; "Phase 1 proof of concept" → shipped/production; "LLM outage" fallback → general reliability;
   "structured LLM output" → generic AI; "target users" → users); invented relationship details (how, how long, where they met);
   invented user feedback, teams, titles or motives; MaterialCheck described beyond phase 1 POC delivered on time, phases 2-3 planned.
B. TONE AND LENGTH for {KIND}: referral-letter = referrer's voice, third person about the candidate, 200-280 words, two project cards led by
   their title, each mapped to named JD duties, not CV-like; connect-note = first person, <= 300 characters, one concrete hook, no pitch;
   follow-up = <= 120 words, one ask, polite, no pressure; thank-you = <= 150 words, 1-2 specific interview topics only if real.
C. JD MAPPING: is every mapped duty really in the JD text; is the strongest JD requirement the CV can support missing; anything that
   would make the recipient forward it more easily.

Output: numbered findings, each "line/sentence · A|B|C · problem · exact replacement wording". Max 12, most serious first.
End with verdict PASS or REVISE.
```

Apply the findings once, re-run `check`. A finding that needs a fact only the candidate has becomes a `TODO candidate:` line,
never a guess.
