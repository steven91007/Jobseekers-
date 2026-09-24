"""Region classification, title filtering, date parsing and duplicate keys."""

import re
from datetime import datetime, timezone

from .config import REMOTE_EU

# --- region -----------------------------------------------------------------

_DE = re.compile(
    r"\b(germany|deutschland|berlin|munich|münchen|muenchen|hamburg|frankfurt|cologne|köln|"
    r"stuttgart|düsseldorf|dusseldorf|heidelberg|tübingen|tubingen|karlsruhe|leipzig|dresden|"
    r"nuremberg|nürnberg|hannover|bonn|darmstadt|mannheim|potsdam|aachen|freiburg|essen|dortmund)\b",
    re.I,
)
_NL = re.compile(
    r"\b(netherlands|nederland|holland|amsterdam|rotterdam|utrecht|eindhoven|the hague|den haag|"
    r"delft|leiden|groningen|haarlem|amstelveen|hilversum|nijmegen|enschede)\b",
    re.I,
)
_DUBLIN = re.compile(r"\bdublin\b", re.I)
_OTHER_IRISH_CITY = re.compile(r"\b(cork|galway|limerick|waterford|belfast)\b", re.I)
_IRELAND = re.compile(r"\bireland\b", re.I)
_COUNTRY_CODE = re.compile(r"(?:^|[,\s(])(DE|NL|IE)\)?\s*$")  # "Munich, DE", "Dublin, IE"
_REMOTE = re.compile(r"\bremote\b|\banywhere\b", re.I)
_EU_WIDE = re.compile(r"\b(europe|european union|eu|emea)\b", re.I)


def classify_region(location: str) -> str | None:
    """Map a free-text location to DE / NL / IE / REMOTE_EU, or None if out of scope.

    Ireland counts only for Dublin, or for "Ireland" with no other Irish city
    named (typically remote-in-Ireland roles you can do from Dublin).
    """
    if not location:
        return None
    if _DE.search(location):
        return "DE"
    if _NL.search(location):
        return "NL"
    if _DUBLIN.search(location):
        return "IE"
    if _IRELAND.search(location) and not _OTHER_IRISH_CITY.search(location):
        return "IE"
    code = _COUNTRY_CODE.search(location.strip())
    if code:
        return code.group(1)
    if _REMOTE.search(location) and _EU_WIDE.search(location):
        return REMOTE_EU
    return None


def classify_any(locations: list[str]) -> str | None:
    """First in-scope region among several location strings (primary first)."""
    fallback = None
    for loc in locations:
        region = classify_region(loc or "")
        if region and region != REMOTE_EU:
            return region
        fallback = fallback or region
    return fallback


# --- title filter -------------------------------------------------------------

AI_TERMS = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|ml|machine learning|deep learning|llms?|genai|gen ai|"
    r"generative|nlp|natural language|computer vision|agents?|agentic|rag|mlops|llmops|"
    r"applied scientist|research engineer|research scientist|inference|model|foundation models?|"
    r"data scientist|reinforcement learning|speech|conversational)\b",
    re.I,
)
ENGINEER_TERMS = re.compile(r"\b(engineer|engineering|developer|architect|scientist|researcher)\b", re.I)
SOFTWARE_TERMS = re.compile(
    r"\b(software|backend|back-end|back end|full[- ]?stack|platform|infrastructure|python|"
    r"forward deployed|site reliability|sre|devops|cloud|distributed systems|data engineer|api)\b",
    re.I,
)
# Placeholder / test postings some ATS boards leave public.
_PLACEHOLDER = re.compile(r"^\s*(test|dummy|template|do not apply)\b", re.I)
EXCLUDE = re.compile(
    r"\b(sales|account executive|account manager|business development|recruit(er|ing|ment)|talent|"
    r"marketing|customer success|customer support|support engineer(ing)?|technical support|program manager|project manager|"
    r"product manager|product owner|designer|legal|counsel|finance|accountant|payroll|hr\b|"
    r"people partner|office manager|executive assistant|team assistant|assistant|intern(ship)?|werkstudent|working student|"
    r"praktik|praktikum|thesis|abschlussarbeit|stagiair|afstudeer|trainee|apprentice|ausbildung|partnerships?)\b",
    re.I,
)


def is_relevant_title(title: str, company_tier: str) -> bool:
    """Keep AI/ML/LLM engineering titles anywhere, plus software/backend titles at AI-native companies."""
    if not title or EXCLUDE.search(title) or _PLACEHOLDER.search(title):
        return False
    if AI_TERMS.search(title) and (ENGINEER_TERMS.search(title) or SOFTWARE_TERMS.search(title)):
        return True
    return (
        company_tier == "ai_native"
        and bool(SOFTWARE_TERMS.search(title))
        and bool(ENGINEER_TERMS.search(title))
    )


# --- dates --------------------------------------------------------------------


def to_iso(value) -> str:
    """Normalize ISO strings, dates, or epoch milliseconds to an ISO 8601 UTC string."""
    if value in (None, "", "N/A"):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def posted_date(iso: str) -> str:
    return iso[:10] if iso else ""


# --- dedupe keys ----------------------------------------------------------------

_COMPANY_SUFFIX = re.compile(
    r"\b(gmbh|bv|b\.v\.|nv|n\.v\.|inc|ltd|limited|llc|se|ag|plc|co|corp|corporation|group|holding|technologies|labs?)\b\.?",
    re.I,
)
_GENDER_TAGS = re.compile(r"\((?:m|f|w|d|x|all genders?|mwd|m/w/d|f/m/d|w/m/d|m/f/d|f/m/x|m/f/x|gn)[^)]*\)", re.I)


# Names a company uses on LinkedIn that differ from its job-board name.
COMPANY_ALIASES = {"fin": "intercom", "fin ai": "intercom", "anysphere": "cursor",
                   "helsing ai": "helsing", "deepl se": "deepl"}


def company_key(company: str) -> str:
    lowered = company.lower().strip()
    lowered = COMPANY_ALIASES.get(lowered, lowered)
    text = _COMPANY_SUFFIX.sub(" ", lowered)
    return re.sub(r"[^a-z0-9]+", "", text)


def title_key(title: str) -> str:
    text = _GENDER_TAGS.sub(" ", title.lower())
    for pattern in (_DE, _NL, _DUBLIN, _IRELAND):
        text = pattern.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())
