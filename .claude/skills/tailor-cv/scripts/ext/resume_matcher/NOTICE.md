# Vendored from srbhr/Resume-Matcher (Apache-2.0)

Source: https://github.com/srbhr/Resume-Matcher — `apps/backend/app/services/{ats.py,text_match.py,ats_parse/}`
at commit f6296a5 (2026-10-23). Copied 2026-10-29 for the tailor-cv skill's external ATS check.

Modifications (so the modules run without the web app, database or an LLM provider):
- `app/__init__.py`, `app/services/__init__.py`, `app/services/ats_parse/__init__.py` emptied (the
  originals import LiteLLM, the LLM parser and the PDF renderer).
- `app/services/ats_parse/roundtrip.py` reduced to the `CONDENSED_ORDER` constant.
- `app/services/parser.py` replaced by a stub: `_MD_DATE_RE` copied verbatim; the upload-safety
  validators and `run_bounded_document_job` are no-ops (inputs are our own built files).
Everything else is unmodified. Python deps: pdfminer.six, python-docx, pydantic (project `.venv`).
