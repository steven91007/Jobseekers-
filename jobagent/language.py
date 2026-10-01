"""Is a posting written in English, and does it demand German or Dutch?

The candidate wants English-language roles only. Two cheap, offline checks:

* `posting_language` counts function words ("und", "wir", "het", "the", ...) in the
  text. Function words are what a language cannot avoid, so a few dozen of them
  identify German, Dutch or English reliably without a model or a dependency.
* `required_language` finds sentences about German/Dutch skills and decides
  whether they are a requirement ("fluent German", "German C1") or a plus
  ("German is a plus", "nice to have").

Titles alone are weak evidence ("AI Engineer (m/w/d)" is often followed by a German
description), so `check` prefers the description and falls back to German/Dutch
words in the title only when there is no description.
"""

import re
from dataclasses import dataclass

_WORD = re.compile(r"[a-zäöüßéèëïĳ]+", re.I)
STOP = {
    "de": set("und der die das mit für wir sie ist ein eine einen zu von im auf den des dich du dein deine "
              "unser unsere uns bei oder nicht als auch werden sind ihre ihr über sowie zur zum dem wird "
              "kannst bist hast hat haben durch nach unter".split()),
    "nl": set("en het een van voor wij je jouw met zijn naar ons onze bij niet ook worden wordt hebt heeft "
              "hebben door zoals jij bent kunt als dat deze die werk".split()),
    "en": set("the and with for we you is a an to of in on our your are will as or be this that at by "
              "from have has who what how about into their they".split()),
}
# Words that only make sense in German / Dutch job titles.
_DE_TITLE = re.compile(r"\b(entwickler(in)?|ingenieur(in)?|mitarbeiter(in)?|spezialist(in)?|berater(in)?|"
                       r"leiter(in)?|referent(in)?|sachbearbeiter(in)?|fachkraft|werkstudent(in)?|"
                       r"informatiker(in)?|für|und|im bereich|schwerpunkt)\b", re.I)
_NL_TITLE = re.compile(r"\b(ontwikkelaar|medewerker|adviseur|voor|stagiair)\b", re.I)

LANG = {"german": r"(german|deutsch\w*)", "dutch": r"(dutch|nederlands\w*)"}
_LEVEL = r"(native|fluent|fluency|business[- ]fluent|business[- ]level|proficien\w*|excellent|very good|strong|good|solid|advanced)"
_CEFR = r"\b(b2|c1|c2)\b"
_PLUS = re.compile(r"\b(plus|nice[- ]to[- ]have|bonus|advantage\w*|beneficial|preferred|desirable|asset|"
                   r"preferabl\w*|ideally|not required|not a must|no german|no dutch|optional|helpful|welcome)\b", re.I)
_SENTENCE = re.compile(r"[^.\n;•]+")


@dataclass
class LanguageCheck:
    english: bool          # keep this posting?
    language: str          # en | de | nl | unknown (of the description, or "title")
    required: str          # "german" | "dutch" | "" — a stated requirement, not a plus
    reason: str = ""


def posting_language(text: str, min_hits: int = 12) -> str:
    words = [w.lower() for w in _WORD.findall(text or "")]
    hits = {lang: sum(1 for w in words if w in stop) for lang, stop in STOP.items()}
    best = max(hits, key=hits.get)
    if hits[best] < min_hits:
        return "unknown"
    # German and Dutch share "die"/"als"/"van"-like words; demand a clear margin over the runner-up.
    runner_up = sorted(hits.values())[-2]
    return best if hits[best] >= 1.5 * runner_up else "unknown"


def required_language(text: str) -> str:
    """'german' or 'dutch' when the text states that language as a requirement."""
    for lang, word in LANG.items():
        mention = re.compile(rf"(\b{_LEVEL}\b[^.\n]{{0,40}}\b{word}\b|\b{word}\b[^.\n]{{0,40}}({_CEFR}|\b{_LEVEL}\b|"
                             rf"\brequired\b|\bmandatory\b|\bmust\b|\bessential\b)|{_CEFR}[^.\n]{{0,20}}\b{word}\b)", re.I)
        for sentence in _SENTENCE.findall(text or ""):
            if mention.search(sentence) and not _PLUS.search(sentence):
                return lang
    return ""


def check(title: str, description: str = "") -> LanguageCheck:
    desc = (description or "").strip()
    if len(desc) >= 300:
        lang = posting_language(desc)
        if lang in ("de", "nl"):
            return LanguageCheck(False, lang, "", f"description is in {'German' if lang == 'de' else 'Dutch'}")
        req = required_language(desc)
        if req:
            return LanguageCheck(False, lang, req, f"requires {req.title()}")
        return LanguageCheck(True, lang, "")
    if _DE_TITLE.search(title or ""):
        return LanguageCheck(False, "title", "", "German job title")
    if _NL_TITLE.search(title or ""):
        return LanguageCheck(False, "title", "", "Dutch job title")
    return LanguageCheck(True, "unknown", "")


def filter_jobs(jobs: list) -> tuple[list, dict[str, int]]:
    """(English jobs, {reason: dropped count}). Dropped jobs never reach the database."""
    kept, dropped = [], {}
    for job in jobs:
        c = check(job.title, job.description)
        if c.english:
            kept.append(job)
        else:
            dropped[c.reason] = dropped.get(c.reason, 0) + 1
    return kept, dropped
