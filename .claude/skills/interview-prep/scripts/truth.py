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


def toks(text):
    """Number tokens as (value, unit). Ranges expand: '84–89%' -> (84,'%'),(89,'%'); '€7.6B' -> (7.6,'€b')."""
    out = []
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
    parts = [b[1] or "", b[2] or "", b[3] or ""]
    for r in con.execute("select text from project_contexts where project_code=?", (b[0],)):
        parts.append(r[0])
    p = con.execute("select metrics from projects where code=?", (b[0],)).fetchone()
    parts.append(p[0] if p and p[0] else "")
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
