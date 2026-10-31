"""Stub for the symbols ats_parse.extract imports from Resume-Matcher's parser module.

The originals are upload-safety guards (container validation, bounded async jobs) for
untrusted uploads; here the inputs are our own built files, so they are no-ops.
"""
from __future__ import annotations

import re

MAX_PDF_PREFLIGHT_PAGES = 100

# copied verbatim from the original parser.py
_MD_DATE_RE = re.compile(
    r"(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)"
    r"\.?\s+\d{4})"
    r"(?:\s*[-–—]\s*"
    r"(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|Present|Current|Now|Ongoing))?",
    re.IGNORECASE,
)



class DocumentValidationError(Exception):
    pass


def _validate_docx_container(source) -> None:  # noqa: D401 - no-op
    return None


def _validate_extracted_text(text: str) -> None:
    return None


def _validate_pdf_container(source) -> None:
    return None


async def run_bounded_document_job(fn, *args, **kwargs):
    return fn(*args, **kwargs)
