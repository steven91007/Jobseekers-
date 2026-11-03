# Codex review prompt (step 4)

Send through `Agent(subagent_type: "codex:codex-rescue")`. Line 1 is `--fresh`, line 2 the review-only guard.
Fill `{D}` (application dir) and `{JD}` (JD code). Replace nothing else.

```
--fresh
Review-only. Do NOT edit or write any files. Do NOT use Notion. There is no AGENTS.md — do not search for it.
Project root: /mnt/e/PythonProject/LinkedinJobSearcher
Application dir: {D}
Read, in this order: {D}/jd.md · {D}/01_jd.md · {D}/03_cv.md · {D}/10_interview.json · {D}/02_company.json (sections interview_process and interview_reports only, if the file exists) ·
cv/profile/candidate_profile.md · cv/profile/candidate_confirmations.md ·
.claude/skills/tailor-cv/references/writing_rules.md · cv/profile/bullet_library.generated.md
Source of truth = the cited library bullets (bullet_library.generated.md, read-only export of the experience DB) + candidate_confirmations.md + candidate_profile.md. Anything else is unverified.

Each item in 10_interview.json is a spoken interview answer by the candidate. `bullets` lists the evidence codes, `notes` may cite CONF items.
`gap` items must say plainly that the experience is missing. `ask` items are questions the candidate asks the interviewer. `TODO candidate:` marks a fact the library does not hold; that is allowed and must stay.

A. TRUTH. For every item list each claim that the cited bullets, their card contexts, the CV (03_cv.md) or a CONF item do not prove:
   invented motives, causes, results, team sizes, baselines, tools, self-characterisations ("I always…"), or a number not in the evidence.
   Flag any gap answered as if covered, any MaterialCheck wording beyond "phase 1 in production (200+ users), phases 2–3 planned",
   and any breach of writing_rules.md. Also flag a TODO that should exist but is missing (a claim that needs a fact only the candidate has).
B. REALISM. As a hiring manager for this JD: which questions would you actually ask that are missing; which items are unrealistic or
   duplicates (the same story twice); which answers are evasive, over-long for speech, or coaching ("a good structure is…") instead of an answer.
   interview_reports (R#) are questions real candidates say this company asked: flag a report no item practises (`reported` field), an item
   whose question drifts from the report it cites, and a reported stage (coding test, take-home, presentation) the prep does not prepare for.
   Reports are never evidence for an answer.

Output: numbered findings, each "item # · A|B · problem · exact replacement wording or fix". Max 15 findings, most serious first.
End with verdict PASS or REVISE.
```

Apply the findings once in `{D}/10_interview.json`, re-run `check` and `render`. A finding that needs a candidate fact becomes a
`TODO candidate:` in the answer, never a guess.
