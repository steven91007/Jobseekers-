"""All Jev (TypeSafe System One) wording, thresholds and the truth table for the inbox classifier — one module to
audit and edit (TypeSafe skill: "keep all question wording in one place"; "judgments from Jev, decisions in code").

Docs read 2026-11-02: docs.typesafe.ai/api/system-one.md, primitives/noul.md, primitives/choice.md, confidence.md,
api/errors.md, models.md, openapi.json, skills/typesafe-ai/SKILL.md.
- One request per mail with every question (questions run in parallel; state is paid once).
- Nouls: one judgment each, phrased so that high = yes, concrete `false` criteria naming the confusing case.
- The company Choice has no "none of these" option: per the docs a companion Noul (`names_a_candidate_company`)
  decides the null case, and code reads the Choice only when that Noul says yes.
- Noul has no confidence field; the value is the probability of yes. Thresholds: >= YES act, <= NO no,
  between = "needs review" (its own row in the truth table).
- Price (models.md): input tokens only, $0.042 per million (jev-1.14 / jev-1.13); output tokens are free.
"""
from __future__ import annotations

MODEL = "jev-latest"
YES = 0.8          # act
NO = 0.4           # below: no; between NO and YES: needs review
PRICE_PER_MTOK = 0.042
TEXT_LIMIT = 1500  # characters of mail text sent (snippet, or the body when the snippet alone was ambiguous)

# question id -> (instructions, criteria true, criteria false). Ids are not sent to the model.
NOULS = {
    "auto_reply": (
        "Is `email` an automatic reply, such as an out-of-office notice?",
        "An automatic message sent because the recipient is away or the mailbox auto-responds",
        "A message a recruiter, hiring team or applicant-tracking system sent about the application itself",
    ),
    "mass_mailing": (
        "Is `email` a job alert, newsletter or marketing mail sent to many people?",
        "A list of recommended jobs, a newsletter, a webinar or event invitation, or other bulk marketing",
        "A message about one application the candidate made, such as a receipt, a rejection or an invitation",
    ),
    "rejection": (
        "Does `email` tell the candidate that their application will not go further?",
        "The employer has decided against the candidate for this role, e.g. 'we have decided not to move forward' "
        "or 'leider müssen wir Ihnen absagen'",
        "The application is still open: the mail only thanks them, says it is under review, schedules or moves a "
        "meeting ('unfortunately the slot is taken'), or invites them for another role",
    ),
    "interview_invitation": (   # v2 2026-11-02: v1 asked only about scheduling and missed "we'd like to invite you"
        "Does `email` invite the candidate to an interview, call or meeting about a job?",
        "It invites them to an interview or call, or asks them to book, pick or confirm a time for one",
        "It only says an interview may follow later if they are selected, or describes the process in general",
    ),
    "offer": (
        "Does `email` make the candidate a job offer?",
        "The employer offers the candidate the position or sends an offer letter or contract",
        "Any earlier stage: a receipt, an invitation, a request for salary expectations, or a rejection",
    ),
    "info_request": (
        "Does `email` ask the candidate to send information, documents or answers, or to complete an assessment?",
        "It asks for salary expectations, notice period, documents, a questionnaire, a coding test or similar",
        "It asks for nothing, or only asks the candidate to pick an interview time",
    ),
    "ack": (   # v2 2026-11-02: v1 ("was received") missed "thanks for applying" / "your application was sent to X"
        "Does `email` confirm or thank the candidate for an application they submitted?",
        "It thanks them for applying, says the application was sent, received or is under review",
        "It is not about an application they submitted, e.g. a job alert listing new openings",
    ),
    "names_a_candidate_company": (
        "Is `email` about an application at one of the companies in `candidate_companies`?",
        "The sender, the subject or the text names one of those companies as the employer",
        "None of those companies is the employer, e.g. the mail is from a different company or a job board",
    ),
}
COMPANY_CHOICE = "Which company in `candidate_companies` is the employer this email is about?"

KINDS = ("rejection", "interview", "offer", "request", "ack", "other")   # same names as the regex classifier


def questions(candidates: list[dict]) -> dict:
    """The question map of one request. `candidates`: [{"name", "role"}] (shortlist; may be empty)."""
    q = {k: {"type": "noul", "instructions": ins, "criteria": {"true": t, "false": f}}
         for k, (ins, t, f) in NOULS.items()}
    if not candidates:
        q.pop("names_a_candidate_company")
    else:
        q["company"] = {"type": "choice", "instructions": COMPANY_CHOICE,
                        "criteria": {c["name"]: (f"Applied for: {c['role']}" if c.get("role") else "An employer the candidate applied to")
                                     for c in candidates}}
    return q


def state(mail: dict, candidates: list[dict], text: str) -> dict:
    """Named fields (TypeSafe skill: 'structure with named fields'); only what a person would look at."""
    s = {"email": {"from": mail.get("from", ""), "from_domain": mail.get("from_domain", ""),
                   "subject": mail.get("subject", ""), "date": str(mail.get("date", ""))[:10], "text": text},
         "note": "The candidate is the recipient. They applied to jobs; this mail arrived in their inbox."}
    if candidates:
        s["candidate_companies"] = {c["name"]: (c.get("role") or "") for c in candidates}
    return s


def band(p: float | None) -> str:
    if p is None:
        return "missing"
    return "yes" if p >= YES else "no" if p <= NO else "review"


def decide(answers: dict) -> dict:
    """Truth table: Noul values → kind. Returns {kind, prob, band, why}. kind 'review' = abstain (no proposal).

    | first match                                   | kind       |
    |-----------------------------------------------|------------|
    | auto_reply yes                                | other (auto-reply) |
    | mass_mailing yes                              | other (newsletter) |
    | offer yes                                     | offer      |
    | interview_invitation yes                      | interview  (an invite for another role beats 'position filled') |
    | rejection yes                                 | rejection  |
    | info_request yes                              | request    |
    | any of offer / interview / rejection in review band | review (needs a person) |
    | ack yes                                       | ack        |
    | info_request or ack in review band            | review     |
    | otherwise                                     | other      |
    """
    v = {k: (answers.get(k) or {}).get("noul") for k in NOULS}
    b = {k: band(x) for k, x in v.items()}
    for key, kind in (("auto_reply", "other"), ("mass_mailing", "other")):
        if b[key] == "yes":
            return {"kind": kind, "prob": v[key], "band": "yes", "why": key}
    for key, kind in (("offer", "offer"), ("interview_invitation", "interview"), ("rejection", "rejection"),
                      ("info_request", "request")):
        if b[key] == "yes":
            return {"kind": kind, "prob": v[key], "band": "yes", "why": key}
    unsure = [k for k in ("offer", "interview_invitation", "rejection") if b[k] == "review"]
    if unsure:
        k = max(unsure, key=lambda x: v[x])
        return {"kind": "review", "prob": v[k], "band": "review", "why": k}
    if b["ack"] == "yes":
        return {"kind": "ack", "prob": v["ack"], "band": "yes", "why": "ack"}
    unsure = [k for k in ("info_request", "ack") if b[k] == "review"]
    if unsure:
        k = max(unsure, key=lambda x: v[x])
        return {"kind": "review", "prob": v[k], "band": "review", "why": k}
    return {"kind": "other", "prob": max((x for x in v.values() if x is not None), default=None), "band": "no", "why": "none"}


def decide_company(answers: dict, candidates: list[dict]) -> dict:
    """Null case from the companion Noul, the name from the Choice. {name | None, prob, band}."""
    if not candidates:
        return {"name": None, "prob": None, "band": "no"}
    n = (answers.get("names_a_candidate_company") or {}).get("noul")
    ch = answers.get("company") or {}
    p = (ch.get("probabilities") or {}).get(ch.get("choice"))
    if band(n) == "no":
        return {"name": None, "prob": n, "band": "no"}
    if band(n) in ("review", "missing") or p is None or p < YES:
        return {"name": ch.get("choice"), "prob": p, "band": "review"}
    return {"name": ch.get("choice"), "prob": p, "band": "yes"}
