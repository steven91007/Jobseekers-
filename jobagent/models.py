"""Normalized job record and the LLM fit assessment schema."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


@dataclass
class Job:
    source: str                 # linkedin | greenhouse | ashby | lever
    source_id: str
    title: str
    company: str
    location: str
    region: str                 # DE | NL | IE | REMOTE_EU
    url: str
    posted_at: str = ""         # ISO 8601; date-only for LinkedIn
    work_type: str = ""
    description: str = ""       # plain text, may be filled lazily
    company_tier: str = "other"  # ai_native | ai_heavy | other
    ats_slug: str = ""          # for lazy detail fetches on ATS jobs
    extra: dict = field(default_factory=dict)

    @property
    def job_key(self) -> str:
        return f"{self.source}:{self.source_id}"


class Assessment(BaseModel):
    """What the scorer returns for one job. Every field is required (strict mode)."""

    fit_score: int = Field(description="0-100: how well this job matches the candidate profile.")
    apply_priority: Literal["now", "soon", "skip"] = Field(
        description="now = strong fit, apply this week; soon = decent fit; skip = poor fit or blocker."
    )
    employer_type: Literal[
        "ai_product_company", "tech_company_with_ai_team", "consultancy_or_agency",
        "recruitment_agency", "enterprise_non_tech", "unknown",
    ]
    seniority: Literal["intern_or_student", "junior", "mid", "senior", "staff_or_lead", "manager", "unknown"]
    local_language_required: Literal["none", "german", "dutch", "german_nice_to_have", "dutch_nice_to_have", "unknown"]
    language_evidence: str | None = Field(description="Short quote from the JD about language, or null.")
    visa_or_relocation: Literal["sponsorship_or_relocation_offered", "must_have_eu_work_permit", "not_mentioned"]
    remote_policy: Literal["onsite", "hybrid", "remote", "unknown"]
    salary: str | None = Field(description="Salary range exactly as stated, or null.")
    must_haves: list[str] = Field(description="Up to 6 hard requirements from the JD.")
    gaps: list[str] = Field(description="Requirements the candidate appears to miss, up to 4.")
    why_fit: str = Field(description="Two sentences on why this is or is not a fit.")
    suggested_pitch: str = Field(description="Three short lines the candidate could lead an application with.")
