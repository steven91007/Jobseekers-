"""Visa / work-permit sponsorship detection for job postings.

Two layers, deliberately:

1. Rules (:func:`classify_visa`). Fast, offline, multilingual (English, German
   and the Nordic languages). Negative phrases ("no visa sponsorship", "must
   already have the right to work") are checked before positive ones because
   they usually contain the positive phrase.
2. An optional LLM assist (:class:`LLMVisaClassifier`) for postings the rules
   cannot decide. It only runs when ``ANTHROPIC_API_KEY`` is set and
   ``VISA_LLM_ASSIST`` is not ``0``; otherwise undecided postings stay
   ``unknown``. It never overrides a rule-based verdict.

:func:`check_visa_support` ties both together: it fetches each posting's
description from LinkedIn (one request per job, serially, with a pause) and
annotates the job dicts in place with ``visa_status``, ``visa_evidence`` and
``visa_source``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable

import requests

import linkedin_scraper
from linkedin_scraper import _pause, _remaining

log = logging.getLogger(__name__)

DEFAULT_LLM_MODEL = "claude-opus-5"
DETAIL_PAUSE = 1.0
MAX_LLM_CHARS = 12000


class VisaStatus(str, Enum):
    SUPPORTED = "supported"          # posting says it sponsors / helps with visa or relocation
    NOT_SUPPORTED = "not_supported"  # posting says it does not, or requires existing right to work
    UNKNOWN = "unknown"              # nothing decisive in the text
    UNCHECKED = "unchecked"          # detail could not be fetched


LABEL_ZH = {
    VisaStatus.SUPPORTED: "有",
    VisaStatus.NOT_SUPPORTED: "無",
    VisaStatus.UNKNOWN: "不明",
    VisaStatus.UNCHECKED: "未檢查",
}
LABEL_EN = {
    VisaStatus.SUPPORTED: "sponsorship mentioned",
    VisaStatus.NOT_SUPPORTED: "no sponsorship",
    VisaStatus.UNKNOWN: "not stated",
    VisaStatus.UNCHECKED: "not checked",
}


@dataclass
class VisaVerdict:
    status: VisaStatus
    evidence: str = ""
    source: str = "rules"  # rules | llm | none
    confidence: float = 0.0


# --- rules ------------------------------------------------------------------

# Patterns are applied to lowercased text. Keep them specific: a false
# "supported" is worse than an "unknown", because people act on it.
_NEGATIVE = [
    r"\b(no|without|not offer(?:ing)?|do(?:es)? not (?:offer|provide)|cannot (?:offer|provide)|unable to (?:offer|provide))\s+(?:any\s+)?(?:visa|work permit|work-permit|immigration|relocation)\s*(?:sponsorship|support|assistance)?",
    r"\b(?:not|cannot|can't|unable to|won't|will not|do(?:es)? not)\s+(?:be able to\s+)?sponsor",
    r"\bno sponsorship\b",
    r"\bsponsorship (?:is )?not (?:available|provided|offered|possible)",
    r"\bmust (?:already )?(?:have|hold|possess)\s+(?:the |a |an )?(?:valid |existing |current |unrestricted )?(?:right|permit|permission|authori[sz]ation|eligibility|visa)\b[^.]{0,60}\bto work\b",
    r"\b(?:right|eligib(?:le|ility)|authori[sz]ed|permit(?:ted)?)\s+to work\s+in\s+[^.]{0,40}?\b(?:is )?(?:required|mandatory|essential|a must|needed|necessary)",
    r"\b(?:existing|valid|current)\s+(?:work|residence)\s+permit\s+(?:is )?(?:required|mandatory|essential|needed)",
    r"\bmust (?:already )?(?:have|hold|possess)\s+(?:a |an |the )?(?:valid |existing |current )?(?:work|residence)\s+(?:permit|visa|authori[sz]ation)\b",
    r"\b(?:eu|eea|uk|us|swiss|schengen)[\s/-]*(?:citizens?|nationals?|passport holders?)\s+only\b",
    r"\bonly (?:candidates|applicants) (?:who are )?(?:already )?(?:eligible|authori[sz]ed|permitted) to work\b",
    r"\bkein(?:e)?\s+(?:visa|visum|sponsoring|relocation)",
    r"\b(?:gültige|bestehende)\s+arbeits(?:erlaubnis|genehmigung)\s+(?:ist\s+)?(?:erforderlich|vorausgesetzt|notwendig)",
    r"\bing(?:en|a)\s+(?:visum|visa|arbetstillstånd|arbejdstilladelse|arbeidstillatelse)",
    r"\bei\s+(?:visum|työlupa)",
]

_POSITIVE = [
    r"\bvisa\s+sponsorship\b",
    r"\bsponsor(?:s|ship|ing)?\s+(?:a |the |your |an )?(?:work\s+)?(?:visa|permit|work permit)",
    r"\b(?:we|company|employer)\s+(?:can|will|do|are (?:able|happy|willing) to)\s+sponsor\b",
    r"\b(?:visa|work permit|work-permit|immigration)\s+(?:support|assistance|help|process(?:ing)?)\s+(?:is )?(?:provided|available|offered|included)",
    r"\b(?:support|assist(?:ance)?|help)\s+with\s+(?:the |your |a )?(?:visa|work permit|work-permit|immigration|residence permit|relocation)",
    r"\brelocation\s+(?:package|support|assistance|bonus|budget|allowance|help)\b",
    r"\b(?:we )?(?:offer|provide|cover)\s+(?:a |full |partial )?relocation\b",
    r"\b(?:eu\s+)?blue\s*card\b",
    r"\binternational\s+(?:candidates|applicants|talent)\s+(?:are\s+)?(?:welcome|encouraged)",
    r"\b(?:open to|welcome)\s+(?:candidates|applicants)\s+(?:from\s+)?(?:abroad|outside|worldwide|globally|all over the world)",
    r"\bvisum[-\s]?(?:sponsoring|unterstützung|support)|\barbeits(?:erlaubnis|visum)[-\s]?(?:unterstützung|support)|\bwir\s+(?:unterstützen|helfen)\s+(?:dich|sie|bei)[^.]{0,40}(?:visum|umzug|relocation)|\bumzugs(?:kosten|unterstützung|paket)",
    r"\b(?:vi|företaget)\s+(?:hjälper|stödjer|sponsrar)[^.]{0,40}(?:arbetstillstånd|visum|flytt)|\barbetstillstånd\s+(?:ordnas|erbjuds)",
    r"\b(?:vi|virksomheden)\s+(?:hjælper|støtter)[^.]{0,40}(?:arbejdstilladelse|visum|flytning)",
    r"\b(?:vi|selskapet)\s+(?:hjelper|støtter)[^.]{0,40}(?:arbeidstillatelse|visum|flytting)",
    r"\b(?:autamme|tuemme)[^.]{0,40}(?:työlu(?:pa|van)|oleskelulu(?:pa|van)|muut(?:to|on))",
]

_NEG_RE = [re.compile(p, re.I) for p in _NEGATIVE]
_POS_RE = [re.compile(p, re.I) for p in _POSITIVE]


_BOUNDARY = re.compile(r"[.!?\n]")


def _sentence_around(text: str, start: int, end: int, width: int = 200) -> str:
    """The sentence containing the match, clamped to ``width`` chars each side."""
    left = max(0, start - width)
    for m in _BOUNDARY.finditer(text, left, start):
        left = m.end()
    m = _BOUNDARY.search(text, end, min(len(text), end + width))
    right = m.end() if m else min(len(text), end + width)
    return " ".join(text[left:right].split())


def classify_visa(text: str) -> VisaVerdict:
    """Rule-based verdict on one posting's description."""
    if not text or not text.strip():
        return VisaVerdict(VisaStatus.UNKNOWN, "", "rules", 0.0)
    for rx in _NEG_RE:
        m = rx.search(text)
        if m:
            return VisaVerdict(VisaStatus.NOT_SUPPORTED, _sentence_around(text, m.start(), m.end()), "rules", 0.9)
    for rx in _POS_RE:
        m = rx.search(text)
        if m:
            return VisaVerdict(VisaStatus.SUPPORTED, _sentence_around(text, m.start(), m.end()), "rules", 0.85)
    return VisaVerdict(VisaStatus.UNKNOWN, "", "rules", 0.0)


# --- optional LLM assist ------------------------------------------------------

_LLM_SYSTEM = """You read a single job posting and decide whether the employer \
offers visa or work-permit sponsorship (or relocation support that implies it) \
for candidates who do not already have the right to work in the country.

Answer with a status:
- supported: the posting states or clearly implies sponsorship or relocation help.
- not_supported: the posting states it does not sponsor, or requires an existing \
right to work / valid permit / citizenship.
- unknown: the posting does not address it. Prefer unknown over guessing; a wrong \
"supported" misleads people who will apply based on it.

Quote the exact sentence you relied on as evidence (empty when unknown), and give \
a confidence between 0 and 1."""

_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["supported", "not_supported", "unknown"]},
        "evidence": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["status", "evidence", "confidence"],
    "additionalProperties": False,
}


class LLMVisaClassifier:
    """Claude-backed classifier used only for postings the rules left unknown."""

    def __init__(self, model: str = DEFAULT_LLM_MODEL, api_key: str | None = None):
        import anthropic  # lazy: only needed when the assist is enabled

        self._anthropic = anthropic
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3)
        self._fallbacks_ok = True

    def classify(self, text: str) -> VisaVerdict:
        a = self._anthropic
        body = text if len(text) <= MAX_LLM_CHARS else text[:MAX_LLM_CHARS] + "\n[truncated]"
        try:
            resp = self._request(body)
        except a.RateLimitError as e:
            log.warning("visa LLM rate limited (request %s)", e.request_id)
            return VisaVerdict(VisaStatus.UNKNOWN, "", "llm", 0.0)
        except a.APIStatusError as e:
            log.warning("visa LLM API error %s (request %s): %s", e.status_code, e.request_id, e.message)
            return VisaVerdict(VisaStatus.UNKNOWN, "", "llm", 0.0)
        except a.APIConnectionError as e:
            log.warning("visa LLM connection error: %s", e)
            return VisaVerdict(VisaStatus.UNKNOWN, "", "llm", 0.0)

        if resp.stop_reason == "refusal":
            return VisaVerdict(VisaStatus.UNKNOWN, "", "llm", 0.0)
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(raw)
            status = VisaStatus(data["status"])
            return VisaVerdict(status, str(data.get("evidence", "")).strip(), "llm", float(data.get("confidence", 0)))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            log.warning("visa LLM returned unparseable output")
            return VisaVerdict(VisaStatus.UNKNOWN, "", "llm", 0.0)

    def _request(self, body: str):
        common = dict(
            model=self.model,
            max_tokens=1024,
            system=[{"type": "text", "text": _LLM_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": body}],
            output_config={"format": {"type": "json_schema", "schema": _LLM_SCHEMA}, "effort": "low"},
        )
        if self._fallbacks_ok:
            try:
                return self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **common
                )
            except (TypeError, self._anthropic.BadRequestError) as e:
                log.info("server-side fallbacks unavailable (%s); continuing without", e)
                self._fallbacks_ok = False
        return self.client.messages.create(**common)


def llm_from_env() -> LLMVisaClassifier | None:
    """The LLM assist, if configured: needs ANTHROPIC_API_KEY and VISA_LLM_ASSIST != 0."""
    if os.getenv("VISA_LLM_ASSIST", "1").strip() in ("0", "false", "no", "off"):
        return None
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        return LLMVisaClassifier(os.getenv("VISA_LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL, key)
    except ImportError:
        log.warning("ANTHROPIC_API_KEY is set but the anthropic package is missing; pip install anthropic")
        return None


# --- orchestration ------------------------------------------------------------

Classifier = Callable[[str], VisaVerdict]


def check_visa_support(
    jobs: Iterable[dict],
    *,
    llm: Classifier | LLMVisaClassifier | None = None,
    session: requests.Session | None = None,
    timeout: float | None = None,
    max_checks: int | None = None,
    pause: float = DETAIL_PAUSE,
    fetch_detail: Callable[..., dict] | None = None,
    progress: Callable[[int, int, dict], None] | None = None,
) -> int:
    """Annotate each job dict in place. Returns how many postings were checked.

    Adds ``visa_status`` (a :class:`VisaStatus` value string), ``visa_evidence``
    and ``visa_source``. Jobs beyond ``max_checks`` or past the ``timeout``
    budget are left ``unchecked``. ``fetch_detail`` and ``llm`` exist so tests
    can run offline.
    """
    jobs = list(jobs)
    fetch = fetch_detail or linkedin_scraper.get_job_detail
    classify_llm = getattr(llm, "classify", llm)
    deadline = None if timeout is None else time.monotonic() + timeout
    owns_session = session is None and fetch_detail is None
    session = session or (requests.Session() if fetch_detail is None else None)

    for job in jobs:
        job.setdefault("visa_status", VisaStatus.UNCHECKED.value)
        job.setdefault("visa_evidence", "")
        job.setdefault("visa_source", "none")

    checked = 0
    try:
        for i, job in enumerate(jobs):
            if max_checks is not None and checked >= max_checks:
                break
            if _remaining(deadline) <= 0:
                break
            if job.get("visa_status") not in (VisaStatus.UNCHECKED.value, None):
                continue
            if checked and not _pause(pause, deadline):
                break
            detail = fetch(job["job_id"], session, deadline) if fetch_detail is None else fetch(job["job_id"])
            checked += 1
            if detail.get("error") or not detail.get("description"):
                job["visa_source"] = "none"
                if progress:
                    progress(i + 1, len(jobs), job)
                continue
            verdict = classify_visa(detail["description"])
            if verdict.status is VisaStatus.UNKNOWN and classify_llm is not None:
                verdict = classify_llm(detail["description"])
            job["visa_status"] = verdict.status.value
            job["visa_evidence"] = verdict.evidence
            job["visa_source"] = verdict.source
            job["visa_confidence"] = verdict.confidence
            if progress:
                progress(i + 1, len(jobs), job)
    finally:
        if owns_session and session is not None:
            session.close()
    return checked


def status_of(job: dict) -> VisaStatus:
    try:
        return VisaStatus(job.get("visa_status", VisaStatus.UNCHECKED.value))
    except ValueError:
        return VisaStatus.UNCHECKED
