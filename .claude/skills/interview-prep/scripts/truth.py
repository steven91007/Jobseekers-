"""truth.py - shared number / unit / CONF truth check for /interview-prep and /outreach (stdlib).

A number-like token in a draft (digits, %, ×, €/$/£ prefix, k/m/b suffix, ranges expanded) must appear WITH THE SAME UNIT
in the hard corpus (cited bullets, their cards, CV lines, JD/profile where allowed). A token found only in a CONF item cited
in the draft's notes is a WARN naming the CONF id; CONF items with correction/retraction wording are never accepted.
"""
import os, re, sqlite3

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 4))
DB_PATH = os.environ.get("EXPERIENCE_DB") or os.path.join(ROOT, "cv", "profile", "experience.sqlite")
CONF_FILE = os.path.join(ROOT, "cv", "profile", "candidate_confirmations.md")


def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return ""


def db():
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split()) if i >= 2}
WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "hundred": "100", "half": "0.5", "double": "2", "twice": "2"})
NUMRE = re.compile(r"(?<![A-Za-z])(?P<cur>[€$£])?(?P<n1>\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
                   r"(?:\s?[–-]\s?(?P<cur2>[€$£])?(?P<n2>\d+(?:,\d{3})*(?:\.\d+)?))?"
                   r"(?:\s?(?P<unit>%|×)|(?P<unit2>[xkKmMbB])(?![A-Za-z]))?")


TIMES = re.compile(r"(\d)\s*times\b", re.I)


def toks(text):
    """Number tokens as (value, unit). Ranges expand: '84–89%' -> (84,'%'),(89,'%'); '€7.6B' -> (7.6,'€b')."""
    out = []
    text = TIMES.sub(r"\1×", text)   # "33 times" == "33×" on both sides
    for m in NUMRE.finditer(text):
        unit = (m.group("unit") or m.group("unit2") or "").lower().replace("x", "×")
        cur = m.group("cur") or m.group("cur2") or ""
        u = cur + unit
        for g in ("n1", "n2"):
            v = m.group(g)
            if v:
                v = v.replace(",", "")
                v = v.rstrip("0").rstrip(".") if "." in v else v
                out.append((v, u))
    return out


def word_toks(text):
    return [(WORDS[w.lower()], "") for w in re.findall(r"\b[A-Za-z]+\b", text) if w.lower() in WORDS]


def numbers(text):
    s = set(toks(text))
    s |= set(word_toks(text))
    return s


def fmt(t):
    u = t[1]
    return (u[0] + t[0] + u[1:]) if u and u[0] in "€$£" else t[0] + u


# an item that says "Correction to CONF-x" / "replaces CONF-x" IS the current truth (CONF-i1 €7.6B,
# i2 six years, i4 the move date); only its target is superseded (handled in conf_items). Items are
# unusable as evidence only when they mark their own figures as struck, evidence-only or never-write.
SKIP_MARK = re.compile(r"~~|evidence only|not on the CV|never writ", re.I)


def conf_items():
    """-> ({'o2': [text, ...]}, skipped {'e6': reason}, duplicate letters).
    Headings '(cited as CONF-o n)', '(cited as CONF-ab; ...)' and the first round '(cited as CONF)' (letter '')
    are read in file order; a repeated letter keeps all items under the same key."""
    items, letters, dup, letter, seen_heads = {}, [], [], None, set()
    for line in read(CONF_FILE).splitlines():
        if line.startswith("## "):
            m = re.search(r"cited as CONF(?:-([a-z]+))?", line)
            letter = None if not m else (m.group(1) or "")
            if letter is not None:
                if letter in seen_heads:
                    dup.append(letter)
                seen_heads.add(letter)
            continue
        m = re.match(r"^(\d+)\.\s+(.*)", line)
        if letter is not None and m:
            items.setdefault(letter + m.group(1), []).append(m.group(2))
    skipped = {}
    for k, texts in items.items():
        for t in texts:
            if SKIP_MARK.search(t):
                skipped[k] = "correction/retraction wording"
            for r in re.findall(r"(?:Correction to|replaces) CONF-([a-z]*\d+)", t):
                skipped.setdefault(r, f"superseded in CONF-{k}")
    return items, skipped, sorted(set(dup))


def conf_refs(notes):
    """'CONF-o1–o5', 'CONF-b1–3', 'CONF-f6, f9', 'CONF-ab' -> list of item keys / section letters."""
    keys = []
    for m in re.finditer(r"CONF-([a-z]*\d+(?:\s?[–-]\s?[a-z]*\d+)?(?:,\s*[a-z]*\d+(?:\s?[–-]\s?[a-z]*\d+)?)*|[a-z]+)\b", notes or ""):
        cur = ""
        for part in re.split(r",\s*", m.group(1)):
            rng = re.match(r"^([a-z]*)(\d+)(?:\s?[–-]\s?([a-z]*)(\d+))?$", part)
            if not rng:
                keys.append(part)  # bare section letter(s)
                continue
            let = rng.group(1) or cur
            cur = let
            lo = int(rng.group(2))
            hi = int(rng.group(4)) if rng.group(4) else lo
            keys += [f"{let}{n}" for n in range(lo, hi + 1)]
    return keys


def corpus_for(con, code, cv_text):
    b = con.execute("select project_code,body,metric,constraints from bullets where code=?", (code,)).fetchone()
    if not b:
        return ""
    # body + metric only: `constraints` holds superseded wording ("Previous body: … 200 representative
    # images") and projects.metrics aggregates every bullet of the card, incl. bullets not on this CV
    # (PS3 "6× faster" leaked into TOMRA drafts that way)
    parts = [b[1] or "", b[2] or ""]
    for r in con.execute("select text from project_contexts where project_code=?", (b[0],)):
        parts.append(r[0])
    for r in con.execute("select lead from bullet_leads where bullet_code=?", (code,)):
        for line in cv_text.splitlines():
            if r[0] and r[0].lower() in line.lower():
                parts.append(line)
    return "\n".join(parts)




def check_numbers(t, text, hard, notes, conf, conf_skipped, cited=""):
    """-> (errors, warnings) for one draft. t = label such as '#3'."""
    errs, warns = [], []
    allowed = numbers(hard)
    conf_nums = {}
    for ref in conf_refs(notes):
        texts = conf.get(ref) if re.search(r"\d", ref) else [x for k, v in conf.items() if re.fullmatch(re.escape(ref) + r"\d+", k) for x in v]
        if texts is None:
            warns.append(f"{t} notes cite CONF-{ref}, not found in candidate_confirmations.md")
            continue
        if ref in conf_skipped:
            warns.append(f"{t} notes cite CONF-{ref}, which is {conf_skipped[ref]}; its numbers are not accepted")
            continue
        for x in texts:
            if SKIP_MARK.search(x):
                continue
            for tok in numbers(x):
                conf_nums.setdefault(tok, []).append("CONF-" + ref)
    for tok in toks(text):
        if tok in allowed:
            continue
        if tok in conf_nums:
            warns.append(f"{t} number '{fmt(tok)}' is supported only by {', '.join(sorted(set(conf_nums[tok])))}, not by a cited bullet or card: confirm it is current")
        else:
            errs.append(f"{t} number '{fmt(tok)}' not in cited bullets/card context/usable CONF ({cited or 'no bullets'})")
    for tok in word_toks(text):
        if tok not in allowed and tok not in conf_nums:
            warns.append(f"{t} number word meaning {tok[0]} is not in the cited evidence")
    return errs, warns


# ---------------------------------------------------------------- company numbers (02_company.json facts)
# A number found only in the company facts (company_research.company_text: snapshot, product_team, news, hook
# company_fact) is accepted in a sentence only when (a) the sentence has no candidate reference in the draft's voice,
# (b) it names the company or a product, and (c) the same token sits next to a shared content word in both texts
# (within WINDOW tokens), so "51% stake" cannot become "51% fewer errors".
NAME_STOP = {"EUR", "MEUR", "USD", "CEO", "CFO", "CTO", "EVP", "API", "APIS", "LLM", "GPU", "CPU", "SQL", "OSE", "IFAT",
             "HTML", "CSS", "EBITA", "EBIT", "EBITDA", "RVM", "DRS", "UBC", "PET", "NRW", "GMBH", "ASA", "KPI", "ESG",
             "DE", "NL", "UK", "US", "IE", "EU", "AT", "CH", "NO", "TW", "USA"}
WINDOW = 4
CAND = {"third": re.compile(r"\b(?:he|his|him|himself)\b|\bChi-?yi\b|\bAmo\b|\bHsu\b", re.I),
        "first": re.compile(r"\b(?:I|I'm|I've|I'd|My|Me|We|Our|Us)\b|\b(?:my|me|mine|myself|we|our|ours|us)\b")}
STOPWORDS = set("""a an the and or but nor of to in on at by for with from into onto over under than then that this these
those about around up down out off per each every its it's their them they there here is are was were be been being has
have had do does did as also which who whom whose what when where while new more most less least some any all both very
just only up to like such same other another first last next one""".split())


def sentences(text):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text or "") if x.strip()]


def company_names(company, ctext, explicit=()):
    """The company (full name + first word), an explicit `names` list from 02_company.json (multi-word products such
    as "Local Control"), and product-like tokens in the company facts (TOMRA, AUTOSORT, GAINnext, PolyPerception)."""
    names = {x.strip() for x in explicit or () if x and x.strip()}
    if company:
        names |= {company.strip(), company.split()[0]}
    names |= {t for t in re.findall(r"\b[A-Z][A-Za-z]*[A-Z][a-z]+\b|\b[A-Z]{4,}\b", ctext or "")
              if t.upper() not in NAME_STOP and (t.isupper() or len(t) >= 5)}   # not "UIs"
    return sorted((n for n in names if len(n) >= 3 and n.upper() not in NAME_STOP), key=len, reverse=True)


def _pieces(text):
    return re.findall(r"[€$£]?\d[\d,.]*(?:\s?[%×]|[xkKmMbB](?![A-Za-z]))?|[A-Za-z][A-Za-z'-]*", TIMES.sub(r"\1×", text or ""))


def _stem(w):
    w = w.lower().strip("'-")
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


def context_words(text, tok, names=()):
    """Content words within WINDOW pieces of each occurrence of tok in text."""
    nm = {_stem(x) for n in names for x in n.split()}
    ps = _pieces(text)
    out = set()
    for i, p in enumerate(ps):
        if tok not in toks(p):
            continue
        for q in ps[max(0, i - WINDOW): i + WINDOW + 1]:
            w = _stem(q)
            if q[:1].isalpha() and len(w) >= 3 and w not in STOPWORDS and w not in nm:
                out.add(w)
    return out


def company_number_ok(sentence, tok, ctext, names, voice):
    """-> (ok, reason) for one number token that only the company facts support."""
    if tok not in numbers(ctext):
        return False, "not in the company facts either"
    if CAND[voice].search(sentence):
        return False, "the sentence also refers to the candidate (a company figure cannot describe the candidate's work)"
    if not any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", sentence) for n in names):
        return False, f"the sentence names neither the company nor a product ({', '.join(names[:5])})"
    if tok[1] == "" and re.fullmatch(r"(?:19|20)\d\d", tok[0]):
        return True, "year"
    shared = context_words(sentence, tok, names) & context_words(ctext, tok, names)
    if not shared:
        return False, "no shared context word next to the number in the company facts (different quantity?)"
    return True, "shares: " + ", ".join(sorted(shared)[:3])


def filter_company_numbers(t, text, errs, ctext, names, voice):
    """Drop number errors that the company-number rule accepts; explain the ones it refuses."""
    if not ctext:
        return errs
    out = list(errs)
    for tok in dict.fromkeys(toks(text)):
        key = f"number '{fmt(tok)}' not in"
        hits = [e for e in out if key in e]
        if not hits:
            continue
        res = [company_number_ok(x, tok, ctext, names, voice) for x in sentences(text) if tok in toks(x)] or [(False, "?")]
        out = [e for e in out if e not in hits]
        bad = [r for ok, r in res if not ok]
        if bad and tok in numbers(ctext):
            out.append(f"{t} number '{fmt(tok)}' is only a company fact (02_company.json) and fails the company rule: {bad[0]}")
        elif bad:
            out += hits
    return out


def company_selftest(ctext, company="TOMRA Recycling", explicit=()):
    """Cases from the /apply review (2026-11-02). -> list of (name, ok)."""
    names = company_names(company, ctext, explicit)
    cases = [
        ("company 33x claim passes", "TOMRA reports up to 33× throughput for GAINnext UBC sorting.", "third", True),
        ("company headcount passes", "TOMRA Recycling had about 5,800 employees in 2025.", "first", True),
        ("33 times laundered onto the candidate", "Like TOMRA, he reached 33 times the manual throughput.", "third", False),
        ("16 MEUR cost programme as a candidate result", "At TOMRA I cut EBITA costs by 16 MEUR.", "first", False),
        ("51% stake as an error rate", "With the PolyPerception stake, his tools made 51% fewer errors.", "third", False),
        ("51% with no shared context", "TOMRA ships 51% fewer errors than rivals.", "third", False),
        ("company number with no company name", "The group has about 5,800 employees.", "first", False),
    ]
    res = []
    for name, sent, voice, want in cases:
        errs = [f"#t number '{fmt(x)}' not in cited" for x in toks(sent)]
        left = filter_company_numbers("#t", sent, errs, ctext, names, voice)
        res.append((name, (not left) == want, left[0] if left else "accepted"))
    return res


def cv_metrics(metrics, corpus):
    """projects.metrics aggregates every bullet of a card, incl. bullets NOT on this CV (PS3 "6× faster" on TOMRA).
    -> (segments whose numbers all appear in corpus = the submitted CV + picked bullets, number of dropped segments)."""
    allowed = numbers(corpus)
    keep, drop = [], 0
    for seg in [x.strip() for x in re.split(r"\s+·\s+", metrics or "") if x.strip()]:
        if all(t in allowed for t in toks(seg)):
            keep.append(seg)
        else:
            drop += 1
    return keep, drop


def card_metrics_line(code, name, metrics, corpus):
    keep, drop = cv_metrics(metrics, corpus)
    return (f"- **{code}** {name} | metrics on this CV: {' · '.join(keep) or '-'}"
            + (f" ({drop} card metric(s) omitted: from bullets not on this CV)" if drop else ""))

