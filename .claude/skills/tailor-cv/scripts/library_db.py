"""Experience library DB — SQLite store for project cards, bullets, Skills and JD keywords
(references/library_db_spec.md + _v2.md + _v3.md). Stdlib only; run with WSL `python3`.

    python3 library_db.py init [--seed PATH] [--db PATH] [--fresh]
    python3 library_db.py cards [--for ARCHETYPE]
    python3 library_db.py card VI            |  bullet VI1  |  leads VI1
    python3 library_db.py skills [--category X] [--rows]
    python3 library_db.py keywords [--jd TOMRA] [--coverage gap] [--term X]
    python3 library_db.py match $D/07_job_keywords.json   (per term: coverage + concept; bullets by concept score)
    python3 library_db.py resolve "<term>"                 (term → concept, rules 1–4; unknown → NEW)
    python3 library_db.py render --scope SCOPE-ATS --card VI:0:0 --bullet VI1:0 ... \
                                 --skills-row "AI — computer vision:1=0,1,2,4~1,5~1,6"
    python3 library_db.py render --jd TOMRA          (recorded picks)
    python3 library_db.py diff --jd TOMRA [--cv $D/03_cv.md]
    python3 library_db.py record --jd TOMRA --company ... --title ... --dir ... --picks "scope=...;card=VI:0:0;..." \
                                 [--headline "<L2 text>"] [--country Germany]
    python3 library_db.py picks TOMRA
    python3 library_db.py normalize-keywords $D/07_job_keywords.json [--write]
    python3 library_db.py fit --picks "..."  |  fit --jd TOMRA      (exit 1 when a line is over)
    python3 library_db.py pick --jd Scout24 [--dir $D] [--archetype X] [--cards 2|3]
    python3 library_db.py align --jd Scout24 [--dir $D] [--picks "..."]   (JD nouns into the leads; advisory)
    python3 library_db.py gaps --jd TOMRA [--dir $D]      (JD concepts: SoT-supported / gap / do not claim)
    python3 library_db.py ingest-ats --jd Scout24 [--dir $D]
    python3 library_db.py set-coverage "<term>" gap|covered|"do not claim"|"sot-supported" [--note ...]
    python3 library_db.py set-concept "<term>" AGENTIC     (alias + concept + coverage from its claim)
    python3 library_db.py status
    python3 library_db.py export-md [--out PATH]
    python3 library_db.py notion-payload projects|bullets|skills|keywords|concepts [--only-new]
    python3 library_db.py notion-payload applications [--jd TOMRA]
    python3 library_db.py set-notion-id bullets VI1 <page id>     (also: applications TOMRA <page id>)
    python3 library_db.py concepts [--kind X] [--claim covered|sot|gap|dnc] [--orphans]  |  concept AGENTIC
    python3 library_db.py concepts-bootstrap [--out PATH]      (lexical clusters → concept proposals)
    python3 library_db.py sot-leads --jd Scout24 [--per-bullet 2] [--write]   (SoT terms → lead proposals)
    python3 library_db.py qa add --jd TOMRA --question "…" --kind behavioural [--concepts A,B] [--bullets VI1] \\
                                 [--answer "…" | --answer-file P] [--source generated|asked|self] [--outcome …] [--notes …]
    python3 library_db.py qa import questions.json [--update] [--prune]  |  qa list [--jd Co] [--kind K] [--concept C] [--source S] [--full]
    python3 library_db.py qa set ID [--outcome …] [--notes …] [--answer …]
    python3 library_db.py outreach add --jd TOMRA --person "…" --channel email --kind referral-letter [--role …] \\
                                 [--status draft] [--file P] [--notes …]
    python3 library_db.py outreach list [--jd Co] [--status S]  |  outreach set ID --status S [--sent D] [--replied D] [--notes …]
    python3 library_db.py selftest

Pick specs (render / record): `scope=CODE` · `card=CODE[:title_ord[:context_ord]]` ·
`bullet=CODE[:lead_ord]` (bullets follow the card they sit under) ·
`skill_row=LABEL[:VARIANT][=ITEMS]` — VARIANT = 0 (label) / 1.. (label variants) or the label
text; ITEMS = comma list of item ords in print order, `ord~n` = the n-th spelling variant
(`skills --rows` lists ords and spellings). Omitted ITEMS = all items, default spellings.

`init` rebuilds every library table from the seed but carries over `applications`,
`application_picks`, Notion page ids set with `set-notion-id`, and the `ingest-ats` log +
`set-coverage` overrides, which it replays onto the seed keywords (unless --fresh), and the
`set-concept` overrides (manual aliases).

User-data tables `interview_qa` and `outreach` (spec v3 §8) are created with IF NOT EXISTS and are not in
LIBRARY_TABLES, so `init` never drops them.

Concept layer (v3): `pick` ranks bullets by Σ JD weight (required 3 / preferred 2 / keyword 1) ×
evidence strength (direct 1 / implied 0.5) × idf(concept) + BM25 over body + leads + keywords +
concept aliases, then polishes variants so the most JD terms land on the CV (all v2 rules kept).
Concepts noted "exclude from scoring" (ROLE_WORDS) never score.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import difflib
import functools
import io
import json
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[3]
PROFILE = ROOT / "cv" / "profile"
SEED = PROFILE / "library_seed.json"
DB = PROFILE / "experience.sqlite"
EXPORT = PROFILE / "bullet_library.generated.md"
TOMRA_DIR = "cv/applications/20261023_TOMRA_Senior-Python-Software-Engineer/v3_cards"
METRICS = SCRIPTS / "fit_metrics.json"
FIXTURES = SCRIPTS / "fixtures"
# Scout24 picks frozen 2026-11-02 with DT1 on a lead naming no JD agent term (DT1:2, no concept); the recorded
# DT1:9 already says "workflow agents", so align would keep it. selftest [11] aligns these and expects DT1:3 (AGENTIC).
SCOUT24_PICKS = ("scope=SCOPE-ATS;card=DT:1:0;bullet=DT1:2;bullet=DT2a:5;card=MC:0:3;bullet=MC1:9;bullet=MC6:4;"
                 "card=PS:1:3;bullet=PS1:11;bullet=PS2:4;skill_row=AI — LLM & agents:0=0~1,3~1,5,6~1;"
                 "skill_row=MLOps & LLMOps:0=1~1,2~2,3;skill_row=Testing & deployment:0=0~2,1,2,3,5;"
                 "skill_row=AI methods:0=0~1,1,2,3;skill_row=Languages & backend:0;skill_row=Data & UI:0")
TOMRA_PICKS = ("scope=SCOPE-ATS;card=VI:0:0;bullet=VI1:0;bullet=VI2:1;bullet=VI3:1;"
               "card=PS:0:1;bullet=PS1:1;bullet=PS2:1;card=MC:0:1;bullet=MC4:0;"
               "skill_row=Languages & backend;"
               "skill_row=AI — computer vision:1=0,1,2,4~1,5~1,6;"
               "skill_row=MLOps & LLMOps;"
               "skill_row=Testing & deployment:1=0~1,4,2,3,5;"
               "skill_row=Data & UI=0,1,2~1,3,4;"
               "skill_row=AI — LLM & agents")
NOTION_URL = "https://app.notion.com/p/"
CARD_MARK = ""   # optional marker before project-card titles (tried ▪ / ■ on 2026-10-30, removed: layout looked odd)
CONF_RE = re.compile(r"CONF-[^\s,;)\"]+")
ROLE_RE = re.compile(r"^\*\*[^*]+\*\*  \|  ")

SCHEMA = """
CREATE TABLE projects(code TEXT PRIMARY KEY, name TEXT, employer TEXT, period TEXT, status TEXT,
  domains TEXT, metrics TEXT, best_for TEXT, constraints TEXT, evidence TEXT, notion_id TEXT);
CREATE TABLE project_titles(project_code TEXT REFERENCES projects(code), title TEXT, ord INTEGER);
CREATE TABLE project_contexts(project_code TEXT REFERENCES projects(code), text TEXT, conf TEXT,
  note TEXT, ord INTEGER);
CREATE TABLE scope_lines(code TEXT PRIMARY KEY, text TEXT, conf TEXT, note TEXT);
CREATE TABLE bullets(code TEXT PRIMARY KEY, project_code TEXT REFERENCES projects(code), status TEXT,
  body TEXT, capabilities TEXT, keywords_carried TEXT, metric TEXT, conf TEXT, constraints TEXT,
  evidence TEXT, chars INTEGER, notion_id TEXT);
CREATE TABLE bullet_leads(bullet_code TEXT REFERENCES bullets(code), lead TEXT, ord INTEGER,
  concept_code TEXT REFERENCES concepts(code));
CREATE TABLE skills(name TEXT PRIMARY KEY, category TEXT, esco TEXT, variants TEXT, conf TEXT,
  on_cv INTEGER, notion_id TEXT);
CREATE TABLE skill_rows(label TEXT PRIMARY KEY, variants TEXT, ord INTEGER);
CREATE TABLE skill_row_items(row_label TEXT REFERENCES skill_rows(label), item TEXT, ord INTEGER,
  spellings TEXT);
CREATE TABLE keywords(term TEXT PRIMARY KEY, type TEXT, coverage TEXT, esco TEXT, spelling TEXT,
  freq INTEGER, notes TEXT, notion_id TEXT, concept_code TEXT REFERENCES concepts(code));
CREATE TABLE concepts(code TEXT PRIMARY KEY, name TEXT, kind TEXT, canonical TEXT, esco TEXT, definition TEXT,
  claim TEXT, notes TEXT, notion_id TEXT);
CREATE TABLE concept_aliases(concept_code TEXT REFERENCES concepts(code), alias TEXT, source TEXT, jd TEXT,
  UNIQUE(alias COLLATE NOCASE));
CREATE TABLE concept_evidence(concept_code TEXT REFERENCES concepts(code), kind TEXT, code TEXT, strength TEXT,
  note TEXT);
CREATE TABLE concept_relations(src TEXT REFERENCES concepts(code), rel TEXT, dst TEXT REFERENCES concepts(code));
CREATE TABLE keyword_bullets(term TEXT REFERENCES keywords(term), bullet_code TEXT REFERENCES bullets(code));
CREATE TABLE keyword_skills(term TEXT REFERENCES keywords(term), skill_name TEXT REFERENCES skills(name));
CREATE TABLE keyword_jds(term TEXT REFERENCES keywords(term), jd TEXT);
CREATE TABLE meta_lines(kind TEXT, text TEXT, ord INTEGER);
CREATE TABLE IF NOT EXISTS applications(jd TEXT PRIMARY KEY, company TEXT, title TEXT, dir TEXT, created TEXT,
  headline TEXT, country TEXT, summary_chars INTEGER, notion_id TEXT);
CREATE TABLE IF NOT EXISTS application_picks(jd TEXT REFERENCES applications(jd), kind TEXT, code TEXT,
  variant_ord INTEGER, context_ord INTEGER, lead TEXT, items TEXT, position INTEGER);
CREATE TABLE IF NOT EXISTS ats_ingest(jd TEXT, term TEXT, type TEXT, found INTEGER, freq INTEGER, date TEXT,
  ord INTEGER);
CREATE TABLE IF NOT EXISTS coverage_overrides(term TEXT PRIMARY KEY, coverage TEXT, note TEXT, date TEXT);
CREATE TABLE IF NOT EXISTS concept_overrides(term TEXT PRIMARY KEY COLLATE NOCASE, concept_code TEXT, date TEXT);
CREATE TABLE IF NOT EXISTS interview_qa(id INTEGER PRIMARY KEY, jd TEXT, company TEXT, question TEXT, kind TEXT,
  concepts TEXT, bullets TEXT, answer TEXT, source TEXT, outcome TEXT, notes TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS outreach(id INTEGER PRIMARY KEY, jd TEXT, company TEXT, person TEXT, role TEXT,
  channel TEXT, kind TEXT, status TEXT, file TEXT, sent TEXT, replied TEXT, notes TEXT, created TEXT);
"""
APP_COLUMNS = {"headline": "TEXT", "country": "TEXT", "summary_chars": "INTEGER", "notion_id": "TEXT"}
KW_FIELDS = (("required_skills", "required skill"), ("preferred_skills", "preferred skill"), ("keywords", "keyword"))
LIBRARY_TABLES = ["keyword_jds", "keyword_skills", "keyword_bullets", "keywords", "concept_relations",
                  "concept_evidence", "concept_aliases", "concepts", "skill_row_items",
                  "skill_rows", "skills", "bullet_leads", "bullets", "scope_lines", "project_contexts",
                  "project_titles", "projects", "meta_lines"]
NOTION_KEYS = {"projects": "code", "bullets": "code", "skills": "name", "keywords": "term", "concepts": "code"}
# Sidecar that survives a DB rebuild or a deleted DB: {table: {key: notion_id}}.
NOTION_IDS_FILE = PROFILE / "notion_ids.json"


def load_notion_ids(path: Path = NOTION_IDS_FILE) -> dict[tuple[str, str], str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(t, k): v for t, rows in data.items() for k, v in rows.items() if v}


def save_notion_ids(con: sqlite3.Connection, path: Path = NOTION_IDS_FILE) -> int:
    """Write every notion_id in the DB (plus ids already in the file) back to the sidecar."""
    ids = load_notion_ids(path)
    for table, key in list(NOTION_KEYS.items()) + [("applications", "jd")]:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            continue
        for r in con.execute(f"SELECT {key} k, notion_id FROM {table} WHERE notion_id != '' AND notion_id IS NOT NULL"):
            ids[(table, r["k"])] = r["notion_id"]
    out: dict[str, dict[str, str]] = {}
    for (t, k), v in sorted(ids.items()):
        out.setdefault(t, {})[k] = v
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(ids)
CLAIMS = {"covered": "covered", "sot": "SoT-supported wording", "sot-supported": "SoT-supported wording",
          "gap": "gap", "dnc": "do not claim", "do not claim": "do not claim"}


class PickError(ValueError):
    pass


def connect(db: Path = DB) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    have = {r[1] for r in con.execute("PRAGMA table_info(applications)")}
    if have:   # v1 DBs: add the v2 columns in place (recorded picks stay)
        for col, typ in APP_COLUMNS.items():
            if col not in have:
                con.execute(f"ALTER TABLE applications ADD COLUMN {col} {typ}")
        con.commit()
    return con


def notion_url(notion_id: str | None) -> str | None:
    return NOTION_URL + notion_id.replace("-", "") if notion_id else None


def split_list(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def bullet_chars(lead: str, body: str) -> int:
    return len(lead + " — " + body) + 4


@functools.lru_cache(maxsize=8192)
def _term_re(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")


def has_term(term: str, text: str) -> bool:
    """Whole-word, case-insensitive — the same rule as the ATS keyword match."""
    return _term_re(term.lower()).search(text.lower()) is not None


def app_path(d: str) -> Path:
    return Path(d) if Path(d).is_absolute() else ROOT / d


def cv_head(cv: Path) -> dict[str, Any]:
    """Headline (L2 without **), relocation country (L3) and Summary length (L5) of a 03_cv.md."""
    if not cv.exists():
        return {}
    lines = cv.read_bytes().decode("utf-8").splitlines()
    m = re.search(r"Open to relocate to (.+?)\s{2,}\|", lines[2]) if len(lines) > 2 else None
    return {"headline": lines[1].strip("*") if len(lines) > 1 else None,
            "country": m.group(1).strip() if m else None,
            "summary_chars": len(lines[4]) if len(lines) > 4 else None, "summary": lines[4] if len(lines) > 4 else None}


# ---------------------------------------------------------------- init
def init_db(seed: Path = SEED, db: Path = DB, fresh: bool = False, ids_path: Path = NOTION_IDS_FILE) -> str:
    data = json.loads(seed.read_text(encoding="utf-8"))
    con = connect(db)
    con.execute("PRAGMA foreign_keys = OFF")
    if fresh:
        for t in ("application_picks", "applications", "ats_ingest", "coverage_overrides", "concept_overrides"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
    kept_ids: dict[tuple[str, str], str] = load_notion_ids(ids_path)
    for table, key in NOTION_KEYS.items():
        if not fresh and con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            for r in con.execute(f"SELECT {key} k, notion_id FROM {table} WHERE notion_id != ''"):
                kept_ids[(table, r["k"])] = r["notion_id"]
    for t in LIBRARY_TABLES:
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.executescript(SCHEMA)
    con.execute("PRAGMA foreign_keys = ON")
    with con:
        for s in data["scope_lines"]:
            con.execute("INSERT INTO scope_lines VALUES(?,?,?,?)", (s["code"], s["text"], s["conf"], s["note"]))
        for p in data["projects"]:
            con.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (p["code"], p["name"], p["employer"], p["period"], p["status"], p["domains"],
                         p["metrics"], p["best_for"], p["constraints"], p["evidence"], p.get("notion_id", "")))
            for i, t in enumerate(p["titles"]):
                con.execute("INSERT INTO project_titles VALUES(?,?,?)", (p["code"], t, i))
            for i, c in enumerate(p["contexts"]):
                con.execute("INSERT INTO project_contexts VALUES(?,?,?,?,?)",
                            (p["code"], c["text"], c["conf"], c["note"], i))
        for b in data["bullets"]:
            con.execute("INSERT INTO bullets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (b["code"], b["project"], b["status"], b["body"], b["capabilities"], b["keywords"],
                         b["metric"], b["conf"], b["constraints"], b["evidence"],
                         bullet_chars(b["leads"][0], b["body"]), b.get("notion_id", "")))
            for i, lead in enumerate(b["leads"]):
                con.execute("INSERT INTO bullet_leads VALUES(?,?,?,NULL)", (b["code"], lead, i))
        for s in data["skills"]:
            con.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?)",
                        (s["name"], s["category"], s["esco"], s["variants"], s["conf"], s["on_cv"],
                         s.get("notion_id", "")))
        for i, r in enumerate(data["skill_rows"]):
            con.execute("INSERT INTO skill_rows VALUES(?,?,?)", (r["label"], r["variants"], i))
            spellings = r.get("item_spellings", {})
            for j, item in enumerate(r["items"]):
                con.execute("INSERT INTO skill_row_items VALUES(?,?,?,?)",
                            (r["label"], item, j, json.dumps(spellings.get(item, []), ensure_ascii=False)))
        for k in data["keywords"]:
            con.execute("INSERT INTO keywords VALUES(?,?,?,?,?,?,?,?,NULL)",
                        (k["term"], k["type"], k["coverage"], k.get("esco", ""), k.get("spelling", ""),
                         k.get("freq"), k.get("notes", ""), k.get("notion_id", "")))
            for b in k["bullets"]:
                con.execute("INSERT INTO keyword_bullets VALUES(?,?)", (k["term"], b))
            for s in k["skills"]:
                con.execute("INSERT INTO keyword_skills VALUES(?,?)", (k["term"], s))
            for jd in k["jds"]:
                con.execute("INSERT INTO keyword_jds VALUES(?,?)", (k["term"], jd))
        for c in data.get("concepts", []):
            con.execute("INSERT INTO concepts VALUES(?,?,?,?,?,?,?,?,?)",
                        (c["code"], c["name"], c["kind"], c["canonical"], c.get("esco", ""), c.get("definition", ""),
                         c["claim"], c.get("notes", ""), c.get("notion_id", "")))
            for a in c["aliases"]:
                con.execute("INSERT INTO concept_aliases VALUES(?,?,?,?)", (c["code"], a, "jd", ""))
            for kind, code, strength, *note in c.get("evidence", []):
                con.execute("INSERT INTO concept_evidence VALUES(?,?,?,?,?)",
                            (c["code"], kind, code, strength, note[0] if note else ""))
        for c in data.get("concepts", []):      # second pass: targets must exist
            for rel, dst in c.get("relations", []):
                con.execute("INSERT INTO concept_relations VALUES(?,?,?)", (c["code"], rel, dst))
        for kind in ("rationales", "facts"):
            for i, line in enumerate(data.get(kind, [])):
                con.execute("INSERT INTO meta_lines VALUES(?,?,?)", (kind, line, i))
        restored = 0
        for (table, code), nid in kept_ids.items():
            key = NOTION_KEYS.get(table)
            if not key:
                continue
            restored += con.execute(f"UPDATE {table} SET notion_id=? WHERE {key}=? AND notion_id=''",
                                    (nid, code)).rowcount
    con.close()
    con = connect(db)   # adds the v2 applications columns to an old DB
    apply_concept_overrides(con)   # manual aliases first: ingest-ats resolves new terms against them
    replayed = replay_ingest(con)
    # ingest-only keywords exist only after the replay, so restore their notion ids again here
    for (table, code), nid in kept_ids.items():
        key = NOTION_KEYS.get(table)   # the sidecar also holds applications, which init never drops
        if not key:
            continue
        restored += con.execute(f"UPDATE {table} SET notion_id=? WHERE {key}=? AND (notion_id='' OR notion_id IS NULL)",
                                (nid, code)).rowcount
    con.commit()
    save_notion_ids(con, ids_path)
    backfill_applications(con)
    assign_concepts(con)
    apply_concept_overrides(con)
    assign_lead_concepts(con)
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("projects", "bullets", "skills", "skill_rows", "keywords", "concepts", "applications")}
    con.close()
    return (f"{db}: " + " · ".join(f"{k} {v}" for k, v in counts.items())
            + (f" · notion ids restored {restored}" if restored else "")
            + (f" · ingest-ats replayed {replayed}" if replayed else ""))


def backfill_applications(con: sqlite3.Connection) -> None:
    """Fill headline / country / summary_chars of recorded applications from their 03_cv.md."""
    with con:
        for a in con.execute("SELECT * FROM applications WHERE headline IS NULL OR country IS NULL").fetchall():
            h = cv_head(app_path(a["dir"]) / "03_cv.md")
            if h:
                con.execute("UPDATE applications SET headline=coalesce(headline, ?), country=coalesce(country, ?), "
                            "summary_chars=coalesce(summary_chars, ?) WHERE jd=?",
                            (h["headline"], h["country"], h["summary_chars"], a["jd"]))


# ---------------------------------------------------------------- picks
def parse_picks(spec: str) -> list[tuple[str, str]]:
    picks = []
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        kind, sep, val = part.partition("=")
        kind = kind.strip().replace("-", "_")
        if not sep or kind not in ("scope", "card", "bullet", "skill_row"):
            raise PickError(f"bad pick {part!r} (want scope=|card=|bullet=|skill_row=)")
        picks.append((kind, val.strip()))
    return picks


def picks_to_spec(picks: list[tuple[str, str]]) -> str:
    return ";".join(f"{k}={v}" for k, v in picks)


def _ords(val: str, n: int) -> tuple[str, list[int]]:
    code, *rest = val.split(":")
    if len(rest) > n:
        raise PickError(f"too many ords in {val!r}")
    try:
        return code, [int(x) for x in rest] + [0] * (n - len(rest))
    except ValueError:
        raise PickError(f"ords must be integers in {val!r}") from None


def _nth(rows: list[sqlite3.Row], i: int, what: str) -> sqlite3.Row:
    if not 0 <= i < len(rows):
        raise PickError(f"{what}: ord {i} out of range 0..{len(rows) - 1}")
    return rows[i]


def resolve_skill_row(con: sqlite3.Connection, val: str) -> dict[str, Any]:
    head, _, items_spec = val.partition("=")
    label, _, variant = head.partition(":")
    row = con.execute("SELECT * FROM skill_rows WHERE label=?", (label.strip(),)).fetchone()
    if not row:
        raise PickError(f"unknown skill row {label!r}")
    labels = [row["label"]] + [v.strip() for v in row["variants"].split("|") if v.strip()]
    variant = variant.strip()
    if not variant:
        v_ord = 0
    elif variant.isdigit():
        v_ord = int(variant)
        _nth(labels, v_ord, f"skill row {row['label']!r} variant")
    elif variant in labels:
        v_ord = labels.index(variant)
    else:
        raise PickError(f"skill row {row['label']!r}: unknown variant {variant!r} (have {labels})")
    items = con.execute("SELECT * FROM skill_row_items WHERE row_label=? ORDER BY ord", (row["label"],)).fetchall()
    chosen = []
    tokens = [t.strip() for t in items_spec.split(",") if t.strip()] if items_spec else [str(i) for i in range(len(items))]
    for tok in tokens:
        i, _, sp = tok.partition("~")
        try:
            item = _nth(items, int(i), f"skill row {row['label']!r} item")
            spellings = [item["item"]] + json.loads(item["spellings"])
            chosen.append(_nth(spellings, int(sp or 0), f"item {item['item']!r} spelling"))
        except ValueError:
            raise PickError(f"bad item token {tok!r} in {val!r} (want ord or ord~n)") from None
    return {"label": row["label"], "rendered_label": labels[v_ord], "variant_ord": v_ord,
            "items": chosen, "items_spec": items_spec}


def resolve(con: sqlite3.Connection, picks: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Validate picks against the DB; return one dict per pick with the rendered line."""
    out, card, seen = [], None, set()
    for pos, (kind, val) in enumerate(picks):
        if kind == "scope":
            r = con.execute("SELECT * FROM scope_lines WHERE code=?", (val,)).fetchone()
            if not r:
                raise PickError(f"unknown scope line {val!r}")
            out.append({"kind": kind, "code": val, "line": r["text"]})
        elif kind == "card":
            code, (t, c) = _ords(val, 2)
            if not con.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone():
                raise PickError(f"unknown card {code!r}")
            title = _nth(con.execute("SELECT title FROM project_titles WHERE project_code=? ORDER BY ord",
                                     (code,)).fetchall(), t, f"card {code} title")["title"]
            ctx = _nth(con.execute("SELECT text FROM project_contexts WHERE project_code=? ORDER BY ord",
                                   (code,)).fetchall(), c, f"card {code} context")["text"]
            card = code
            out.append({"kind": kind, "code": code, "variant_ord": t, "context_ord": c,
                        "line": f"**{CARD_MARK}{title}** — {ctx}"})
        elif kind == "bullet":
            code, (lo,) = _ords(val, 1)
            b = con.execute("SELECT * FROM bullets WHERE code=?", (code,)).fetchone()
            if not b:
                raise PickError(f"unknown bullet {code!r}")
            if code in seen:
                raise PickError(f"bullet {code} picked twice")
            seen.add(code)
            if card is None:
                raise PickError(f"bullet {code} before any card")
            if b["project_code"] != card:
                print(f"note: bullet {code} (card {b['project_code']}) placed under card {card}", file=sys.stderr)
            lead = _nth(con.execute("SELECT lead FROM bullet_leads WHERE bullet_code=? ORDER BY ord",
                                    (code,)).fetchall(), lo, f"bullet {code} lead")["lead"]
            out.append({"kind": kind, "code": code, "variant_ord": lo, "lead": lead,
                        "line": f"- **{lead} —** {b['body']}", "chars": bullet_chars(lead, b["body"])})
        else:
            r = resolve_skill_row(con, val)
            out.append({"kind": kind, "code": r["label"], "variant_ord": r["variant_ord"],
                        "lead": r["rendered_label"], "items": r["items_spec"],
                        "line": f"**{r['rendered_label']}:** " + " · ".join(r["items"])})
    return out


def render_lines(con: sqlite3.Connection, picks: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    res = resolve(con, picks)
    return ([r["line"] for r in res if r["kind"] == "skill_row"],
            [r["line"] for r in res if r["kind"] != "skill_row"])


def recorded_picks(con: sqlite3.Connection, jd: str) -> list[tuple[str, str]]:
    rows = con.execute("SELECT * FROM application_picks WHERE jd=? ORDER BY position", (jd,)).fetchall()
    if not rows:
        raise PickError(f"no recorded picks for {jd!r} (run record first)")
    picks = []
    for r in rows:
        if r["kind"] == "scope":
            picks.append(("scope", r["code"]))
        elif r["kind"] == "card":
            picks.append(("card", f"{r['code']}:{r['variant_ord']}:{r['context_ord']}"))
        elif r["kind"] == "bullet":
            picks.append(("bullet", f"{r['code']}:{r['variant_ord']}"))
        else:
            picks.append(("skill_row", f"{r['code']}:{r['variant_ord']}" + (f"={r['items']}" if r["items"] else "")))
    return picks


def recorded_dir(con: sqlite3.Connection, jd: str) -> Path:
    row = con.execute("SELECT dir FROM applications WHERE jd=?", (jd,)).fetchone()
    if not row:
        raise PickError(f"no recorded application {jd!r} (pass --dir)")
    return app_path(row["dir"])


def picks_from_args(con: sqlite3.Connection, args: argparse.Namespace) -> list[tuple[str, str]]:
    if getattr(args, "jd", None):
        return recorded_picks(con, args.jd)
    picks = parse_picks(args.picks) if getattr(args, "picks", None) else []
    picks += getattr(args, "ordered", None) or []
    if not picks:
        raise PickError("no picks given (use --scope/--card/--bullet/--skills-row, --picks or --jd)")
    return picks


# ---------------------------------------------------------------- diff
def cv_blocks(cv: Path) -> tuple[list[tuple[int, str]], list[tuple[int, str]], str]:
    """(skills lines, Delta block lines) with 1-based line numbers, plus the line ending."""
    raw = cv.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = list(enumerate(raw.split(eol), 1))
    skills, delta, mode = [], [], None
    for n, line in lines:
        if line == "**TECHNICAL SKILLS**":
            mode = "skills"
            continue
        if line == "**PROFESSIONAL EXPERIENCE**":
            mode = None
            continue
        if ROLE_RE.match(line):
            mode = "delta" if "Delta Electronics" in line else None
            continue
        if re.fullmatch(r"\*\*[A-Z ,&]+\*\*", line):
            mode = None
        if mode == "skills" and line:
            skills.append((n, line))
        elif mode == "delta" and line:
            delta.append((n, line))
    return skills, delta, eol


def diff_cv(con: sqlite3.Connection, picks: list[tuple[str, str]], cv: Path) -> list[str]:
    """Return a list of problems (empty = byte-for-byte match per line)."""
    skills_r, delta_r = render_lines(con, picks)
    skills_f, delta_f, eol = cv_blocks(cv)
    problems = []
    for name, want, have in (("Skills", skills_r, skills_f), ("Delta", delta_r, delta_f)):
        for i in range(max(len(want), len(have))):
            w = want[i] if i < len(want) else None
            n, h = have[i] if i < len(have) else (None, None)
            if w != h:
                problems.append(f"{name} line {n or '-'}:\n  cv     : {h}\n  render : {w}")
                if w and h:
                    problems.append("  " + "".join(
                        f"[{op} {h[i1:i2]!r}→{w[j1:j2]!r}]"
                        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, h, w).get_opcodes() if op != "equal"))
        if want and want == [h for _, h in have] and eol.join(want).encode("utf-8") not in cv.read_bytes():
            problems.append(f"{name}: rendered block is not a contiguous byte run in {cv.name}")
    return problems


# ---------------------------------------------------------------- match
def match_keywords(con: sqlite3.Connection, path: Path) -> dict[str, Any]:
    job = json.loads(path.read_text(encoding="utf-8"))
    sections = [("required_skills", "req"), ("preferred_skills", "pref"), ("keywords", "kw")]
    known_terms = {r["term"]: r for r in con.execute("SELECT * FROM keywords")}
    folded = {t.casefold(): t for t in known_terms}
    aliases = alias_index(con)
    rows, bullet_hits = [], {}
    for sec, tag in sections:
        for term in job.get(sec, []):
            key = term if term in known_terms else folded.get(term.casefold())
            res = resolve_term(term, aliases)
            if key is None:
                rows.append({"tag": tag, "term": term, "new": True, "concept": res})
                continue
            k = known_terms[key]
            bl = [r[0] for r in con.execute("SELECT bullet_code FROM keyword_bullets WHERE term=?", (key,))]
            sk = [r[0] for r in con.execute("SELECT skill_name FROM keyword_skills WHERE term=?", (key,))]
            rows.append({"tag": tag, "term": term, "new": False, "case_only": key != term, "concept": res,
                         "coverage": k["coverage"], "bullets": bl, "skills": sk, "spelling": k["spelling"]})
            if k["coverage"] == "covered":
                for b in bl:
                    hit = bullet_hits.setdefault(b, {"req": 0, "pref": 0, "kw": 0, "terms": []})
                    hit[tag] += 1
                    hit["terms"].append(term)
    ranked = sorted(bullet_hits.items(), key=lambda kv: (-kv[1]["req"], -kv[1]["pref"], -kv[1]["kw"], kv[0]))
    return {"rows": rows, "ranked": ranked,
            "known": sum(not r["new"] for r in rows), "new": sum(r["new"] for r in rows),
            "covered": [r["term"] for r in rows if not r["new"] and r["coverage"] == "covered"],
            "not_covered": [r["term"] for r in rows if not r["new"] and r["coverage"] != "covered"]}


def print_match(m: dict[str, Any]) -> None:
    for r in m["rows"]:
        c = r.get("concept") or {}
        cc = (f" → {c['code']}" + (f" (rule {c['rule']})" if c["rule"] > 1 else "")) if c.get("code") else " → no concept"
        if r["new"]:
            print(f"[{r['tag']}] {r['term']} — NEW" + (cc if c.get("code") else ""))
            continue
        bits = [r["coverage"] + cc]
        if r["bullets"]:
            bits.append("bullets " + ",".join(r["bullets"]))
        if r["skills"]:
            bits.append("skills " + " | ".join(r["skills"]))
        if r["spelling"]:
            bits.append(f"spelling: {r['spelling']}")
        if r["case_only"]:
            bits.append("(case-insensitive match)")
        print(f"[{r['tag']}] {r['term']} — " + " · ".join(bits))
    print(f"\nknown {m['known']} / new {m['new']} · covered {len(m['covered'])} · "
          f"gap/do-not-claim/other {len(m['not_covered'])}")
    print("bullets by covered terms (req/pref/kw):")
    for code, h in m["ranked"]:
        print(f"  {code}: {h['req']}/{h['pref']}/{h['kw']} — " + ", ".join(h["terms"]))


# ---------------------------------------------------------------- normalize-keywords
SPLIT_RE = re.compile(r"\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+")
SPLIT_STOP = {"etc", "etc.", "the", "other", "others", "more", "similar", "related", "e.g.", "i.e."}
# "prompt and context engineering" → "prompt engineering", "context engineering" only for these heads
SHARED_HEADS = {"engineering", "development", "design", "management", "testing", "learning", "processing",
                "pipelines", "systems", "applications", "services", "workflows", "models", "tools", "analysis",
                "optimization", "optimisation", "integration", "monitoring", "deployment", "operations",
                "frameworks", "skills", "architecture", "automation", "evaluation", "review", "reviews", "data",
                "jobs"}


def split_term(term: str) -> list[str]:
    """One concept per term (references/ats_keywords.md): split lists of ≥ 3 words on ', ' / and / or."""
    if len(term.split()) < 3 or not SPLIT_RE.search(term):
        return [term]
    parts = [p.strip(" .;:") for p in SPLIT_RE.split(term)]
    parts = [p for p in parts if len(p) > 2 and p.casefold() not in SPLIT_STOP]
    if len(parts) < 2:
        return [term]
    last = parts[-1].split()
    if len(last) == 2 and last[1].casefold() in SHARED_HEADS and all(len(p.split()) == 1 for p in parts[:-1]):
        parts = [f"{p} {last[1]}" for p in parts[:-1]] + [parts[-1]]
    return parts


def normalize_keywords(job: dict[str, Any], jd_text: str = "") -> tuple[dict[str, Any], list[str]]:
    """Return (normalised job dict, diff lines). jd_text enables the hyphen-variant rule."""
    out, diff, seen = dict(job), [], {}
    for field, _ in KW_FIELDS:
        before, after = job.get(field) or [], []
        for term in before:
            parts = split_term(term)
            if parts != [term]:
                diff.append(f"  {field}: {term!r} → " + " · ".join(repr(x) for x in parts))
            extra = []
            for x in parts:
                spaced = x.replace("-", " ")
                if "-" in x and jd_text and has_term(x, jd_text) and has_term(spaced, jd_text):
                    extra.append((x, spaced))
            for x in parts + [sp for _, sp in extra]:
                if x.casefold() in seen:
                    if x != term or seen[x.casefold()] != field:
                        diff.append(f"  {field}: drop duplicate {x!r} (first in {seen[x.casefold()]})")
                    continue
                seen[x.casefold()] = field
                if x in [sp for _, sp in extra]:
                    diff.append(f"  {field}: + {x!r} (JD also writes {next(h for h, sp in extra if sp == x)!r})")
                after.append(x)
        out[field] = after
    return out, diff


def kw_count(job: dict[str, Any]) -> list[int]:
    return [len(job.get(f) or []) for f, _ in KW_FIELDS]


# ---------------------------------------------------------------- fit
_METRICS: dict[str, Any] = {}


def metrics() -> dict[str, Any]:
    if not _METRICS:
        _METRICS.update(json.loads(METRICS.read_text(encoding="utf-8")))
    return _METRICS


def text_width(text: str, font: str) -> float:
    w = metrics()["widths"][font]
    return sum(w.get(c, 1000 if ord(c) > 0x2E80 else 500) for c in text) / 100   # 10 pt


def wrap_lines(segs: list[tuple[str, str]], x0: float, x_rest: float, right: float) -> int:
    """Greedy word wrap at spaces (what Word does for these lines) → number of lines."""
    n, x, avail, space = 1, 0.0, right - x0, 0.0
    for text, font in segs:
        for tok in re.findall(r"[^ ]+| +", text):
            w = text_width(tok, font)
            if tok[0] == " ":
                space += w
                continue
            if x > 0 and x + space + w > avail:
                n, x, avail = n + 1, w, right - x_rest
            else:
                x += space + w
            space = 0.0
    return n


def line_segs(kind: str, line: str) -> list[tuple[str, str]]:
    if kind == "scope":
        return [(line, "italic")]
    m = re.match(r"^(?:- )?\*\*(.+?)\*\*(.*)$", line)
    return [(m.group(1), "bold"), (m.group(2), "regular")] if m else [(line, "regular")]


def fit_line(kind: str, ref: str, line: str) -> dict[str, Any]:
    """Rendered chars, predicted lines and slack (pt before the next wrap) for one CV line."""
    m = metrics()
    segs, (x0, xr), right = line_segs(kind, line), m["x0"][kind], m["right"]
    lines = wrap_lines(segs, x0, xr, right)
    lo, hi = right - 80, right + 80                  # smallest right edge that keeps this line count
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if wrap_lines(segs, x0, xr, mid) <= lines else (mid, hi)
    plain = "".join(t for t, _ in segs)
    chars = len(plain) + (4 if kind in ("bullet", "card") else 0)
    meas = m["measured"].get(f"{kind} {ref}")
    if meas:
        lines = meas["lines"]
    over = lines > m["max_lines"][kind]
    return {"kind": kind, "ref": ref, "chars": chars, "lines": lines, "max": m["max_lines"][kind],
            "slack": round(right - hi, 1), "measured": meas["source"] if meas else None, "over": over,
            "tight": not over and not meas and right - hi < m["tight_pt"]}


def fit_picks(con: sqlite3.Connection, picks: list[tuple[str, str]]) -> dict[str, Any]:
    rows = []
    for r in resolve(con, picks):
        ref = (r["code"] if r["kind"] == "scope" else
               f"{r['code']}:{r['variant_ord']}:{r['context_ord']}" if r["kind"] == "card" else
               f"{r['code']}:{r['variant_ord']}" if r["kind"] == "bullet" else f"{r['lead']}")
        rows.append(fit_line(r["kind"], ref, r["line"]))
    delta = sum(x["lines"] for x in rows if x["kind"] != "skill_row")
    budget = metrics()["delta_budget_lines"]
    return {"rows": rows, "delta_lines": delta, "budget": budget,
            "bullets": sum(x["kind"] == "bullet" for x in rows),
            "ok": not any(x["over"] for x in rows) and delta <= budget}


def print_fit(f: dict[str, Any]) -> None:
    for x in f["rows"]:
        verdict = "OVER" if x["over"] else "tight" if x["tight"] else "ok"
        note = (f"measured: {x['measured']}" if x["measured"] else
                f"slack {'> 79' if x['slack'] >= 79.9 else x['slack']} pt")
        print(f"{verdict:5} {x['kind']:9} {x['ref'][:34]:34} {x['chars']:3} chars · "
              f"{x['lines']}/{x['max']} line{'s' if x['max'] > 1 else ''} · {note}")
    over = [x["ref"] for x in f["rows"] if x["over"]]
    print(f"fit: {'PASS' if f['ok'] else 'FAIL'} · Delta block {f['delta_lines']}/{f['budget']} lines · "
          f"{f['bullets']} bullets" + (f" · over: {', '.join(over)}" if over else ""))


# ---------------------------------------------------------------- pick
# archetype → card order (v2 spec §4); aliases are matched whole-word, earliest in the line wins
ARCHETYPES = [("MLOps/platform", ("mlops", "platform", "ml platform"), ["VI", "PS"]),
              ("computer vision", ("computer vision", "computer-vision", "cv", "vision"), ["VI", "PS"]),
              ("agentic/LLM", ("agentic", "llm", "agents", "agent"), ["DT", "MC", "PS"]),
              ("backend/automation", ("backend", "automation"), ["PS", "DT", "VI"]),
              ("full-stack", ("full-stack", "full stack", "fullstack"), ["PS", "DT", "MC"])]
STATUS_RANK = {"Production": 0, "POC": 1}
STOP = set("a an and or the of to for in on with by from as at is are be into via your our their its that this "
           "we you it how what when so than through using use e g i".split())
EXCLUSIVE = [{"DT2a", "DT2b"}]


def tok_list(text: str) -> list[str]:
    """Lower-cased word tokens, stop words dropped, plural `s` stripped (keeps repeats, for BM25)."""
    return [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t
            for t in re.findall(r"[a-z0-9+#]+", text.lower()) if t not in STOP and len(t) >= 2]


def tokens(text: str) -> set[str]:
    return set(tok_list(text))


def detect_archetype(brief: str, override: str | None = None) -> tuple[str, list[str], str]:
    if override:
        for name, aliases, cards in ARCHETYPES:
            if override.casefold() in (name.casefold(),) + aliases:
                return name, cards, "--archetype"
        raise PickError(f"unknown archetype {override!r} (have {', '.join(n for n, _, _ in ARCHETYPES)})")
    for src in ("archetype", "tags:"):
        line = next((l for l in brief.splitlines() if src in l.lower()), None)
        if not line:
            continue
        text = line.lower().split(src, 1)[1].split(" — ")[0]
        hits = [(m.start(), -len(al), name, cards) for name, aliases, cards in ARCHETYPES for al in aliases
                for m in [re.search(rf"(?<![\w-]){re.escape(al)}(?![\w-])", text)] if m]
        if hits:
            _, _, name, cards = min(hits)
            return name, cards, f"01_jd.md {'Archetype' if src == 'archetype' else 'Tags'} line"
    return "backend/automation", ARCHETYPES[3][2], "default (no Archetype/Tags line)"


def duty_phrases(brief: str, job: dict[str, Any]) -> list[str]:
    line = next((l for l in brief.splitlines() if "duty phrases" in l.lower()), None)
    if line:
        body = re.split(r"duty phrases[^:\"“]*[:)]?\**", line, maxsplit=1, flags=re.I)[-1]
        quoted = re.findall(r"[\"“]([^\"”]+)[\"”]", body)
        if quoted:
            return quoted
        return [re.sub(r"^[a-z]\.\s+", "", x.strip(" .")) for x in body.split(" · ") if x.strip(" .")]
    top = next((l for l in brief.splitlines() if l.lower().lstrip("- ").startswith("top 5")), "")
    out = [re.sub(r"^\d+\s+", "", x.strip()) for x in top.split(":", 1)[-1].split(" · ") if x.strip()]
    return out + list(job.get("key_responsibilities") or [])


def jd_terms(job: dict[str, Any]) -> list[str]:
    seen: dict[str, str] = {}
    for f, _ in KW_FIELDS:
        for t in job.get(f) or []:
            seen.setdefault(t.casefold(), t)
    return list(seen.values())


def terms_in(terms: list[str], text: str) -> list[str]:
    return [t for t in terms if has_term(t, text)]


def best_overlap(text: str, phrases: list[str]) -> tuple[int, str]:
    tk = tokens(text)
    return max(((len(tk & tokens(p)), p) for p in phrases), default=(0, ""), key=lambda x: x[0])


def pick_skill_row(con: sqlite3.Connection, row: sqlite3.Row, terms: list[str], jd_tok: set[str]) -> dict[str, Any]:
    labels = [row["label"]] + [v.strip() for v in row["variants"].split("|") if v.strip()]
    lab = max(range(len(labels)), key=lambda i: (len(tokens(labels[i]) & jd_tok), -i))
    items = []
    for it in con.execute("SELECT * FROM skill_row_items WHERE row_label=? ORDER BY ord", (row["label"],)):
        spell = [it["item"]] + json.loads(it["spellings"])
        hits = [terms_in(terms, sp) for sp in spell]
        k = max(range(len(spell)), key=lambda i: (len(hits[i]), -i))
        items.append({"ord": it["ord"], "sp": k, "text": spell[k], "hits": hits[k]})
    while True:
        tok = ",".join(f"{i['ord']}" + (f"~{i['sp']}" if i["sp"] else "") for i in items)
        default = tok == ",".join(str(i) for i in range(len(items))) and len(items) == len(
            con.execute("SELECT 1 FROM skill_row_items WHERE row_label=?", (row["label"],)).fetchall())
        spec = f"{row['label']}:{lab}" + ("" if default else f"={tok}")
        line = f"**{labels[lab]}:** " + " · ".join(i["text"] for i in items)
        f = fit_line("skill_row", labels[lab], line)
        if not f["over"] or len(items) == 1:
            break
        drop = min(range(len(items)), key=lambda i: (len(items[i]["hits"]), -len(items[i]["text"]), -i))
        items.pop(drop)
    hit = sorted({t for i in items for t in i["hits"]}, key=terms.index)
    return {"spec": spec, "label": labels[lab], "hits": hit, "fit": f}


def suggest_picks(con: sqlite3.Connection, app_dir: Path, archetype: str | None = None,
                  n_cards: int | None = None) -> dict[str, Any]:
    brief_p, kw_p = app_dir / "01_jd.md", app_dir / "07_job_keywords.json"
    if not kw_p.exists():
        raise PickError(f"{kw_p} missing")
    brief = brief_p.read_text(encoding="utf-8") if brief_p.exists() else ""
    job = json.loads(kw_p.read_text(encoding="utf-8"))
    jd_text = next((p.read_text(encoding="utf-8") for p in (app_dir / "jd.md", brief_p) if p.exists()), "")
    terms, duties = jd_terms(job), duty_phrases(brief, job)
    arch, cards, arch_src = detect_archetype(brief, archetype)
    jd_tok = tokens(jd_text + " " + " ".join(terms))
    title_ref = duties + terms + [job.get("role", "")]
    sc = score_bullets(con, job)        # spec v3 §3.4: concept weight × strength × idf + BM25
    jc, idx = sc["jd"], sc["idx"]

    def bullet_score(b: sqlite3.Row) -> float:
        return sc["bullets"][b["code"]]["score"]

    if n_cards and n_cards != len(cards):
        if n_cards < len(cards):
            cards = cards[:n_cards]
        else:
            rest = [c for c in ("PS", "DT", "VI", "MC", "WR") if c not in cards]
            rest.sort(key=lambda c: -sum(bullet_score(b) for b in con.execute(
                "SELECT * FROM bullets WHERE project_code=? AND status!='Legacy wording'", (c,))))
            cards = cards + rest[:n_cards - len(cards)]
    per_card = 3 if len(cards) == 2 else 2

    # candidates per card: concept score, then status, then the best lead ↔ duty overlap, then library
    # order; Legacy wording (superseded by the cards) only as filler
    cand: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        rows = con.execute("SELECT rowid, * FROM bullets WHERE project_code=?", (c,)).fetchall()
        lst = [{"b": b, "hits": sc["bullets"][b["code"]]["literal"], "score": bullet_score(b),
                "concepts": sc["bullets"][b["code"]]["hits"], "duty": max(
            (best_overlap(r[0], duties)[0] for r in con.execute(
                "SELECT lead FROM bullet_leads WHERE bullet_code=?", (b["code"],))), default=0)} for b in rows]
        lst.sort(key=lambda x: (x["b"]["status"] == "Legacy wording", -x["score"],
                                STATUS_RANK.get(x["b"]["status"], 2), -x["duty"], x["b"]["rowid"]))
        cand[c] = lst
    chosen: dict[str, list[dict[str, Any]]] = {c: [] for c in cards}
    taken: set[str] = set()
    bodies: set[str] = set()

    def ok(x: dict[str, Any]) -> bool:
        code = x["b"]["code"]
        return (code not in taken and x["b"]["body"] not in bodies
                and not any(code in ex and taken & (ex - {code}) for ex in EXCLUSIVE))

    def take(c: str) -> bool:
        for x in cand[c]:
            if ok(x):
                lead = pick_lead(x["b"])
                if lead is None:
                    continue
                x["lead"] = lead
                chosen[c].append(x)
                taken.add(x["b"]["code"])
                bodies.add(x["b"]["body"])
                return True
        return False

    def lead_info(b: sqlite3.Row, ld: sqlite3.Row) -> dict[str, Any]:
        f = fit_line("bullet", f"{b['code']}:{ld['ord']}", f"- **{ld['lead']} —** {b['body']}")
        n, duty = best_overlap(ld["lead"], duties)
        cw, named = lead_jd_score(ld["lead"], jc, idx)
        return {"ord": ld["ord"], "lead": ld["lead"], "overlap": n, "duty": duty, "fit": f, "cw": cw, "named": named}

    def pick_lead(b: sqlite3.Row) -> dict[str, Any] | None:
        scored = [lead_info(b, ld) for ld in idx["leads"][b["code"]]]
        fitting = [x for x in scored if not x["fit"]["over"]]
        if not fitting:
            return None
        # v3: a lead naming a JD-weighted concept first (JD alias verbatim counts double), then duty overlap
        return max(fitting, key=lambda x: (x["cw"], x["overlap"], not x["fit"]["tight"], -x["ord"]))

    for c in cards:
        for _ in range(per_card):
            take(c)
    while sum(len(v) for v in chosen.values()) < 6:   # a short card hands its slot to the best other card
        spare = [(c, next((x for x in cand[c] if ok(x)), None)) for c in cards if len(chosen[c]) < 3]
        spare = [(c, x) for c, x in spare if x]
        if not spare:
            break
        take(max(spare, key=lambda cx: cx[1]["score"])[0])

    # card title (token overlap with duty phrases + terms), context ord 0 unless the budget needs 1 line
    card_pick = {}
    for c in cards:
        titles = [r[0] for r in con.execute("SELECT title FROM project_titles WHERE project_code=? ORDER BY ord", (c,))]
        ctxs = [r[0] for r in con.execute("SELECT text FROM project_contexts WHERE project_code=? ORDER BY ord", (c,))]
        t = max(range(len(titles)), key=lambda i: (len(tokens(titles[i]) & tokens(" ".join(title_ref))), -i))
        ctx_fit = [fit_line("card", f"{c}:{t}:{i}", f"**{CARD_MARK}{titles[t]}** — {x}") for i, x in enumerate(ctxs)]
        ci = next(i for i, f in enumerate(ctx_fit) if not f["over"]) if any(not f["over"] for f in ctx_fit) else 0
        card_pick[c] = {"title": t, "ctx": ci, "titles": titles, "ctx_fit": ctx_fit,
                        "overlap": sorted(tokens(titles[t]) & tokens(" ".join(title_ref)))}

    scopes = con.execute("SELECT * FROM scope_lines ORDER BY rowid").fetchall()
    scope = max(scopes, key=lambda r: (len(terms_in(terms, r["text"])), -scopes.index(r)))

    def lines_now() -> int:
        return (1 + sum(card_pick[c]["ctx_fit"][card_pick[c]["ctx"]]["lines"] for c in cards if chosen[c])
                + sum(x["lead"]["fit"]["lines"] for v in chosen.values() for x in v))

    budget = metrics()["delta_budget_lines"]
    for c in cards:   # over budget → shortest-fitting context variant of each card, in card order
        if lines_now() <= budget:
            break
        best = min(range(len(card_pick[c]["ctx_fit"])), key=lambda i: (card_pick[c]["ctx_fit"][i]["lines"], i))
        card_pick[c]["ctx"] = best
    while lines_now() > budget and sum(len(v) for v in chosen.values()) > 5:
        c = max(cards, key=lambda c: len(chosen[c]))
        chosen[c].pop()

    rows = con.execute("SELECT * FROM skill_rows ORDER BY ord").fetchall()
    row_picks = [pick_skill_row(con, r, terms, jd_tok) for r in rows]
    order = sorted(range(len(rows)), key=lambda i: (-len(row_picks[i]["hits"]), i))[:6]
    scope = polish_picks(con, terms, jc, sc, cards, cand, chosen, card_pick, scope, scopes,
                         [("skill_row", row_picks[i]["spec"]) for i in order], lead_info, budget)
    for c in cards:
        cp = card_pick[c]
        cp["overlap"] = sorted(tokens(cp["titles"][cp["title"]]) & tokens(" ".join(title_ref)))

    picks: list[tuple[str, str]] = [("scope", scope["code"])]
    why = [f"scope {scope['code']} — terms: {', '.join(terms_in(terms, scope['text'])) or '-'}"]
    for c in cards:
        if not chosen[c]:
            continue
        cp = card_pick[c]
        picks.append(("card", f"{c}:{cp['title']}:{cp['ctx']}"))
        why.append(f"card {c}:{cp['title']}:{cp['ctx']} \"{cp['titles'][cp['title']]}\" — title tokens: "
                   f"{', '.join(cp['overlap']) or '-'}")
        for x in chosen[c]:
            ld = x["lead"]
            picks.append(("bullet", f"{x['b']['code']}:{ld['ord']}"))
            why.append(f"  bullet {x['b']['code']}:{ld['ord']} [{x['b']['status']}] score {x['score']:.1f} · concepts "
                       f"{', '.join(c for c, _, _ in x['concepts'][:5]) or '-'} · literal {len(x['hits'])} "
                       f"({', '.join(x['hits'][:5])}{', …' if len(x['hits']) > 5 else ''}) · lead \"{ld['lead']}\""
                       + (f" names {', '.join(ld['named'])}" if ld["named"] else "")
                       + (f" ↔ duty \"{ld['duty'][:50]}\" (overlap {ld['overlap']})" if ld["overlap"] else ""))
    for i in order:
        rp = row_picks[i]
        picks.append(("skill_row", rp["spec"]))
        why.append(f"skill_row {rp['label']} — {len(rp['hits'])} terms: {', '.join(rp['hits']) or '-'}")
    ranked = sorted(sc["bullets"], key=lambda b: (-sc["bullets"][b]["score"], idx["bullets"][b]["rowid"]))
    return {"archetype": arch, "archetype_src": arch_src, "cards": cards, "n_terms": len(terms),
            "n_duties": len(duties), "picks": picks, "why": why, "fit": fit_picks(con, picks), "ranked": ranked}


TPL = ROOT / "cv" / "template" / "CV_template_v3.md"


def polish_picks(con: sqlite3.Connection, terms: list[str], jc: dict[str, Any], sc: dict[str, Any],
                 cards: list[str], cand: dict[str, list[dict[str, Any]]], chosen: dict[str, list[dict[str, Any]]],
                 card_pick: dict[str, dict[str, Any]], scope: sqlite3.Row, scopes: list[sqlite3.Row],
                 skill_picks: list[tuple[str, str]], lead_info: Any, budget: int) -> sqlite3.Row:
    """Deterministic hill climb over scope / card title + context / lead ords / same-card bullet swaps.
    Objective (lexicographic): distinct JD terms on the CV (what the ATS counts; the template's fixed
    roles, Education and the picked Skills rows are the base), their type weight, the bullets' concept
    score, the leads' concept weight. Keeps every v2 rule: ≤ 3 per card, EXCLUSIVE pairs, unique bodies,
    Legacy wording only in place of Legacy wording, every line fits, Delta block within budget.
    Mutates chosen / card_pick; returns the scope row."""
    weight = {r["term"].casefold(): r["w"] for r in jc["terms"]}
    base = ""
    if TPL.exists():
        lines = cv_with_picks(con, TPL, skill_picks)
        lines[4] = ""        # the Summary is written after the picks
        base = ats_text(lines)
    base_found = frozenset(t for t in terms if has_term(t, base))
    found_cache: dict[str, frozenset[str]] = {}
    fit_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    lead_cache: dict[str, float] = {}
    texts = {c: ([r[0] for r in con.execute("SELECT title FROM project_titles WHERE project_code=? ORDER BY ord", (c,))],
                 [r[0] for r in con.execute("SELECT text FROM project_contexts WHERE project_code=? ORDER BY ord", (c,))])
             for c in cards}
    leads, bullets = sc["idx"]["leads"], sc["idx"]["bullets"]
    by_code = {x["b"]["code"]: x for c in cards for x in cand[c]}

    def fit(kind: str, ref: str, line: str) -> dict[str, Any]:
        key = (kind, ref, line)
        if key not in fit_cache:
            fit_cache[key] = fit_line(kind, ref, line)
        return fit_cache[key]

    def found(line: str) -> frozenset[str]:
        if line not in found_cache:
            found_cache[line] = frozenset(t for t in terms if has_term(t, line))
        return found_cache[line]

    def value(st: tuple) -> tuple | None:
        sc_code, per_card = st
        srow = next(r for r in scopes if r["code"] == sc_code)
        if fit("scope", sc_code, srow["text"])["over"]:
            return None
        n_lines, hit, cs, cw = 1, set(base_found | found(srow["text"])), 0.0, 0.0
        for c, t, ci, bl in per_card:
            if not bl:
                continue
            title, ctx = texts[c][0][t], texts[c][1][ci]
            f = fit("card", f"{c}:{t}:{ci}", f"**{CARD_MARK}{title}** — {ctx}")
            if f["over"]:
                return None
            n_lines += f["lines"]
            hit |= found(f"{title} — {ctx}")
            for code, lo in bl:
                lead, body = leads[code][lo]["lead"], bullets[code]["body"]
                f = fit("bullet", f"{code}:{lo}", f"- **{lead} —** {body}")
                if f["over"]:
                    return None
                n_lines += f["lines"]
                hit |= found(f"{lead} — {body}")
                cs += sc["bullets"][code]["score"]
                if lead not in lead_cache:
                    lead_cache[lead] = lead_jd_score(lead, jc, sc["idx"])[0]
                cw += lead_cache[lead]
        if n_lines > budget:
            return None
        return len(hit), sum(weight.get(t.casefold(), 1) for t in hit), round(cs, 3), round(cw, 3)

    def allowed(new: str, old: str, bl_all: list[str]) -> bool:
        others = [b for b in bl_all if b != old]
        nb = bullets[new]
        return (new not in others and nb["body"] not in {bullets[b]["body"] for b in others}
                and not any(new in ex and set(others) & (ex - {new}) for ex in EXCLUSIVE)
                and (nb["status"] != "Legacy wording" or bullets[old]["status"] == "Legacy wording"))

    st = (scope["code"], tuple((c, card_pick[c]["title"], card_pick[c]["ctx"],
                                tuple((x["b"]["code"], x["lead"]["ord"]) for x in chosen[c])) for c in cards))
    cur = value(st)
    for _ in range(40):
        if cur is None:
            break
        best, best_v = None, cur
        sc_code, per_card = st
        moves = [(r["code"], per_card) for r in scopes]
        all_b = [b for _, _, _, bl in per_card for b, _ in bl]
        for i, (c, t, ci, bl) in enumerate(per_card):
            def with_card(nt: int, nci: int, nbl: tuple) -> tuple:
                return sc_code, per_card[:i] + ((c, nt, nci, nbl),) + per_card[i + 1:]
            moves += [with_card(nt, nci, bl) for nt in range(len(texts[c][0])) for nci in range(len(texts[c][1]))]
            for k, (code, lo) in enumerate(bl):
                for x in [code] + [y["b"]["code"] for y in cand[c] if allowed(y["b"]["code"], code, all_b)]:
                    moves += [with_card(t, ci, bl[:k] + ((x, ld["ord"]),) + bl[k + 1:]) for ld in leads[x]]
        for mv in moves:
            if mv == st:
                continue
            v = value(mv)
            if v is not None and v > best_v:
                best, best_v = mv, v
        if best is None:
            break
        st, cur = best, best_v
    sc_code, per_card = st
    for c, t, ci, bl in per_card:
        titles = texts[c][0]
        card_pick[c].update(title=t, ctx=ci, ctx_fit=[fit_line("card", f"{c}:{t}:{i}", f"**{CARD_MARK}{titles[t]}** — {x}")
                                                       for i, x in enumerate(texts[c][1])])
        chosen[c] = []
        for code, lo in bl:
            x = by_code[code]
            x["lead"] = lead_info(x["b"], leads[code][lo])
            chosen[c].append(x)
    return next(r for r in scopes if r["code"] == sc_code)


# ---------------------------------------------------------------- ingest-ats
def ingest_ats(con: sqlite3.Connection, jd: str, app_dir: Path, date: str | None = None) -> dict[str, Any]:
    """Log one ATS run (07_job_keywords.json + 07_external_ats.json) and replay it onto the keyword map."""
    job = json.loads((app_dir / "07_job_keywords.json").read_text(encoding="utf-8"))
    ats = json.loads((app_dir / "07_external_ats.json").read_text(encoding="utf-8"))
    found = {t.casefold() for t in ats["keyword_match"]["found"]}
    freq = {r["term"].casefold(): r["jd_count"] for r in ats.get("repeated_terms") or []}
    date = date or dt.date.today().isoformat()
    rows, seen = [], set()
    for field, typ in KW_FIELDS:
        for t in job.get(field) or []:
            if t.casefold() not in seen:
                seen.add(t.casefold())
                rows.append((jd, t, typ, int(t.casefold() in found), freq.get(t.casefold()), date, len(rows)))
    with con:
        con.execute("DELETE FROM ats_ingest WHERE jd=?", (jd,))
        con.executemany("INSERT INTO ats_ingest VALUES(?,?,?,?,?,?,?)", rows)
    r = apply_ingest(con, jd)
    r["unresolved"] = assign_concepts(con)
    return r


def replay_ingest(con: sqlite3.Connection) -> int:
    """After init: replay every logged run (oldest first) and the set-coverage overrides."""
    jds = [r[0] for r in con.execute("SELECT jd FROM ats_ingest GROUP BY jd ORDER BY min(date), min(rowid)")]
    for jd in jds:
        apply_ingest(con, jd)
    if not jds:
        apply_overrides(con)
    return len(jds)


def apply_ingest(con: sqlite3.Connection, jd: str) -> dict[str, Any]:
    known = {r["term"].casefold(): r for r in con.execute("SELECT * FROM keywords")}
    bullets = con.execute("SELECT code, keywords_carried FROM bullets").fetchall()
    skills = con.execute("SELECT name, variants FROM skills").fetchall()
    new, updated, unclassified, conflicts, resolved = [], [], [], [], []
    aliases = alias_index(con)
    claims = {r[0]: r[1] for r in con.execute("SELECT code, claim FROM concepts")}
    with con:
        for r in con.execute("SELECT * FROM ats_ingest WHERE jd=? ORDER BY ord", (jd,)).fetchall():
            k = known.get(r["term"].casefold())
            if k is None:
                # spec v3 §3.7: resolve (rules 1–4); a hit assigns the concept and inherits its claim —
                # a fuzzy hit (3/4) never makes a missing term `covered`, only SoT-supported wording
                res = resolve_term(r["term"], aliases)
                code = res["code"]
                if r["found"]:
                    cov, note = "covered", "" if code else f"auto: unresolved ({jd} {r['date']})"
                elif code:
                    cov = claims[code]
                    if cov == "covered" and res["rule"] > 2:
                        cov = "SoT-supported wording"
                    note = f"auto: concept {code} via rule {res['rule']} ({jd} {r['date']})"
                else:
                    cov, note = "gap", f"auto: unclassified, unresolved ({jd} {r['date']})"
                con.execute("INSERT INTO keywords VALUES(?,?,?,?,?,?,?,?,?)",
                            (r["term"], r["type"], cov, "", "", r["freq"], note, "", code))
                term = r["term"]
                new.append(term)
                if code:
                    resolved.append(f"{term} → {code} (rule {res['rule']})")
                if not r["found"] and not code:
                    unclassified.append(term)
            else:
                term, cov = k["term"], k["coverage"]
                if r["found"] and cov == "do not claim":
                    conflicts.append(term)        # found in the CV text, but the writer ruled it out: keep
                elif r["found"] and cov != "covered":
                    cov = "covered"
                    con.execute("UPDATE keywords SET coverage='covered', notes=CASE WHEN notes LIKE "
                                "'auto: unclassified%' THEN '' ELSE notes END WHERE term=?", (term,))
                if r["freq"] and (k["freq"] or 0) < r["freq"]:
                    con.execute("UPDATE keywords SET freq=? WHERE term=?", (r["freq"], term))
                if not con.execute("SELECT 1 FROM keyword_jds WHERE term=? AND jd=?", (term, jd)).fetchone() \
                        or cov != k["coverage"]:
                    updated.append(term)
            if not con.execute("SELECT 1 FROM keyword_jds WHERE term=? AND jd=?", (term, jd)).fetchone():
                con.execute("INSERT INTO keyword_jds VALUES(?,?)", (term, jd))
            if cov == "covered":
                for b in bullets:
                    if has_term(term, b["keywords_carried"]) and not con.execute(
                            "SELECT 1 FROM keyword_bullets WHERE term=? AND bullet_code=?", (term, b["code"])).fetchone():
                        con.execute("INSERT INTO keyword_bullets VALUES(?,?)", (term, b["code"]))
                for sk in skills:
                    if (has_term(term, sk["name"]) or has_term(term, sk["variants"] or "")) and not con.execute(
                            "SELECT 1 FROM keyword_skills WHERE term=? AND skill_name=?", (term, sk["name"])).fetchone():
                        con.execute("INSERT INTO keyword_skills VALUES(?,?)", (term, sk["name"]))
            known[term.casefold()] = con.execute("SELECT * FROM keywords WHERE term=?", (term,)).fetchone()
    apply_overrides(con)
    left = con.execute("SELECT COUNT(*) FROM keywords k JOIN keyword_jds j ON j.term=k.term WHERE j.jd=? "
                       "AND k.notes LIKE 'auto: unclassified%'", (jd,)).fetchone()[0]
    return {"new": new, "updated": updated, "unclassified": left, "conflicts": conflicts, "resolved": resolved}


def apply_overrides(con: sqlite3.Connection) -> None:
    with con:
        for o in con.execute("SELECT * FROM coverage_overrides").fetchall():
            con.execute("UPDATE keywords SET coverage=?, notes=? WHERE term=?", (o["coverage"], o["note"], o["term"]))


def set_coverage(con: sqlite3.Connection, term: str, coverage: str, note: str | None) -> str:
    k = con.execute("SELECT * FROM keywords WHERE term=?", (term,)).fetchone() or next(
        (r for r in con.execute("SELECT * FROM keywords") if r["term"].casefold() == term.casefold()), None)
    if not k:
        raise PickError(f"unknown keyword {term!r} (ingest-ats or seed first)")
    if note is None:
        note = "" if (k["notes"] or "").startswith("auto: unclassified") else k["notes"]
    with con:
        con.execute("INSERT OR REPLACE INTO coverage_overrides VALUES(?,?,?,?)",
                    (k["term"], coverage, note, dt.date.today().isoformat()))
    apply_overrides(con)
    return f"{k['term']}: {k['coverage']} → {coverage}" + (f" · {note}" if note else "")


# ---------------------------------------------------------------- status
def app_status(con: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for a in con.execute("SELECT * FROM applications").fetchall():
        d = app_path(a["dir"])
        row = {"jd": a["jd"], "company": a["company"], "title": a["title"], "dir": a["dir"], "date": a["created"],
               "qa": con.execute("SELECT count(*) FROM interview_qa WHERE jd=?", (a["jd"],)).fetchone()[0],
               "outreach": (con.execute("SELECT status FROM outreach WHERE jd=? ORDER BY id DESC LIMIT 1",
                                        (a["jd"],)).fetchone() or ["-"])[0],
               "ats": None, "kw": "-", "layout": "-", "util": "-", "hr": "-", "hm": "-", "verdict": "-"}
        if (d / "07_external_ats.json").exists():
            ats = json.loads((d / "07_external_ats.json").read_text(encoding="utf-8"))
            km = ats["keyword_match"]
            row["ats"] = ats["ats_score"].get("overall_score")
            row["kw"] = f"{len(km['found'])}/{km['n_terms']}"
        if (d / "06_layout.json").exists():
            lay = json.loads((d / "06_layout.json").read_text(encoding="utf-8"))
            row["layout"] = lay.get("status", "-")
            util = next((c["detail"] for c in lay.get("checks", []) if c["check"] == "page_utilization"), "")
            m = re.match(r"(\d+%)", str(util))
            row["util"] = m.group(1) if m else "-"
        if (d / "04_review.md").exists():
            rv = (d / "04_review.md").read_text(encoding="utf-8")
            for key in ("hr", "hm"):
                m = re.search(rf"\b{key.upper()} (\d+)", rv)
                row[key] = m.group(1) if m else "-"
            m = re.search(r"VERDICT:\s*\**([A-Z][A-Z -]*[A-Z])", rv)
            row["verdict"] = m.group(1) if m else "-"
        out.append(row)
    return sorted(out, key=lambda r: -(r["ats"] if r["ats"] is not None else -1))


def print_status(rows: list[dict[str, Any]]) -> None:
    print("| JD | Company | Title | ATS | Keywords | Layout | Util | HR | HM | Verdict | Date | Dir | QA | Outreach |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['jd']} | {r['company']} | {r['title']} | {r['ats'] if r['ats'] is not None else '-'} | {r['kw']} | "
              f"{r['layout']} | {r['util']} | {r['hr']} | {r['hm']} | {r['verdict']} | {r['date']} | "
              f"`{Path(r['dir']).parent.name}/{Path(r['dir']).name}` | {r['qa']} | {r['outreach']} |")


# ---------------------------------------------------------------- notion: applications
def cv_markdown(cv: Path) -> list[str]:
    """03_cv.md → Notion Markdown: section headings, role lines with dates, cards, bullets."""
    lines = cv.read_bytes().decode("utf-8").splitlines()
    out = lines[:3] + ["", lines[4]]
    for l in lines[5:]:
        if not l.strip():
            continue
        if re.fullmatch(r"\*\*[A-Z][A-Z ,&/]+\*\*", l) or l.startswith("**LANGUAGES**"):
            head, _, rest = l.partition("**  ")
            out += ["", f"### {head.strip('*').title()}"] + ([rest.strip()] if rest.strip() else [])
        elif "\t" in l and l.startswith("**"):           # role line: title | company <tab> dates
            head, years = l.split("\t", 1)
            out += ["", f"**{head.replace('**', '')}** · {years.strip()}"]
        else:
            out.append(l)
    return out


def application_payload(con: sqlite3.Connection, jd: str | None = None) -> list[dict[str, Any]]:
    out = []
    q = "SELECT * FROM applications" + (" WHERE jd=?" if jd else "") + " ORDER BY created, jd"
    status = {r["jd"]: r for r in app_status(con)}
    for a in con.execute(q, (jd,) if jd else ()).fetchall():
        d, st = app_path(a["dir"]), status[a["jd"]]
        picks = recorded_picks(con, a["jd"])
        mentions = []
        for kind, val in picks:
            if kind == "bullet":
                code = val.split(":")[0]
                nid = con.execute("SELECT notion_id FROM bullets WHERE code=?", (code,)).fetchone()[0]
                mentions.append(f'<mention-page url="{notion_url(nid)}"/> ({code})' if nid else code)
        body = [f"<callout>ATS {st['ats']} ({st['kw']} keywords) · layout {st['layout']} {st['util']} · "
                f"HR {st['hr']} · HM {st['hm']} · verdict {st['verdict']} · {a['created']}</callout>", "",
                "## CV text"] + (cv_markdown(d / "03_cv.md") if (d / "03_cv.md").exists() else ["(no 03_cv.md)"])
        body += ["", "## Picks", f"`{picks_to_spec(picks)}`", "", "Bullets: " + " · ".join(mentions), "",
                 "## Files", f"`{a['dir']}`"]
        body += [f"- {p.name}" for p in sorted(d.iterdir()) if p.is_file()] if d.exists() else []
        text = "\n".join(body)
        if len(text) > 8000:   # keep the page under 8 k: the CV text goes first
            text = text[:7900].rsplit("\n", 1)[0] + "\n…(truncated)"
        out.append({"properties": {"title": f"{a['company']} — {a['title']}"}, "content": text,
                    "jd": a["jd"], "notion_id": a["notion_id"] or ""})
    return out


# ---------------------------------------------------------------- views
# ---- concept layer (library_db_spec_v3.md §2, §3.2) --------------------------------------------
def assign_concepts(con: sqlite3.Connection) -> list[str]:
    """Set keywords.concept_code from concept_aliases (exact, NOCASE). Returns the orphan terms."""
    with con:
        con.execute("""UPDATE keywords SET concept_code = (
                           SELECT concept_code FROM concept_aliases a WHERE a.alias = keywords.term COLLATE NOCASE)
                       WHERE concept_code IS NULL""")
    return [r[0] for r in con.execute("SELECT term FROM keywords WHERE concept_code IS NULL ORDER BY term COLLATE NOCASE")]


def concept_rows(con: sqlite3.Connection, kind: str | None = None, claim: str | None = None) -> list[sqlite3.Row]:
    sql, params = "SELECT * FROM concepts WHERE 1=1", []
    if kind:
        sql, params = sql + " AND kind=?", params + [kind]
    if claim:
        sql, params = sql + " AND claim=?", params + [CLAIMS.get(claim.casefold(), claim)]
    return con.execute(sql + " ORDER BY code", params).fetchall()


def print_concepts(con: sqlite3.Connection, kind: str | None, claim: str | None, orphans: bool) -> None:
    if orphans:
        orphan_kw = assign_concepts(con)
        no_ev = [r[0] for r in con.execute("""SELECT code FROM concepts c WHERE claim IN ('covered','SoT-supported wording')
                                               AND NOT EXISTS (SELECT 1 FROM concept_evidence e WHERE e.concept_code=c.code)""")]
        print(f"keywords without a concept: {len(orphan_kw)}" + (" — " + " · ".join(orphan_kw) if orphan_kw else ""))
        print(f"claimable concepts without evidence: {len(no_ev)}" + (" — " + " · ".join(no_ev) if no_ev else ""))
        return
    for c in concept_rows(con, kind, claim):
        n_al = con.execute("SELECT COUNT(*) FROM concept_aliases WHERE concept_code=?", (c["code"],)).fetchone()[0]
        n_kw = con.execute("SELECT COUNT(*) FROM keywords WHERE concept_code=?", (c["code"],)).fetchone()[0]
        ev = [f"{r['code']}{'' if r['strength'] == 'direct' else '?'}" for r in con.execute(
            "SELECT * FROM concept_evidence WHERE concept_code=? ORDER BY kind, strength, code", (c["code"],))]
        print(f"{c['code']:<16} {c['kind']:<9} {c['claim']:<22} {n_al:>3} aliases/{n_kw:>3} kw · {c['name']}"
              + (f" · ev: {', '.join(ev)}" if ev else ""))


def print_concept(con: sqlite3.Connection, code: str) -> None:
    c = con.execute("SELECT * FROM concepts WHERE code=?", (code,)).fetchone()
    if not c:
        raise PickError(f"no concept {code!r} (see `concepts`)")
    print(f"{c['code']} — {c['name']} · {c['kind']} · claim: {c['claim']} · canonical: {c['canonical']}")
    if c["definition"] or c["esco"]:
        print(f"  {c['definition']} {('ESCO: ' + c['esco']) if c['esco'] else ''}".rstrip())
    if c["notes"]:
        print(f"  notes: {c['notes']}")
    al = con.execute("""SELECT a.alias, k.type, k.coverage, k.freq FROM concept_aliases a
                        LEFT JOIN keywords k ON k.term = a.alias COLLATE NOCASE
                        WHERE a.concept_code=? ORDER BY k.freq DESC, a.alias COLLATE NOCASE""", (c["code"],)).fetchall()
    print(f"  aliases ({len(al)}):")
    for a in al:
        cov = f" [{a['coverage']}]" if a["coverage"] and a["coverage"] != c["claim"] else ""
        print(f"    {a['alias']}{cov}" + (f" · {a['type']}" if a["type"] else " · (not in keyword map)"))
    for kind in ("bullet", "skill", "context", "lead", "scope"):
        ev = con.execute("SELECT * FROM concept_evidence WHERE concept_code=? AND kind=? ORDER BY strength, code",
                         (c["code"], kind)).fetchall()
        if ev:
            print(f"  {kind}s: " + " · ".join(f"{e['code']} ({e['strength']})" for e in ev))
    rel = con.execute("SELECT rel, dst FROM concept_relations WHERE src=? UNION ALL "
                      "SELECT rel || ' ←', src FROM concept_relations WHERE dst=?", (c["code"], c["code"])).fetchall()
    if rel:
        print("  relations: " + " · ".join(f"{r[0]} {r[1]}" for r in rel))
    jds = sorted({r[0] for r in con.execute("""SELECT j.jd FROM keyword_jds j JOIN keywords k ON k.term=j.term
                                                WHERE k.concept_code=?""", (c["code"],))})
    if jds:
        print("  JDs: " + ", ".join(jds))


# ---- concept bootstrap (library_db_spec_v3.md §3.1) -------------------------------------------
# Groups the flat keyword map into concept proposals for the writer to name; never writes the seed.
STEM_MAP = {"agentic": "agent", "agents": "agent", "llms": "llm", "apis": "api", "pipelines": "pipeline",
            "containerization": "container", "containerize": "container", "containers": "container",
            "orchestration": "orchestr", "orchestrate": "orchestr", "orchestrating": "orchestr",
            "evaluation": "eval", "evaluate": "eval", "evals": "eval", "optimisation": "optim",
            "optimization": "optim", "optimize": "optim", "optimise": "optim", "deployment": "deploy",
            "deploying": "deploy", "deployments": "deploy", "monitoring": "monitor", "observability": "monitor",
            "testing": "test", "tests": "test", "ci": "cicd", "cd": "cicd", "ci/cd": "cicd",
            "microservices": "microservice", "serving": "serve", "inference": "serve"}
# tokens too common in JD phrasing to link two terms on their own
WEAK = set("ml ai machine learning software engineering development production system systems application "
           "applications experience knowledge tool tools solution solutions technical modern model models data "
           "platform platforms service services end production ready enabled based powered backed engineer "
           "engineering several years strong proven".split())


def stem(tok: str) -> str:
    tok = STEM_MAP.get(tok, tok)
    if len(tok) > 7:
        tok = tok[:6]
    return tok


def kw_stems(term: str) -> set[str]:
    return {stem(t) for t in tokens(term)}


def trigrams(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    s = f"  {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def bootstrap_concepts(con: sqlite3.Connection, min_link: float = 0.34) -> dict[str, Any]:
    kws = [dict(r) for r in con.execute("SELECT * FROM keywords ORDER BY term")]
    link_b = {k["term"]: {r[0] for r in con.execute("SELECT bullet_code FROM keyword_bullets WHERE term=?", (k["term"],))}
              for k in kws}
    link_s = {k["term"]: {r[0] for r in con.execute("SELECT skill_name FROM keyword_skills WHERE term=?", (k["term"],))}
              for k in kws}
    jds = {k["term"]: [r[0] for r in con.execute("SELECT jd FROM keyword_jds WHERE term=? ORDER BY jd", (k["term"],))]
           for k in kws}
    # a shared bullet/skill is a strong signal only when the bullet/skill is not a hub
    hub_b = {b for b, n in con.execute("SELECT bullet_code, COUNT(*) FROM keyword_bullets GROUP BY 1") if n > 6}
    hub_s = {s for s, n in con.execute("SELECT skill_name, COUNT(*) FROM keyword_skills GROUP BY 1") if n > 4}
    st = {k["term"]: kw_stems(k["term"]) for k in kws}
    tg = {k["term"]: trigrams(k["term"]) for k in kws}
    weak = {stem(w) for w in WEAK}
    strong = {k["term"]: {t for t in st[k["term"]] if t not in weak} for k in kws}

    def score(a: str, b: str) -> tuple[float, str]:
        shared = strong[a] & strong[b]
        s_tok = jaccard(strong[a], strong[b]) if shared else 0.0
        s_ev = 1.0 if (link_b[a] & link_b[b]) - hub_b or (link_s[a] & link_s[b]) - hub_s else 0.0
        s_tri = jaccard(tg[a], tg[b])
        s = 0.5 * s_tok + 0.3 * s_ev + 0.2 * s_tri
        why = ("stem:" + "/".join(sorted(shared)) if shared else "") + (" evidence" if s_ev else "") \
            + (f" tri={s_tri:.2f}" if s_tri >= 0.5 else "")
        # one shared strong stem that is the head (last) word of either term links on its own
        if shared and (stem(tokens_tail(a)) in shared or stem(tokens_tail(b)) in shared):
            s = max(s, min_link)
        return s, why.strip()

    # average-linkage agglomeration: a merge needs the *mean* cross-pair score ≥ min_link, so chains
    # like production → production LLM apps → LLM → LLM APIs → APIs → REST APIs cannot form
    terms = [k["term"] for k in kws]
    sc: dict[tuple[str, str], tuple[float, str]] = {}
    for i, a in enumerate(terms):
        for b in terms[i + 1:]:
            sc[(a, b)] = sc[(b, a)] = score(a, b)
    clusters: list[list[str]] = [[t] for t in terms]
    edges: dict[str, list[str]] = {}
    while True:
        best, pair = 0.0, None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                cross = [sc[(a, b)][0] for a in clusters[i] for b in clusters[j]]
                m = sum(cross) / len(cross)
                if m > best:
                    best, pair = m, (i, j)
        if pair is None or best < min_link:
            break
        i, j = pair
        for a in clusters[i]:
            for b in clusters[j]:
                s_, why = sc[(a, b)]
                if s_ >= min_link:
                    edges.setdefault(a, []).append(f"{b} ({why})")
        clusters[i] += clusters.pop(j)
    parent = {t: c[0] for c in clusters for t in c}

    def find(x: str) -> str:
        return parent[x]

    groups: dict[str, list[dict[str, Any]]] = {}
    for k in kws:
        groups.setdefault(find(k["term"]), []).append(k)
    out = []
    for root, members in groups.items():
        members.sort(key=lambda k: (-(k["freq"] or 0), k["term"]))
        cov = sorted({k["coverage"] for k in members})
        bl = sorted({b for k in members for b in link_b[k["term"]]})
        sk = sorted({s for k in members for s in link_s[k["term"]]})
        common = set.intersection(*(strong[k["term"]] for k in members)) if members else set()
        head = "_".join(sorted(common)) or stem(tokens_tail(members[0]["term"]))
        out.append({"proposed_code": re.sub(r"[^A-Z0-9_]", "", head.upper())[:18] or "X",
                    "size": len(members), "terms": [k["term"] for k in members],
                    "types": sorted({k["type"] for k in members}), "coverage": cov,
                    "mixed_coverage": len(cov) > 1, "shared_bullets": bl, "shared_skills": sk,
                    "jds": sorted({j for k in members for j in jds[k["term"]]}),
                    "esco": sorted({k["esco"] for k in members if k["esco"] and not k["esco"].startswith("—")}),
                    "links": [e for k in members for e in edges.get(k["term"], [])]})
    out.sort(key=lambda g: (-g["size"], g["terms"][0].lower()))
    return {"groups": out, "n_terms": len(kws), "n_groups": len(out),
            "singletons": sum(g["size"] == 1 for g in out), "min_link": min_link}


def tokens_tail(term: str) -> str:
    toks = [t for t in re.findall(r"[a-z0-9+#/]+", term.lower()) if t not in STOP]
    return toks[-1] if toks else term.lower()


def print_bootstrap(r: dict[str, Any]) -> None:
    print(f"{r['n_terms']} keywords → {r['n_groups']} groups ({r['singletons']} singletons) · min_link {r['min_link']}")
    for g in r["groups"]:
        if g["size"] == 1:
            continue
        flag = " ⚠ mixed coverage" if g["mixed_coverage"] else ""
        print(f"\n[{g['proposed_code']}] {g['size']} terms · {' / '.join(g['coverage'])}{flag}")
        print("  " + " · ".join(g["terms"]))
        if g["shared_bullets"] or g["shared_skills"]:
            print("  evidence: " + ", ".join(g["shared_bullets"] + g["shared_skills"]))
        if g["jds"]:
            print("  jds: " + ", ".join(g["jds"]))
    single = [g["terms"][0] for g in r["groups"] if g["size"] == 1]
    if single:
        print(f"\nsingletons ({len(single)}): " + " · ".join(single))


# ---- resolve: term → concept (library_db_spec_v3.md §3.3) -------------------------------------
TOKEN_SET_MIN = 2 / 3      # spec "≥ 0.67" = two of three tokens shared
TRIGRAM_MIN = 0.6
TYPE_W = {"required_skills": 3, "preferred_skills": 2, "keywords": 1}
TYPE_TAG = {"required_skills": "req", "preferred_skills": "pref", "keywords": "kw"}
STRENGTH = {"direct": 1.0, "implied": 0.5}
RULES = {1: "exact alias", 2: "normalised", 3: "token-set", 4: "trigram"}
LEAD_PROPOSAL_RE = re.compile(r"lead proposal: '([^']+)'(?: for ([\w/]+))?")


def norm_term(s: str) -> str:
    """Rule 2: lower, `&` → and, plural `s` stripped per word, `-` `/` and spaces folded away."""
    toks = [t for t in re.split(r"[-/\s]+", s.lower().replace("&", " and ")) if t]
    return "".join(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in toks)


def alias_index(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every alias + canonical name of every concept, with its rule-2..4 keys precomputed."""
    rows = [{"code": r[0], "alias": r[1]} for r in con.execute("SELECT concept_code, alias FROM concept_aliases")]
    have = {(r["code"], r["alias"].casefold()) for r in rows}
    rows += [{"code": r[0], "alias": r[1]} for r in con.execute("SELECT code, canonical FROM concepts WHERE canonical != ''")
             if (r[0], r[1].casefold()) not in have]
    for r in rows:
        r.update(norm=norm_term(r["alias"]), tok=tokens(r["alias"]), tri=trigrams(r["alias"]))
    return rows


def resolve_term(term: str, aliases: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic cascade, first hit wins: 1 exact alias · 2 normalised · 3 token-set · 4 trigram.
    Two concepts tied on a rule → not confident (NEW with candidates)."""
    t = term.strip()
    out = {"term": term, "code": None, "rule": None, "alias": None, "score": 0.0, "cands": []}
    hit = next((a for a in aliases if a["alias"].casefold() == t.casefold()), None)
    if hit:
        return {**out, "code": hit["code"], "rule": 1, "alias": hit["alias"], "score": 1.0}
    hits = [a for a in aliases if a["norm"] == norm_term(t)]
    if hits and len({a["code"] for a in hits}) == 1:
        return {**out, "code": hits[0]["code"], "rule": 2, "alias": hits[0]["alias"], "score": 1.0}
    for rule, key, need in ((3, "tok", TOKEN_SET_MIN), (4, "tri", TRIGRAM_MIN)):
        q = tokens(t) if key == "tok" else trigrams(t)
        best: dict[str, tuple[float, str]] = {}
        for a in aliases:
            s = jaccard(q, a[key]) if q and a[key] else 0.0
            if s >= need - 1e-9 and s > best.get(a["code"], (0.0, ""))[0]:
                best[a["code"]] = (s, a["alias"])
        ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))
        if ranked and (len(ranked) == 1 or ranked[1][1][0] < ranked[0][1][0]):
            code, (s, al) = ranked[0]
            return {**out, "code": code, "rule": rule, "alias": al, "score": round(s, 2)}
        if ranked:
            out["cands"] = [(c, round(s, 2), al) for c, (s, al) in ranked[:3]]
            return out
    q = trigrams(t)
    best = {}
    for a in aliases:
        s = jaccard(q, a["tri"])
        if s > best.get(a["code"], (0.0, ""))[0]:
            best[a["code"]] = (s, a["alias"])
    out["cands"] = [(c, round(s, 2), al) for c, (s, al) in sorted(best.items(), key=lambda kv: -kv[1][0])[:3] if s > 0.2]
    return out


def rule_label(r: dict[str, Any]) -> str:
    if r["code"] is None:
        return "NEW"
    return f"rule {r['rule']} {RULES[r['rule']]}" + (f" {r['score']:.2f}" if r["rule"] > 2 else "") + f" ← {r['alias']!r}"


def print_resolve(con: sqlite3.Connection, term: str) -> None:
    r = resolve_term(term, alias_index(con))
    if r["code"] is None:
        print(f"{term} → NEW" + (" · ambiguous: " if r["cands"] and r["cands"][0][1] >= TRIGRAM_MIN else " · nearest: ")
              + (", ".join(f"{c} {s:.2f} ({al})" for c, s, al in r["cands"]) or "-")
              + " → set-concept \"<term>\" <CODE> or add a concept to the seed")
        return
    c = con.execute("SELECT * FROM concepts WHERE code=?", (r["code"],)).fetchone()
    print(f"{term} → {c['code']} ({c['name']}) · claim: {c['claim']} · {rule_label(r)}")


# ---- concept-aware scoring (§3.4) ---------------------------------------------------------------
def excluded_concepts(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT code FROM concepts WHERE notes LIKE '%exclude from scoring%'")}


def lead_concepts(lead: str, aliases: list[dict[str, Any]], excluded: set[str]) -> list[str]:
    """Concepts a lead names (alias or canonical, NOCASE whole word), longest alias first."""
    hits = sorted(((len(a["alias"]), a["code"]) for a in aliases
                   if a["code"] not in excluded and has_term(a["alias"], lead)), reverse=True)
    return list(dict.fromkeys(c for _, c in hits))


def assign_lead_concepts(con: sqlite3.Connection) -> int:
    """bullet_leads.concept_code = the concept the lead names (longest matching alias)."""
    aliases, excluded = alias_index(con), excluded_concepts(con)
    n = 0
    with con:
        for r in con.execute("SELECT rowid, lead FROM bullet_leads").fetchall():
            cs = lead_concepts(r["lead"], aliases, excluded)
            con.execute("UPDATE bullet_leads SET concept_code=? WHERE rowid=?", (cs[0] if cs else None, r["rowid"]))
            n += bool(cs)
    return n


def concept_index(con: sqlite3.Connection) -> dict[str, Any]:
    """Per-bullet concept evidence (one relation hop at half strength), concept idf, BM25 docs."""
    excluded = excluded_concepts(con)
    aliases = alias_index(con)
    concepts = {r["code"]: dict(r) for r in con.execute("SELECT * FROM concepts")}
    bullets = {r["code"]: r for r in con.execute("SELECT rowid, * FROM bullets")}
    ev: dict[str, dict[str, float]] = {b: {} for b in bullets}
    for e in con.execute("SELECT * FROM concept_evidence WHERE kind IN ('bullet', 'lead')"):
        if e["code"] in ev and e["concept_code"] not in excluded:
            s = STRENGTH.get(e["strength"], 0.5)
            ev[e["code"]][e["concept_code"]] = max(ev[e["code"]].get(e["concept_code"], 0.0), s)
    # part_of / is_a: evidence for the part implies the whole; implemented_by: the tool implies the practice
    hops = [(r["src"], r["dst"]) if r["rel"] in ("part_of", "is_a") else (r["dst"], r["src"])
            for r in con.execute("SELECT * FROM concept_relations")]
    for cs in ev.values():
        for child, parent in hops:
            if child in cs and parent not in excluded and cs.get(parent, 0.0) < cs[child] * 0.5:
                cs[parent] = cs[child] * 0.5
    n_b = len(bullets)
    df = {c: sum(c in cs for cs in ev.values()) for c in concepts}
    idf = {c: math.log(n_b / n) if n else 0.0 for c, n in df.items()}
    names: dict[str, list[str]] = {}
    for a in aliases:
        names.setdefault(a["code"], []).append(a["alias"])
    leads: dict[str, list[sqlite3.Row]] = {}
    for r in con.execute("SELECT * FROM bullet_leads ORDER BY bullet_code, ord"):
        leads.setdefault(r["bullet_code"], []).append(r)
    docs = {}
    for code, b in bullets.items():
        text = " ".join([b["body"], b["keywords_carried"]] + [r["lead"] for r in leads.get(code, [])]
                        + [concepts[c]["name"] + " " + " ".join(names.get(c, [])) for c in ev[code]])
        docs[code] = tok_list(text)
    tdf: dict[str, int] = {}
    for d in docs.values():
        for t in set(d):
            tdf[t] = tdf.get(t, 0) + 1
    return {"excluded": excluded, "aliases": aliases, "concepts": concepts, "bullets": bullets, "ev": ev,
            "idf": idf, "docs": docs, "tdf": tdf, "avgdl": sum(map(len, docs.values())) / max(len(docs), 1),
            "leads": leads}


def bm25(query: set[str], doc: list[str], tdf: dict[str, int], n_docs: int, avgdl: float,
         k1: float = 1.2, b: float = 0.75) -> float:
    tf: dict[str, int] = {}
    for t in doc:
        tf[t] = tf.get(t, 0) + 1
    s = 0.0
    for t in query:
        f = tf.get(t)
        if f:
            idf = math.log((n_docs - tdf[t] + 0.5) / (tdf[t] + 0.5) + 1)
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(doc) / avgdl))
    return s


def jd_concepts(job: dict[str, Any], idx: dict[str, Any]) -> dict[str, Any]:
    """Resolve every JD term; concept weight = its strongest mention (required 3 / preferred 2 / keyword 1)."""
    terms, by_code, seen = [], {}, set()
    for field, _ in KW_FIELDS:
        for t in job.get(field) or []:
            if t.casefold() in seen:
                continue
            seen.add(t.casefold())
            r = resolve_term(t, idx["aliases"])
            r.update(field=field, tag=TYPE_TAG[field], w=TYPE_W[field])
            terms.append(r)
            if r["code"] and r["code"] not in idx["excluded"]:
                c = by_code.setdefault(r["code"], {"code": r["code"], "w": 0, "terms": [],
                                                   "claim": idx["concepts"][r["code"]]["claim"]})
                c["w"] = max(c["w"], r["w"])
                c["terms"].append(r)
    return {"terms": terms, "concepts": by_code}


def score_bullets(con: sqlite3.Connection, job: dict[str, Any], idx: dict[str, Any] | None = None) -> dict[str, Any]:
    """score = Σ w_type × strength × idf(concept) + BM25(body + leads + keywords + concept aliases, JD terms)."""
    idx = idx or concept_index(con)
    jc = jd_concepts(job, idx)
    skip = {c for c, x in idx["concepts"].items() if x["claim"] == "do not claim"} | idx["excluded"]
    query = {t for r in jc["terms"] if r["code"] not in skip for t in tokens(r["term"])}
    lit_terms = [r["term"] for r in jc["terms"]]
    out = {}
    for code, b in idx["bullets"].items():
        hits, cscore = [], 0.0
        for c, x in jc["concepts"].items():
            s = idx["ev"][code].get(c)
            if s:
                cscore += x["w"] * s * idx["idf"][c]
                hits.append((c, x["terms"][0]["term"], s))
        lex = bm25(query, idx["docs"][code], idx["tdf"], len(idx["docs"]), idx["avgdl"])
        out[code] = {"score": round(cscore + lex, 2), "concept": round(cscore, 2), "bm25": round(lex, 2),
                     "hits": sorted(hits, key=lambda h: -jc["concepts"][h[0]]["w"] * h[2] * idx["idf"][h[0]]),
                     "literal": terms_in(lit_terms, f"{b['keywords_carried']} {b['body']}")}
    return {"jd": jc, "bullets": out, "idx": idx}


def lead_jd_score(lead: str, jc: dict[str, Any], idx: dict[str, Any]) -> tuple[float, list[str]]:
    """Weight of the JD concepts a lead names: JD alias verbatim 1.0, another alias of the concept 0.5."""
    named = set(lead_concepts(lead, idx["aliases"], idx["excluded"]))
    s, why = 0.0, []
    for c, x in jc["concepts"].items():
        verb = next((r["term"] for r in x["terms"] if has_term(r["term"], lead)), None)
        if verb or c in named:
            s += x["w"] * (1.0 if verb else 0.5)
            why.append(f"{c}:{verb}" if verb else c)
    return s, why


def print_concept_match(sc: dict[str, Any], n: int = 12) -> None:
    jc = sc["jd"]
    print(f"\nconcepts the JD names ({len(jc['concepts'])}; w = required 3 / preferred 2 / keyword 1, idf over "
          f"{len(sc['idx']['bullets'])} bullets):")
    for c, x in sorted(jc["concepts"].items(), key=lambda kv: (-kv[1]["w"], kv[0])):
        print(f"  {c} w{x['w']} idf {sc['idx']['idf'][c]:.2f} · {x['claim']} · " + " · ".join(r["term"] for r in x["terms"]))
    print(f"bullets by concept score (score = concept + BM25 · concepts hit ← JD phrase · literal):")
    ranked = sorted(sc["bullets"].items(), key=lambda kv: (-kv[1]["score"], sc["idx"]["bullets"][kv[0]]["rowid"]))
    for code, r in ranked[:n]:
        st = sc["idx"]["bullets"][code]["status"]
        print(f"  {code:6} {r['score']:6.2f} = {r['concept']:5.2f} + {r['bm25']:5.2f} [{st}] · "
              + (", ".join(f"{c}{'' if s == 1 else '?'} ← {p}" for c, p, s in r["hits"][:5]) or "-")
              + (f" · literal: {', '.join(r['literal'][:6])}" if r["literal"] else ""))


# ---- offline ATS keyword match (selftest §6 item 4; mirrors ats_check.py, no external call) -----
def ats_text(lines: list[str]) -> str:
    """The text Resume-Matcher scans: Summary, work entries, education, Skills items (ats_check.resume_from_md)."""
    def strip(s: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"\1", s).strip()
    parts = [strip(lines[4])]
    i = next(k for k, l in enumerate(lines) if l.startswith("**PROFESSIONAL EXPERIENCE**")) + 1
    while i < len(lines) and lines[i]:
        l = lines[i]
        if "\t" in l and l.startswith("**"):
            parts += [strip(x) for x in l.split("\t")[0].split("  |  ")]
        else:
            parts.append(strip(l[2:] if l.startswith("- ") else l))
        i += 1
    j = next(k for k, l in enumerate(lines) if l.startswith("**EDUCATION**")) + 1
    while j + 1 < len(lines) and lines[j].startswith("**") and "  ·  " in lines[j]:
        parts += [strip(lines[j].split("  ·  ")[0]), lines[j + 1].strip()]
        j += 2
    s0 = next((k for k, l in enumerate(lines) if l.startswith("**TECHNICAL SKILLS**")), -1)
    s1 = next((k for k, l in enumerate(lines) if l.startswith("**PROFESSIONAL EXPERIENCE**")), len(lines))
    for l in lines[s0 + 1:s1] if s0 >= 0 else lines:   # mirrors ats_check.resume_from_md: single-item rows count
        m = re.match(r"^\*\*([^*]+):\*\*\s*(.+)$", l)
        if m:
            parts += [s.strip() for s in m.group(2).split("·") if s.strip()]
    return " ".join(parts)


def cv_with_picks(con: sqlite3.Connection, cv: Path, picks: list[tuple[str, str]]) -> list[str]:
    """03_cv.md with its Skills rows and Delta block replaced by the rendered picks (Summary etc. kept)."""
    lines = cv.read_text(encoding="utf-8-sig").splitlines()
    skills_f, delta_f, _ = cv_blocks(cv)
    skills_r, delta_r = render_lines(con, picks)
    for have, want in sorted(((delta_f, delta_r), (skills_f, skills_r)), key=lambda hw: -hw[0][0][0]):
        lines[have[0][0] - 1:have[-1][0]] = want
    return lines


def offline_ats(con: sqlite3.Connection, jd: str, picks: list[tuple[str, str]]) -> tuple[int, int]:
    """(found, n) over the JD's ingested ATS terms, for the recorded 03_cv.md with `picks` swapped in."""
    terms = [r[0] for r in con.execute("SELECT term FROM ats_ingest WHERE jd=? ORDER BY ord", (jd,))]
    if not terms:
        raise PickError(f"no ingest-ats data for {jd!r}")
    with contextlib.redirect_stderr(io.StringIO()):   # "bullet X placed under card Y" notes are not news here
        text = ats_text(cv_with_picks(con, recorded_dir(con, jd) / "03_cv.md", picks)).lower()
    return sum(has_term(t.strip(), text) for t in terms), len(terms)


# ---- align (§3.5) ---------------------------------------------------------------------------------
def lead_proposal(c: dict[str, Any], bullet: sqlite3.Row, jd_alias: str, idx: dict[str, Any]) -> str:
    """Seed note `lead proposal: '…' for B1/B2` wins; else "<JD alias as duty phrase> (<tool>)"."""
    for m in LEAD_PROPOSAL_RE.finditer(c["notes"] or ""):
        if not m.group(2) or bullet["code"] in m.group(2).split("/"):
            return m.group(1)
    text = f"{bullet['body']} {bullet['keywords_carried']}"
    tool = None
    for name in [e["code"] for e in idx["evidence"] if e["kind"] == "skill" and e["concept_code"] == c["code"]]:
        short = name.split(" (")[0].split(" · ")[0]
        if has_term(short, text):
            tool = short
            break
    if tool is None:   # a tool concept related to this one, named in the body
        for r in idx["tools"].get(c["code"], []):
            if has_term(r, text):
                tool = r
                break
    phrase = jd_alias[0].upper() + jd_alias[1:]
    return f"{phrase} ({tool})" if tool and not has_term(tool, phrase) else phrase


def align(con: sqlite3.Connection, app_dir: Path, picks: list[tuple[str, str]]) -> dict[str, Any]:
    """Per picked bullet × JD-named concept: keep / use lead_ord n / NEW LEAD / spelling / summary (advisory).
    A lead swap is recommended only when it puts more JD terms on the CV than the current lead (never fewer)."""
    job = json.loads((app_dir / "07_job_keywords.json").read_text(encoding="utf-8"))
    sc = score_bullets(con, job)
    idx, jc = sc["idx"], sc["jd"]
    idx["evidence"] = [dict(r) for r in con.execute("SELECT * FROM concept_evidence")]
    idx["tools"] = {}
    for r in con.execute("SELECT r.src, r.dst, s.canonical sc, s.kind sk, d.canonical dc, d.kind dk "
                         "FROM concept_relations r JOIN concepts s ON s.code=r.src JOIN concepts d ON d.code=r.dst"):
        if r["dk"] == "tool":
            idx["tools"].setdefault(r["src"], []).append(r["dc"])
        if r["sk"] == "tool":
            idx["tools"].setdefault(r["dst"], []).append(r["sc"])
    terms = [r["term"] for r in jc["terms"]]
    # the "never fewer" guard must count the same terms the ATS run counted (ats_ingest can differ from
    # 07_job_keywords.json after normalisation), else a swap can drop a found term unnoticed
    for a in con.execute("SELECT jd, dir FROM applications"):
        if (ROOT / a["dir"]).resolve() == app_dir.resolve():
            seen = {t.casefold() for t in terms}
            terms += [r[0] for r in con.execute("SELECT DISTINCT term FROM ats_ingest WHERE jd=?", (a["jd"],))
                      if r[0].casefold() not in seen]
            break
    weight = {r["term"]: r["w"] for r in jc["terms"]}
    res = resolve(con, picks)
    lines = [r["line"].replace("**", "") for r in res]
    bullets = [r for r in res if r["kind"] == "bullet"]

    def found(text: str) -> set[str]:
        return {t for t in terms if has_term(t, text)}

    def ranked_concepts(code: str) -> list[str]:
        """The bullet's JD concepts, strongest first (w × strength × idf) — the lead should name the first."""
        return sorted((c for c in idx["ev"][code] if c in jc["concepts"]),
                      key=lambda c: (-jc["concepts"][c]["w"] * idx["ev"][code][c] * idx["idf"][c], c))

    def lead_rank(lead: str, order: list[str]) -> int:
        named = set(lead_concepts(lead, idx["aliases"], idx["excluded"]))
        named |= {c for c in order if any(has_term(t["term"], lead) for t in jc["concepts"][c]["terms"])}
        return next((i for i, c in enumerate(order) if c in named), len(order))

    # 1) lead ords, greedy in pick order: the variant naming the bullet's strongest JD concept, then the
    #    one putting the most JD terms on the CV; a swap never lowers the CV's JD-term count
    # the rest of the CV (headline, Summary, Mirle, Education…) counts too: a swap that trades
    # 'delivery pipelines' for 'automation' is a net loss when the headline already says automation
    extra = ""
    cv_p = app_dir / "03_cv.md"
    if cv_p.exists():
        sk, dl, eol = cv_blocks(cv_p)
        block = {n for n, _ in sk} | {n for n, _ in dl}
        extra = " ".join(l.replace("**", "") for n, l in enumerate(cv_p.read_bytes().decode("utf-8").split(eol), 1)
                         if n not in block and l)   # same split as cv_blocks (read_text would fold CRLF)
    use: dict[str, int] = {}
    for r in bullets:
        i = res.index(r)
        b, cur = idx["bullets"][r["code"]], r["variant_ord"]
        order = ranked_concepts(r["code"])
        rest = found(" ".join(lines[:i] + lines[i + 1:]) + " " + extra)

        def key(ld: sqlite3.Row) -> tuple:
            f = rest | found(f"{ld['lead']} — {b['body']}")
            return (-lead_rank(ld["lead"], order), len(f), sum(weight[t] for t in f),
                    lead_jd_score(ld["lead"], jc, idx)[0], ld["ord"] == cur, -ld["ord"])
        fitting = [ld for ld in idx["leads"][r["code"]]
                   if not fit_line("bullet", f"{r['code']}:{ld['ord']}", f"- **{ld['lead']} —** {b['body']}")["over"]]
        best = max(fitting, key=key) if fitting else idx["leads"][r["code"]][cur]
        kb, kc = key(best), key(idx["leads"][r["code"]][cur])
        use[r["code"]] = best["ord"] if kb[:3] > kc[:3] and kb[1] >= kc[1] else cur
        lines[i] = f"{idx['leads'][r['code']][use[r['code']]]['lead']} — {b['body']}"
    rendered = " ".join(lines) + " " + extra
    on_cv = found(rendered)
    new_picks = [("bullet", f"{_ords(v, 1)[0]}:{use[_ords(v, 1)[0]]}") if k == "bullet" else (k, v) for k, v in picks]

    # 2) per bullet × concept
    best_for: dict[str, str] = {}
    for r in bullets:
        for c, s in idx["ev"][r["code"]].items():
            if c in jc["concepts"] and s > idx["ev"].get(best_for.get(c, ""), {}).get(c, 0.0):
                best_for[c] = r["code"]
    spelling = {r["term"].casefold(): r["spelling"] for r in con.execute("SELECT term, spelling FROM keywords WHERE spelling != ''")}
    rows, proposals, claimed, n_ok = [], [], set(), 0
    for r in bullets:
        code, cur = r["code"], r["variant_ord"]
        b, lead = idx["bullets"][code], idx["leads"][code][use[code]]["lead"]
        named = set(lead_concepts(lead, idx["aliases"], idx["excluded"]))
        lead_w = max((jc["concepts"][c]["w"] for c in named if c in jc["concepts"]), default=0)
        mine = sorted({c for c in idx["ev"][code] if c in jc["concepts"]} | (named & set(jc["concepts"])),
                      key=lambda c: (-jc["concepts"][c]["w"] * idx["ev"][code].get(c, 1.0) * idx["idf"][c], c))
        def says(c: str) -> bool:
            return c in named or any(t["term"] in on_cv for t in jc["concepts"][c]["terms"])
        eligible = [c for c in mine if not says(c) and idx["ev"][code].get(c) == 1.0 and best_for.get(c) == code
                    and jc["concepts"][c]["w"] > lead_w and c not in claimed and jc["concepts"][c]["claim"] == "covered"
                    and idx["concepts"][c]["kind"] in ("practice", "duty", "domain")]
        # one NEW LEAD per bullet: a lead proposal the seed already notes for this bullet wins
        noted = [c for c in eligible if any(not m.group(2) or code in m.group(2).split("/")
                                            for m in LEAD_PROPOSAL_RE.finditer(idx["concepts"][c]["notes"] or ""))]
        nl = (noted or eligible or [None])[0]
        for c in mine:
            x = jc["concepts"][c]
            jd_alias = x["terms"][0]["term"]
            in_lead = next((t["term"] for t in x["terms"] if has_term(t["term"], lead)), None)
            in_cv = next((t["term"] for t in x["terms"] if t["term"] in on_cv), None)
            # body spelling only: the keyword's recorded spelling, or a hyphen/plural variant in this body
            near = next((spelling.get(t["term"].casefold()) or t["term"] for t in x["terms"]
                         if spelling.get(t["term"].casefold()) or norm_term(t["term"]) in norm_term(b["body"])), None)
            tools = [t for t in idx["tools"].get(c, []) if has_term(t, b["body"])]
            current = (f"lead: {lead}" if in_lead or c in named else
                       f"body: {in_cv}" if in_cv and has_term(in_cv, b["body"]) else
                       "tools: " + (", ".join(tools) or "-"))
            if in_lead or c in named:
                action = f"keep lead_ord {cur}" if use[code] == cur else f"use lead_ord {use[code]}"
            elif in_cv:
                n_ok += 1
                continue
            elif near:
                action = f"spelling: write {near!r}"
            elif c == nl:
                prop = lead_proposal(idx["concepts"][c], b, jd_alias, idx)
                over = fit_line("bullet", f"{code}:new", f"- **{prop} —** {b['body']}")["over"]
                action = f"NEW LEAD \"{prop}\" → library mode" + (" (over the line limit: shorten)" if over else "")
                proposals.append({"bullet": code, "concept": c, "lead": prop, "over": over})
                claimed.add(c)
            elif x["w"] == 3 and x["claim"] == "covered" and c not in claimed:
                action = "summary"
                claimed.add(c)
            else:
                continue
            rows.append({"line": code, "concept": c, "w": x["w"], "jd": in_lead or jd_alias, "current": current,
                         "action": action})
    # 3) Skills rows: a spelling variant of a picked item that says the JD term
    for r in res:
        if r["kind"] != "skill_row":
            continue
        for it in con.execute("SELECT * FROM skill_row_items WHERE row_label=?", (r["code"],)):
            if not has_term(it["item"], r["line"]):
                continue
            for sp in json.loads(it["spellings"]):
                t = next((t for t in terms if has_term(t, sp) and t not in on_cv), None)
                if t:
                    tc = next(x["code"] for x in jc["terms"] if x["term"] == t) or "-"
                    rows.append({"line": f"skills: {r['lead']}", "concept": tc,
                                 "w": jc["concepts"].get(tc, {}).get("w", 0), "jd": t, "current": it["item"],
                                 "action": f"spelling: item ~ {sp!r}"})
    # 4) Summary hints: required, claimable concepts the picks prove without the JD's noun
    for c, x in sorted(jc["concepts"].items()):
        if (x["w"] == 3 and x["claim"] == "covered" and c not in claimed
                and not any(t["term"] in on_cv for t in x["terms"]) and any(c in idx["ev"][r["code"]] for r in bullets)):
            rows.append({"line": "Summary", "concept": c, "w": 3, "jd": x["terms"][0]["term"], "current": "-",
                         "action": "summary"})
    return {"rows": rows, "picks": new_picks, "proposals": proposals, "changed": new_picks != picks, "n_ok": n_ok,
            "on_cv": len(on_cv), "n_terms": len(terms)}


def print_align(r: dict[str, Any]) -> None:
    print("| line | concept | JD phrase | current wording | action |")
    print("|---|---|---|---|---|")
    for x in r["rows"]:
        print(f"| {x['line']} | {x['concept']} (w{x['w']}) | {x['jd']} | {x['current'][:60]} | {x['action']} |")
    n_new = len(r["proposals"])
    n_use = sum(x["action"].startswith("use lead_ord") for x in r["rows"])
    print(f"\n{r['n_ok']} concept mentions already in the JD's wording elsewhere on the CV · JD terms in the picked "
          f"lines {r['on_cv']}/{r['n_terms']} · {n_use} lead swap row(s) · {n_new} NEW LEAD proposal(s)"
          + (" → library mode: append to the bullet's `leads` in the seed, `init`, re-run align" if n_new else ""))
    print("picks with lead ords applied" + ("" if r["changed"] else " (unchanged)") + ":")
    print(picks_to_spec(r["picks"]))


# ---- gaps (§3.6) ----------------------------------------------------------------------------------
GAP_GROUPS = (("SoT-supported wording", "evidence implied → write a lead/bullet in library mode, cite CONF"),
              ("gap", "learn / omit"), ("do not claim", "never on the CV"))


def gaps(con: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any]:
    idx = concept_index(con)
    jc = jd_concepts(job, idx)
    kw = {r["term"].casefold(): r for r in con.execute("SELECT * FROM keywords")}
    groups: dict[str, list[dict[str, Any]]] = {g: [] for g, _ in GAP_GROUPS}
    member = []
    for c, x in sorted(jc["concepts"].items(), key=lambda kv: (-kv[1]["w"], kv[0])):
        ev = [f"{e['kind']} {e['code']} ({e['strength']})" for e in con.execute(
            "SELECT * FROM concept_evidence WHERE concept_code=? ORDER BY kind, strength, code", (c,))]
        item = {"code": c, "name": idx["concepts"][c]["name"], "w": x["w"], "notes": idx["concepts"][c]["notes"],
                "terms": [f"{r['term']} [{r['tag']}]" for r in x["terms"]], "evidence": ev}
        if x["claim"] in groups:
            groups[x["claim"]].append(item)
        elif x["claim"] == "covered":   # concept claimable, but this JD's term is a member-level gap
            bad = [f"{r['term']} [{kw[r['term'].casefold()]['coverage']}]" for r in x["terms"]
                   if r["term"].casefold() in kw and kw[r["term"].casefold()]["coverage"] in ("gap", "do not claim")]
            if bad:
                member.append({**item, "terms": bad})
    new = [r["term"] for r in jc["terms"] if r["code"] is None]
    return {"groups": groups, "member": member, "new": new}


def print_gaps(g: dict[str, Any]) -> None:
    for name, hint in GAP_GROUPS:
        items = g["groups"][name]
        print(f"## {name} ({len(items)}) — {hint}")
        for i in items:
            print(f"- {i['code']} (w{i['w']}) {i['name']} · JD: " + " · ".join(i["terms"]))
            print(f"    evidence: {', '.join(i['evidence']) or 'none'}" + (f" · notes: {i['notes']}" if i["notes"] else ""))
    if g["member"]:
        print(f"## covered concept, term not claimable ({len(g['member'])})")
        for i in g["member"]:
            print(f"- {i['code']} {i['name']} · " + " · ".join(i["terms"]))
    if g["new"]:
        print(f"## unresolved ({len(g['new'])}) — `set-concept \"<term>\" <CODE>`: " + " · ".join(g["new"]))


# ---- set-concept (§3.7) ---------------------------------------------------------------------------
def apply_concept_overrides(con: sqlite3.Connection) -> None:
    """Replay set-concept: manual alias + keywords.concept_code + coverage from the claim (a set-coverage
    override still wins; a term the ATS found on a CV stays covered)."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='concept_overrides'").fetchone():
        return
    with con:
        for o in con.execute("SELECT * FROM concept_overrides").fetchall():
            claim = con.execute("SELECT claim FROM concepts WHERE code=?", (o["concept_code"],)).fetchone()
            if not claim:
                continue      # concept removed from the seed: skip, do not fail init
            con.execute("DELETE FROM concept_aliases WHERE alias=? COLLATE NOCASE", (o["term"],))
            con.execute("INSERT INTO concept_aliases VALUES(?,?,?,?)", (o["concept_code"], o["term"], "manual", ""))
            con.execute("UPDATE keywords SET concept_code=? WHERE term=? COLLATE NOCASE", (o["concept_code"], o["term"]))
            found = con.execute("SELECT 1 FROM ats_ingest WHERE term=? COLLATE NOCASE AND found=1", (o["term"],)).fetchone()
            if not found and not con.execute("SELECT 1 FROM coverage_overrides WHERE term=? COLLATE NOCASE",
                                              (o["term"],)).fetchone():
                con.execute("UPDATE keywords SET coverage=? WHERE term=? COLLATE NOCASE", (claim[0], o["term"]))


def set_concept(con: sqlite3.Connection, term: str, code: str) -> str:
    c = con.execute("SELECT * FROM concepts WHERE code=?", (code,)).fetchone()
    if not c:
        raise PickError(f"no concept {code!r} (see `concepts`; a truly new concept goes into the seed)")
    k = con.execute("SELECT * FROM keywords WHERE term=? COLLATE NOCASE", (term,)).fetchone()
    before = f"{k['concept_code'] or '-'} / {k['coverage']}" if k else "not in keyword map"
    with con:
        con.execute("INSERT OR REPLACE INTO concept_overrides VALUES(?,?,?)",
                    (k["term"] if k else term, code, dt.date.today().isoformat()))
    apply_concept_overrides(con)
    k2 = con.execute("SELECT * FROM keywords WHERE term=? COLLATE NOCASE", (term,)).fetchone()
    return (f"{term}: {before} → {code} / {k2['coverage'] if k2 else '(alias only)'}"
            + (" · set-coverage override kept" if k2 and k2["coverage"] != c["claim"] else ""))


def print_cards(con: sqlite3.Connection, archetype: str | None) -> None:
    for p in con.execute("SELECT * FROM projects ORDER BY rowid"):
        if archetype and archetype.casefold() not in (p["best_for"] + " " + p["domains"]).casefold():
            continue
        t = con.execute("SELECT title FROM project_titles WHERE project_code=? AND ord=0", (p["code"],)).fetchone()
        print(f"{p['code']} · {p['name']} · {p['status']} · title: {t[0] if t else '-'} · "
              f"metrics: {p['metrics']} · best for: {p['best_for']}")


def print_card(con: sqlite3.Connection, code: str) -> None:
    p = con.execute("SELECT * FROM projects WHERE code=?", (code,)).fetchone()
    if not p:
        raise PickError(f"unknown card {code!r}")
    print(f"{p['code']} — {p['name']} ({p['employer']}, {p['period']}, {p['status']}) [{p['domains']}]")
    for r in con.execute("SELECT * FROM project_titles WHERE project_code=? ORDER BY ord", (code,)):
        print(f"  title {r['ord']}: {r['title']}")
    for r in con.execute("SELECT * FROM project_contexts WHERE project_code=? ORDER BY ord", (code,)):
        print(f"  context {r['ord']}: {r['text']}  ({r['conf']}; {r['note']})")
    print(f"  metrics: {p['metrics']}\n  best for: {p['best_for']}\n  constraints: {p['constraints']}")
    for b in con.execute("SELECT * FROM bullets WHERE project_code=? ORDER BY rowid", (code,)):
        lead = con.execute("SELECT lead FROM bullet_leads WHERE bullet_code=? AND ord=0", (b["code"],)).fetchone()[0]
        print(f"  - {b['code']} [{b['status']}] · {lead} · {b['chars']} chars · {b['capabilities']}"
              + (f" · {b['constraints']}" if b["constraints"] else ""))


def print_bullet(con: sqlite3.Connection, code: str, leads_only: bool = False) -> None:
    b = con.execute("SELECT * FROM bullets WHERE code=?", (code,)).fetchone()
    if not b:
        raise PickError(f"unknown bullet {code!r}")
    leads = con.execute("SELECT * FROM bullet_leads WHERE bullet_code=? ORDER BY ord", (code,)).fetchall()
    if leads_only:
        for r in leads:
            print(f"{r['ord']}: {r['lead']}  ({bullet_chars(r['lead'], b['body'])} chars)")
        return
    kws = [r[0] for r in con.execute("SELECT term FROM keyword_bullets WHERE bullet_code=?", (code,))]
    used = [r[0] for r in con.execute("SELECT DISTINCT jd FROM application_picks WHERE kind='bullet' AND code=?", (code,))]
    print(f"{b['code']} · card {b['project_code']} · {b['status']} · {b['chars']} chars")
    for r in leads:
        print(f"  lead {r['ord']}: {r['lead']}")
    for label, val in (("body", b["body"]), ("capabilities", b["capabilities"]),
                       ("keywords carried", b["keywords_carried"]), ("metric", b["metric"]),
                       ("CONF", b["conf"]), ("constraints", b["constraints"]), ("evidence", b["evidence"]),
                       ("JD keywords", ", ".join(kws)), ("used in", ", ".join(used)), ("notion", b["notion_id"])):
        if val:
            print(f"  {label}: {val}")


def print_skills(con: sqlite3.Connection, category: str | None, rows: bool) -> None:
    if rows:
        for r in con.execute("SELECT * FROM skill_rows ORDER BY ord"):
            items = con.execute("SELECT * FROM skill_row_items WHERE row_label=? ORDER BY ord", (r["label"],)).fetchall()
            note = f"   (label variants: 1={r['variants']})" if r["variants"] else ""
            print(f"**{r['label']}:** " + " · ".join(i["item"] for i in items) + note)
            print("    ords: " + " · ".join(
                f"{i['ord']}={i['item']}" + "".join(f" ~{n}={s}" for n, s in enumerate(json.loads(i["spellings"]), 1))
                for i in items))
        return
    q, params = "SELECT * FROM skills", ()
    if category:
        q, params = q + " WHERE category=?", (category,)
    for s in con.execute(q + " ORDER BY category, rowid", params):
        print(f"{s['category']} · {s['name']}" + ("" if s["on_cv"] else " · NOT ON CV")
              + (f" · variants: {s['variants']}" if s["variants"] else "")
              + (f" · ESCO: {s['esco']}" if s["esco"] else "") + f" · {s['conf']}")


def print_keywords(con: sqlite3.Connection, jd: str | None, coverage: str | None, term: str | None) -> None:
    q, params = "SELECT * FROM keywords WHERE 1=1", []
    if jd:
        q += " AND term IN (SELECT term FROM keyword_jds WHERE jd=?)"
        params.append(jd)
    if coverage:
        q += " AND coverage=?"
        params.append(coverage)
    if term:
        q += " AND term LIKE ?"
        params.append(f"%{term}%")
    for k in con.execute(q + " ORDER BY rowid", params):
        bl = ",".join(r[0] for r in con.execute("SELECT bullet_code FROM keyword_bullets WHERE term=?", (k["term"],)))
        sk = " | ".join(r[0] for r in con.execute("SELECT skill_name FROM keyword_skills WHERE term=?", (k["term"],)))
        print(f"{k['term']} · {k['type']} · {k['coverage']}"
              + (f" · spelling: {k['spelling']}" if k["spelling"] else "")
              + (f" · bullets {bl}" if bl else "") + (f" · skills {sk}" if sk else "")
              + (f" · {k['notes']}" if k["notes"] else ""))


# ---------------------------------------------------------------- export-md
def export_md(con: sqlite3.Connection) -> str:
    out = ["# Bullet library (generated from cv/profile/experience.sqlite — do not edit by hand)", "",
           "Regenerate with `library_db.py export-md`. Edit `cv/profile/library_seed.json` (or the DB) instead.",
           "Metrics and business value are set by the candidate; Notion links are evidence only.", ""]
    for kind, head in (("rationales", "Decision rationales (CONF-f7 — use these 'why' clauses)"),
                       ("facts", "Summary facts (the only numbers a Summary may use)")):
        out += [f"## {head}"] + [f"- {r[0]}" for r in con.execute(
            "SELECT text FROM meta_lines WHERE kind=? ORDER BY ord", (kind,))] + [""]

    out += ["## Skills items (by category)", "", "| Category | Item | Variants | ESCO | CONF | On CV |", "|---|---|---|---|---|---|"]
    for s in con.execute("SELECT * FROM skills ORDER BY category, rowid"):
        out.append(f"| {s['category']} | {s['name']} | {s['variants']} | {s['esco']} | {s['conf']} | "
                   f"{'yes' if s['on_cv'] else 'no'} |")
    out += ["", "Skills rows (ord = position for `skill_row=LABEL:VARIANT=ords`; `~n` = spelling variant):"]
    for r in con.execute("SELECT * FROM skill_rows ORDER BY ord"):
        items = con.execute("SELECT * FROM skill_row_items WHERE row_label=? ORDER BY ord", (r["label"],)).fetchall()
        out.append(f"- `**{r['label']}:** " + " · ".join(i["item"] for i in items) + "`"
                   + (f" — label variant: `{r['variants']}`" if r["variants"] else ""))
        spell = [f"{i['ord']}~{n} `{s}`" for i in items for n, s in enumerate(json.loads(i["spellings"]), 1)]
        if spell:
            out.append("  spellings: " + " · ".join(spell))

    out += ["", "## Project cards", "", "Scope lines:"]
    for s in con.execute("SELECT * FROM scope_lines ORDER BY rowid"):
        out.append(f"- **{s['code']}** `{s['text']}` ({s['conf']}; {s['note']})")
    legacy = []
    for p in con.execute("SELECT * FROM projects ORDER BY rowid"):
        out += ["", f"### Card {p['code']} — {p['name']} [{p['domains']}]",
                f"{p['employer']} · {p['period']} · {p['status']}"]
        titles = [r[0] for r in con.execute("SELECT title FROM project_titles WHERE project_code=? ORDER BY ord", (p["code"],))]
        if titles:
            out.append("Titles: " + " · ".join(f"{i}: `{t}`" for i, t in enumerate(titles)))
        for c in con.execute("SELECT * FROM project_contexts WHERE project_code=? ORDER BY ord", (p["code"],)):
            out.append(f"Context {c['ord']}: `{c['text']}` ({c['conf']}; {c['note']})")
        out += [f"Metrics: {p['metrics']}", f"Best for: {p['best_for']}", f"Constraints: {p['constraints']}"]
        if p["evidence"]:
            out.append(f"Evidence: {p['evidence']}")
        for b in con.execute("SELECT * FROM bullets WHERE project_code=? ORDER BY rowid", (p["code"],)):
            if b["status"] == "Legacy wording":
                legacy.append(b)
                continue
            out += _bullet_md(con, b)
    out += ["", "## Legacy bullets (still valid wording and facts — cards supersede them)"]
    for b in legacy:
        out += _bullet_md(con, b)

    out += ["", "## JD keyword map", "", "| Term | Type | Coverage | Bullets | Skills | Spelling | JDs | Notes |",
            "|---|---|---|---|---|---|---|---|"]
    for k in con.execute("SELECT * FROM keywords ORDER BY rowid"):
        bl = ", ".join(r[0] for r in con.execute("SELECT bullet_code FROM keyword_bullets WHERE term=?", (k["term"],)))
        sk = ", ".join(r[0] for r in con.execute("SELECT skill_name FROM keyword_skills WHERE term=?", (k["term"],)))
        jds = ", ".join(r[0] for r in con.execute("SELECT jd FROM keyword_jds WHERE term=?", (k["term"],)))
        out.append(f"| {k['term']} | {k['type']} | {k['coverage']} | {bl} | {sk} | {k['spelling']} | {jds} | {k['notes']} |")

    out += ["", "## Concepts", "", "Term → concept → evidence (library_db_spec_v3.md). `?` = implied evidence.", "",
            "| Code | Name | Kind | Claim | Aliases | Evidence | Relations | Notes |", "|---|---|---|---|---|---|---|---|"]
    for c in con.execute("SELECT * FROM concepts ORDER BY rowid"):
        al = " · ".join(r[0] for r in con.execute("SELECT alias FROM concept_aliases WHERE concept_code=? ORDER BY rowid",
                                                   (c["code"],)))
        ev = ", ".join(f"{r['kind']} {r['code']}{'' if r['strength'] == 'direct' else '?'}" for r in con.execute(
            "SELECT * FROM concept_evidence WHERE concept_code=? ORDER BY rowid", (c["code"],)))
        rel = ", ".join(f"{r[0]} {r[1]}" for r in con.execute("SELECT rel, dst FROM concept_relations WHERE src=?",
                                                               (c["code"],)))
        out.append(f"| {c['code']} | {c['name']} | {c['kind']} | {c['claim']} | {al} | {ev} | {rel} | {c['notes']} |")

    apps = con.execute("SELECT * FROM applications ORDER BY created, jd").fetchall()
    if apps:
        out += ["", "## Applications (recorded picks)"]
        for a in apps:
            out.append(f"- **{a['jd']}** — {a['company']} · {a['title']} · {a['created']} · `{a['dir']}`")
            out.append(f"  `{picks_to_spec(recorded_picks(con, a['jd']))}`")
    out += ["", "## User-data tables (counts only)"]
    for t in ("interview_qa", "outreach"):
        out.append(f"- {t}: {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]} rows")
    return "\n".join(out) + "\n"


def _bullet_md(con: sqlite3.Connection, b: sqlite3.Row) -> list[str]:
    leads = [r[0] for r in con.execute("SELECT lead FROM bullet_leads WHERE bullet_code=? ORDER BY ord", (b["code"],))]
    lines = [f"- **{b['code']}** [{b['status']}; {b['capabilities']}] leads: "
             + " · ".join(f"{i}: `{lead}`" for i, lead in enumerate(leads)),
             f"  `{b['body']}` ({b['conf']}; {b['chars']} chars)"]
    for label, key in (("Keywords carried", "keywords_carried"), ("Metric", "metric"),
                       ("Constraints", "constraints"), ("Evidence", "evidence")):
        if b[key]:
            lines.append(f"  {label}: {b[key]}")
    return lines


# ---------------------------------------------------------------- notion
def _rel(con: sqlite3.Connection, sql: str, *params: Any) -> str:
    return json.dumps([u for u in (notion_url(r[0]) for r in con.execute(sql, params)) if u])


def notion_payload(con: sqlite3.Connection, table: str, only_new: bool = False) -> list[dict[str, Any]]:
    out = []
    key = NOTION_KEYS[table]
    for r in con.execute(f"SELECT * FROM {table} ORDER BY rowid"):
        if only_new and r["notion_id"]:
            continue
        k = r[key]
        if table == "projects":
            titles = [x[0] for x in con.execute("SELECT title FROM project_titles WHERE project_code=? ORDER BY ord", (k,))]
            ctxs = con.execute("SELECT text, conf FROM project_contexts WHERE project_code=? ORDER BY ord", (k,)).fetchall()
            props = {"Name": r["name"], "Code": k, "Employer": r["employer"], "Period": r["period"],
                     "Status": r["status"], "Domains": json.dumps(split_list(r["domains"]), ensure_ascii=False),
                     "Title variants": " · ".join(titles), "Context line": ctxs[0]["text"] if ctxs else "",
                     "Confirmed metrics": r["metrics"], "Best for JD types": r["best_for"],
                     "Constraints": r["constraints"], "Evidence (Notion KB)": r["evidence"],
                     "CONF refs": " · ".join(c["conf"] for c in ctxs),
                     "Bullets": _rel(con, "SELECT notion_id FROM bullets WHERE project_code=? ORDER BY rowid", k)}
        elif table == "bullets":
            leads = [x[0] for x in con.execute("SELECT lead FROM bullet_leads WHERE bullet_code=? ORDER BY ord", (k,))]
            used = [x[0] for x in con.execute(
                "SELECT DISTINCT jd FROM application_picks WHERE kind='bullet' AND code=?", (k,))]
            props = {"Name": f"{k} · {leads[0]}", "Code": k, "Status": r["status"], "Body": r["body"],
                     "Lead variants": " · ".join(leads),
                     "Capabilities": json.dumps(split_list(r["capabilities"]), ensure_ascii=False),
                     "JD keywords carried": r["keywords_carried"], "Metric": r["metric"], "CONF refs": r["conf"],
                     "Constraints": r["constraints"], "Evidence (Notion KB)": r["evidence"], "Chars": r["chars"],
                     "Used in applications": ", ".join(used),
                     "Project": _rel(con, "SELECT notion_id FROM projects WHERE code=?", r["project_code"]),
                     "JD keywords": _rel(con, "SELECT k.notion_id FROM keyword_bullets kb JOIN keywords k "
                                              "ON k.term=kb.term WHERE kb.bullet_code=?", k)}
        elif table == "skills":
            row_vars = sorted({x[0] for x in con.execute(
                "SELECT r.variants FROM skill_row_items i JOIN skill_rows r ON r.label=i.row_label "
                "WHERE i.item=? AND r.variants!=''", (k,))})
            props = {"Name": k, "Category": r["category"], "ESCO concept": r["esco"],
                     "Spelling variants": r["variants"], "Confirmed by": r["conf"],
                     "On CV": "__YES__" if r["on_cv"] else "__NO__", "Row label variants": " · ".join(row_vars),
                     "JD keywords": _rel(con, "SELECT k.notion_id FROM keyword_skills ks JOIN keywords k "
                                              "ON k.term=ks.term WHERE ks.skill_name=?", k)}
        elif table == "concepts":
            ev = con.execute("SELECT * FROM concept_evidence WHERE concept_code=? ORDER BY rowid", (k,)).fetchall()
            props = {"Name": r["name"], "Code": k, "Kind": r["kind"], "Claim": r["claim"], "Canonical": r["canonical"],
                     "ESCO concept": r["esco"], "Definition": r["definition"], "Notes": r["notes"],
                     "Aliases": " · ".join(x[0] for x in con.execute(
                         "SELECT alias FROM concept_aliases WHERE concept_code=? ORDER BY rowid", (k,))),
                     "Evidence": " · ".join(f"{e['kind']} {e['code']} ({e['strength']})" for e in ev),
                     "Relations": " · ".join(f"{x[0]} {x[1]}" for x in con.execute(
                         "SELECT rel, dst FROM concept_relations WHERE src=?", (k,))),
                     "Bullets": _rel(con, "SELECT DISTINCT b.notion_id FROM concept_evidence e JOIN bullets b ON b.code=e.code "
                                          "WHERE e.concept_code=? AND e.kind IN ('bullet', 'lead')", k),
                     "Skills": _rel(con, "SELECT s.notion_id FROM concept_evidence e JOIN skills s ON s.name=e.code "
                                         "WHERE e.concept_code=? AND e.kind='skill'", k),
                     "JD keywords": _rel(con, "SELECT notion_id FROM keywords WHERE concept_code=? ORDER BY rowid", k),
                     "Related concepts": _rel(con, "SELECT c.notion_id FROM concept_relations r JOIN concepts c "
                                                   "ON c.code=r.dst WHERE r.src=?", k)}
        else:
            props = {"Name": k, "Type": r["type"], "Coverage": r["coverage"], "ESCO concept": r["esco"],
                     "Exact spelling to use": r["spelling"], "Notes": r["notes"],
                     "Seen in JDs": json.dumps([x[0] for x in con.execute(
                         "SELECT jd FROM keyword_jds WHERE term=?", (k,))], ensure_ascii=False),
                     "Covered by bullets": _rel(con, "SELECT b.notion_id FROM keyword_bullets kb JOIN bullets b "
                                                     "ON b.code=kb.bullet_code WHERE kb.term=?", k),
                     "Covered by skills": _rel(con, "SELECT s.notion_id FROM keyword_skills ks JOIN skills s "
                                                    "ON s.name=ks.skill_name WHERE ks.term=?", k)}
            if r["freq"] is not None:
                props["JD frequency"] = r["freq"]
        out.append(props)
    return out


# ---------------------------------------------------------------- record
def record(con: sqlite3.Connection, jd: str, company: str, title: str, app_dir: str,
           picks: list[tuple[str, str]], headline: str | None = None, country: str | None = None) -> int:
    res = resolve(con, picks)
    head = cv_head(app_path(app_dir) / "03_cv.md")
    with con:
        con.execute("DELETE FROM application_picks WHERE jd=?", (jd,))
        con.execute("INSERT INTO applications(jd, company, title, dir, created, headline, country, summary_chars) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(jd) DO UPDATE SET company=excluded.company, "
                    "title=excluded.title, dir=excluded.dir, created=excluded.created, "
                    "headline=coalesce(excluded.headline, headline), country=coalesce(excluded.country, country), "
                    "summary_chars=coalesce(excluded.summary_chars, summary_chars)",
                    (jd, company, title, app_dir, dt.date.today().isoformat(), headline or head.get("headline"),
                     country or head.get("country"), head.get("summary_chars")))
        for pos, r in enumerate(res):
            con.execute("INSERT INTO application_picks VALUES(?,?,?,?,?,?,?,?)",
                        (jd, r["kind"], r["code"], r.get("variant_ord"), r.get("context_ord"),
                         r.get("lead"), r.get("items"), pos))
    return len(res)



# ---------------------------------------------------------------- user data: interview_qa, outreach (spec v3 §8)
QA_KINDS = ("behavioural", "technical", "system-design", "project", "motivation", "gap", "ask", "other")
QA_SOURCES = ("generated", "asked", "self")
QA_FIELDS = ("jd", "question", "kind", "concepts", "bullets", "answer", "source", "outcome", "notes")
OUT_CHANNELS = ("email", "linkedin", "referral", "other")
OUT_KINDS = ("referral-letter", "connect-note", "follow-up", "thank-you")
OUT_STATUS = ("draft", "sent", "replied", "no-reply", "declined")


def _check(val: str | None, allowed: tuple[str, ...], what: str) -> None:
    if val is not None and val not in allowed:
        raise PickError(f"{what} {val!r} not in {' | '.join(allowed)}")


def _check_codes(con: sqlite3.Connection, csv: str | None, table: str, what: str) -> str:
    codes = split_list(csv)
    for c in codes:
        if not con.execute(f"SELECT 1 FROM {table} WHERE code=?", (c,)).fetchone():
            raise PickError(f"unknown {what} code {c!r} (see `library_db.py {table}`)")
    return ",".join(codes)


def _company(con: sqlite3.Connection, jd: str | None) -> str:
    row = con.execute("SELECT company FROM applications WHERE jd=?", (jd,)).fetchone() if jd else None
    return row[0] if row else (jd or "")


def _csv(v: Any) -> str | None:
    return ",".join(str(x).strip() for x in v if str(x).strip()) if isinstance(v, (list, tuple)) else v


def qa_validate(con: sqlite3.Connection, d: dict[str, Any]) -> None:
    """Raise PickError for any bad field, without writing."""
    if not d.get("question") or not d.get("jd"):
        raise PickError("qa needs jd and question")
    _check(d.get("kind") or "other", QA_KINDS, "kind")
    _check(d.get("source") or "generated", QA_SOURCES, "source")
    _check_codes(con, _csv(d.get("concepts")), "concepts", "concept")
    _check_codes(con, _csv(d.get("bullets")), "bullets", "bullet")


def qa_import(con: sqlite3.Connection, rows: list[dict[str, Any]], update: bool = False,
              prune: bool = False) -> dict[str, Any]:
    """All-or-nothing: validate every row first, then write in one transaction."""
    for i, r in enumerate(rows):
        try:
            if not isinstance(r, dict):
                raise PickError("not an object")
            qa_validate(con, r)
        except PickError as e:
            raise PickError(f"row {i + 1}: {e} — nothing imported") from None
    res = {"inserted": 0, "updated": 0, "skipped": 0, "stale": [], "pruned": 0}
    with con:
        for r in rows:
            if update:
                res["inserted" if qa_upsert(con, r, tx=False) == "inserted" else "updated"] += 1
            elif qa_add(con, r, skip_dup=True, tx=False) is None:
                res["skipped"] += 1
            else:
                res["inserted"] += 1
        for jd in sorted({r["jd"] for r in rows}):
            have = {r["question"] for r in rows if r["jd"] == jd}
            for row in con.execute("SELECT id, question, source FROM interview_qa WHERE jd=? ORDER BY id", (jd,)).fetchall():
                if row["question"] in have:
                    continue
                res["stale"].append((row["id"], jd, row["source"], row["question"]))
                if prune and row["source"] == "generated":
                    con.execute("DELETE FROM interview_qa WHERE id=?", (row["id"],))
                    res["pruned"] += 1
    return res


def qa_upsert(con: sqlite3.Connection, d: dict[str, Any], tx: bool = True) -> str:
    """Insert, or update answer/kind/concepts/bullets/notes/source of the (jd, question) row. Returns inserted|updated."""
    row = con.execute("SELECT id FROM interview_qa WHERE jd=? AND question=?", (d.get("jd"), d.get("question"))).fetchone()
    if not row:
        qa_add(con, d, tx=tx)
        return "inserted"
    sets: dict[str, str] = {}
    if d.get("kind"):
        _check(d["kind"], QA_KINDS, "kind")
        sets["kind"] = d["kind"]
    if d.get("source"):
        _check(d["source"], QA_SOURCES, "source")
        sets["source"] = d["source"]
    if d.get("concepts") is not None:
        sets["concepts"] = _check_codes(con, _csv(d["concepts"]), "concepts", "concept")
    if d.get("bullets") is not None:
        sets["bullets"] = _check_codes(con, _csv(d["bullets"]), "bullets", "bullet")
    for k in ("answer", "notes"):
        if d.get(k) is not None:
            sets[k] = d[k]
    if sets:
        with (con if tx else contextlib.nullcontext()):
            con.execute(f"UPDATE interview_qa SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                        (*sets.values(), row[0]))
    return "updated"


def qa_add(con: sqlite3.Connection, d: dict[str, Any], skip_dup: bool = False, tx: bool = True) -> int | None:
    d = {**d, "concepts": _csv(d.get("concepts")), "bullets": _csv(d.get("bullets"))}
    if not d.get("question") or not d.get("jd"):
        raise PickError("qa needs jd and question")
    kind, source = d.get("kind") or "other", d.get("source") or "generated"
    _check(kind, QA_KINDS, "kind")
    _check(source, QA_SOURCES, "source")
    if skip_dup and con.execute("SELECT 1 FROM interview_qa WHERE jd=? AND question=?",
                                (d["jd"], d["question"])).fetchone():
        return None
    with (con if tx else contextlib.nullcontext()):
        cur = con.execute(
            "INSERT INTO interview_qa(jd, company, question, kind, concepts, bullets, answer, source, outcome, notes, created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (d["jd"], _company(con, d["jd"]), d["question"], kind,
             _check_codes(con, d.get("concepts"), "concepts", "concept"),
             _check_codes(con, d.get("bullets"), "bullets", "bullet"),
             d.get("answer") or "", source, d.get("outcome") or "", d.get("notes") or "",
             dt.date.today().isoformat()))
    return cur.lastrowid


def qa_list(con: sqlite3.Connection, jd: str | None, kind: str | None, concept: str | None,
            source: str | None, full: bool) -> None:
    _check(kind, QA_KINDS, "kind")
    _check(source, QA_SOURCES, "source")
    rows = [r for r in con.execute("SELECT * FROM interview_qa ORDER BY id")
            if (not jd or r["jd"] == jd) and (not kind or r["kind"] == kind) and (not source or r["source"] == source)
            and (not concept or concept in split_list(r["concepts"]))]
    for r in rows:
        q = r["question"] if full or len(r["question"]) <= 80 else r["question"][:77] + "..."
        print(f"{r['id']} | {r['jd']} | {r['kind']} | {r['concepts'] or '-'} | {q}")
        if full:
            print(f"    source {r['source']} · bullets {r['bullets'] or '-'} · outcome {r['outcome'] or '-'}"
                  + (f" · notes {r['notes']}" if r["notes"] else ""))
            print("    " + (r["answer"] or "(no answer)").replace("\n", "\n    "))
    print(f"{len(rows)} row(s)")


def qa_set(con: sqlite3.Connection, qid: int, **fields: str | None) -> str:
    sets = {k: v for k, v in fields.items() if v is not None}
    if not sets:
        raise PickError("qa set needs --outcome, --notes or --answer")
    with con:
        n = con.execute(f"UPDATE interview_qa SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                        (*sets.values(), qid)).rowcount
    if not n:
        raise PickError(f"no interview_qa row {qid}")
    return f"qa {qid}: set {', '.join(sets)}"


def outreach_add(con: sqlite3.Connection, d: dict[str, Any]) -> int:
    for k in ("jd", "person", "channel", "kind"):
        if not d.get(k):
            raise PickError(f"outreach needs {k}")
    status = d.get("status") or "draft"
    _check(d["channel"], OUT_CHANNELS, "channel")
    _check(d["kind"], OUT_KINDS, "kind")
    _check(status, OUT_STATUS, "status")
    with con:
        cur = con.execute(
            "INSERT INTO outreach(jd, company, person, role, channel, kind, status, file, sent, replied, notes, created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["jd"], _company(con, d["jd"]), d["person"], d.get("role") or "", d["channel"], d["kind"], status,
             d.get("file") or "", "", "", d.get("notes") or "", dt.date.today().isoformat()))
    return cur.lastrowid


def outreach_list(con: sqlite3.Connection, jd: str | None, status: str | None) -> None:
    _check(status, OUT_STATUS, "status")
    rows = [r for r in con.execute("SELECT * FROM outreach ORDER BY id")
            if (not jd or r["jd"] == jd) and (not status or r["status"] == status)]
    for r in rows:
        print(f"{r['id']} | {r['jd']} | {r['person']}{' (' + r['role'] + ')' if r['role'] else ''} | {r['channel']} | "
              f"{r['kind']} | {r['status']} | sent {r['sent'] or '-'} | replied {r['replied'] or '-'}"
              + (f" | {r['file']}" if r["file"] else ""))
    print(f"{len(rows)} row(s)")


def outreach_set(con: sqlite3.Connection, oid: int, status: str | None, sent: str | None,
                 replied: str | None, notes: str | None) -> str:
    _check(status, OUT_STATUS, "status")
    sets = {k: v for k, v in (("status", status), ("sent", sent), ("replied", replied), ("notes", notes)) if v is not None}
    if not sets:
        raise PickError("outreach set needs --status, --sent, --replied or --notes")
    with con:
        n = con.execute(f"UPDATE outreach SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                        (*sets.values(), oid)).rowcount
    if not n:
        raise PickError(f"no outreach row {oid}")
    return f"outreach {oid}: set {', '.join(sets)}"


# ---------------------------------------------------------------- selftest
def selftest(con: sqlite3.Connection) -> bool:
    ok = True
    app = ROOT / TOMRA_DIR
    # the recorded TOMRA picks win; TOMRA_PICKS is only the bootstrap for a fresh DB
    try:
        picks = recorded_picks(con, "TOMRA")
    except PickError:
        picks = parse_picks(TOMRA_PICKS)
    problems = diff_cv(con, picks, app / "03_cv.md")
    print(f"[1] render TOMRA vs 03_cv.md Skills + Delta block: {'PASS' if not problems else 'FAIL'}")
    for p in problems:
        print("    " + p)
    ok &= not problems

    m = match_keywords(con, app / "07_job_keywords.json")
    ats = (app / "07_external_ats.md").read_text(encoding="utf-8")
    found_m = re.search(r"^Found: (.+)$", ats, re.M)
    total = re.search(r"\((\d+)/(\d+)\)", ats)
    found = set(found_m.group(1).split(" · ")) if found_m else set()
    n_found, n_total = (int(total.group(1)), int(total.group(2))) if total else (-1, -1)
    same = set(m["covered"]) == found
    pass2 = (m["new"] == 0 and len(m["covered"]) == n_found and same
             and len(m["not_covered"]) == n_total - n_found)
    print(f"[2] match 07_job_keywords.json: new {m['new']} · covered {len(m['covered'])}/{n_found} · "
          f"not covered {len(m['not_covered'])}/{n_total - n_found} · same terms as ATS 'Found': {same} "
          f"→ {'PASS' if pass2 else 'FAIL'}")
    if not same:
        print(f"    db-only: {sorted(set(m['covered']) - found)} · ats-only: {sorted(found - set(m['covered']))}")
    ok &= pass2

    md = export_md(con)
    seed_text = SEED.read_text(encoding="utf-8")
    seed = json.loads(seed_text)
    codes = [b["code"] for b in seed["bullets"]]
    confs = sorted(set(CONF_RE.findall(seed_text)))
    miss = [c for c in codes if f"**{c}**" not in md] + [c for c in confs if c not in md]
    print(f"[3] export-md: {len(codes)} bullet codes, {len(confs)} CONF refs · missing {len(miss)} "
          f"→ {'PASS' if not miss else 'FAIL'}")
    if miss:
        print("    missing: " + ", ".join(miss))
    ok &= not miss

    # v2 acceptance (library_db_spec_v2.md) 2–6
    fx = json.loads((FIXTURES / "scout24_keywords_raw.json").read_text(encoding="utf-8"))
    norm, _ = normalize_keywords(fx, fx["_jd_text"])
    flat = [t for f, _ in KW_FIELDS for t in norm[f]]
    single = all(split_term(t) == [t] for t in flat)
    unique = len({t.casefold() for t in flat}) == len(flat)
    same = all(norm[f] == fx["_expected"][f] for f, _ in KW_FIELDS)
    pass4 = single and unique and same
    print(f"[4] normalize-keywords fixture: {sum(kw_count(fx))} → {len(flat)} terms · single-concept {single} · "
          f"no duplicates {unique} · posting order = expected {same} → {'PASS' if pass4 else 'FAIL'}")
    ok &= pass4

    f = fit_picks(con, parse_picks("scope=SCOPE;card=PS:1:0;bullet=PS1:7"))
    over = [x["ref"] for x in f["rows"] if x["over"]]
    pass5 = not f["ok"] and any(r.startswith("PS1") for r in over)
    print(f"[5] fit PS1:7 → {'exit 1' if not f['ok'] else 'exit 0'}, over: {', '.join(over) or '-'} "
          f"→ {'PASS' if pass5 else 'FAIL'}")
    ok &= pass5

    pass6, notes, suggested = True, [], {}
    for jd in [r[0] for r in con.execute("SELECT jd FROM applications ORDER BY created, rowid")]:
        try:
            r = suggested[jd] = suggest_picks(con, recorded_dir(con, jd))
            n_b = sum(1 for l in render_lines(con, r["picks"])[1] if l.startswith("- **"))
            good = r["fit"]["ok"] and 5 <= n_b <= 6
        except (PickError, FileNotFoundError) as e:
            good, n_b = False, f"error {e}"
        notes.append(f"{jd} {n_b} bullets {'fits' if good else 'FAILS'}")
        if jd in ("TOMRA", "Scout24"):
            pass6 &= good
    print(f"[6] pick → fit + render: {' · '.join(notes)} → {'PASS' if pass6 else 'FAIL'}")
    ok &= pass6

    mem = sqlite3.connect(":memory:")   # ingest on a copy: selftest never writes the DB
    con.backup(mem)
    mem.row_factory = sqlite3.Row
    ingest_ats(mem, "TOMRA", ROOT / TOMRA_DIR)
    rows = mem.execute("SELECT k.coverage FROM keywords k JOIN keyword_jds j ON j.term=k.term WHERE j.jd='TOMRA'").fetchall()
    n_cov = sum(r[0] == "covered" for r in rows)
    pass7 = len(rows) == 60 and n_cov == 43
    print(f"[7] ingest-ats TOMRA → keywords --jd TOMRA: {len(rows)} terms, {n_cov} covered (want 60 / 43) "
          f"→ {'PASS' if pass7 else 'FAIL'}")
    mem.close()
    ok &= pass7

    st_all = app_status(con)
    st = [r for r in st_all if (app_path(r["dir"]) / "07_external_ats.json").exists()]
    pending = [r["jd"] for r in st_all if r not in st]   # in-progress applications: no ATS file yet
    bad = []
    for r in st:
        ats = json.loads((app_path(r["dir"]) / "07_external_ats.json").read_text(encoding="utf-8"))
        km = ats["keyword_match"]
        if r["ats"] != ats["ats_score"].get("overall_score") or r["kw"] != f"{len(km['found'])}/{km['n_terms']}":
            bad.append(r["jd"])
    pass8 = len(st) >= 6 and not bad
    print(f"[8] status: {len(st)} rows{' (skipped, no ATS yet: ' + ', '.join(pending) + ')' if pending else ''}"
          f" · ATS = 07_external_ats.json: {not bad} "
          f"({' · '.join(str(r['jd']) + ' ' + str(r['ats']) for r in st)}) "
          f"→ {'PASS' if pass8 else 'FAIL'}")
    ok &= pass8
    orphan_kw = assign_concepts(con)
    n_c = con.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    pass9 = n_c > 0 and not orphan_kw
    print(f"[9] concepts: {n_c} concepts · keywords without concept_code: {len(orphan_kw)} → {'PASS' if pass9 else 'FAIL'}")
    ok &= pass9

    # v3 acceptance (library_db_spec_v3.md §6) 2–6
    aliases = alias_index(con)
    want = {"AI agents": "AGENTIC", "LLM orchestration": "AGENTIC", "agentic workflow": "AGENTIC"}
    got = {t: resolve_term(t, aliases) for t in [*want, "Agentic Decision Platform"]}
    dnc = got["Agentic Decision Platform"]
    dnc_claim = con.execute("SELECT claim FROM concepts WHERE code=?", (dnc["code"],)).fetchone() if dnc["code"] else None
    pass10 = (all(got[t]["code"] == c and got[t]["rule"] <= 3 for t, c in want.items())
              and dnc_claim is not None and dnc_claim[0] == "do not claim")
    print("[10] resolve: " + " · ".join(f"{t!r} → {r['code'] or 'NEW'} (rule {r['rule']})" for t, r in got.items())
          + f" [{dnc_claim[0] if dnc_claim else '-'}] → {'PASS' if pass10 else 'FAIL'}")
    ok &= pass10

    pass11, note11 = False, "no Scout24 application"
    if "Scout24" in suggested:
        top6 = suggested["Scout24"]["ranked"][:6]
        al = align(con, recorded_dir(con, "Scout24"), parse_picks(SCOUT24_PICKS))
        dt1 = next((v for k, v in al["picks"] if k == "bullet" and v.startswith("DT1:")), None)
        ord_ = int(dt1.split(":")[1]) if dt1 else -1
        lead_c = con.execute("SELECT lead, concept_code FROM bullet_leads WHERE bullet_code='DT1' AND ord=?",
                             (ord_,)).fetchone()
        prop = [p for p in al["proposals"] if p["bullet"] == "DT1" and p["concept"] == "AGENTIC"]
        agentic = bool(lead_c and lead_c[1] == "AGENTIC") or bool(prop)
        pass11 = "DT1" in top6 and agentic
        shown = (lead_c[0] if lead_c and lead_c[1] == "AGENTIC" else prop[0]["lead"] if prop else lead_c and lead_c[0])
        note11 = f"top 6 {', '.join(top6)} · align DT1 lead {shown!r} ({lead_c[1] if lead_c else '-'}{' + NEW LEAD' if prop else ''})"
    print(f"[11] pick --jd Scout24 ranks DT1 in the top 6, align recommends an 'agentic' lead: {note11} "
          f"→ {'PASS' if pass11 else 'FAIL'}")
    ok &= pass11

    pass12, notes = True, []
    for jd, r in suggested.items():
        if jd in pending:
            notes.append(f"{jd} skipped (no ATS yet)")
            continue
        try:
            ext = json.loads((recorded_dir(con, jd) / "07_external_ats.json").read_text(encoding="utf-8"))
            n_ext = len(ext["keyword_match"]["found"])
            rec, n = offline_ats(con, jd, recorded_picks(con, jd))
            sug, _ = offline_ats(con, jd, r["picks"])
            good = sug >= rec and rec == n_ext
            notes.append(f"{jd} {sug}/{n} vs manual {rec}" + ("" if rec == n_ext else f" (offline ≠ ATS {n_ext})"))
        except (PickError, FileNotFoundError, StopIteration) as e:
            good = False
            notes.append(f"{jd} error {e}")
        pass12 &= good
    tomra = diff_cv(con, picks, app / "03_cv.md")
    pass12 &= {r["jd"] for r in st} <= set(suggested) and not tomra
    print(f"[12] concept pick ATS ≥ manual pick (offline, ingested terms): {' · '.join(notes)} · "
          f"diff --jd TOMRA {'identical' if not tomra else 'DIFFERS'} → {'PASS' if pass12 else 'FAIL'}")
    ok &= pass12

    al = align(con, app, picks)
    pass13 = not al["proposals"]
    print(f"[13] align --jd TOMRA: {len(al['proposals'])} NEW LEAD proposals → {'PASS' if pass13 else 'FAIL'}")
    ok &= pass13

    pay = notion_payload(con, "concepts")
    ref = notion_payload(con, "keywords")[0]
    shape = all(isinstance(x, dict) and x.get("Name") and x.get("Code")
                and all(isinstance(v, (str, int)) for v in x.values())
                and all(isinstance(json.loads(x[k]), list) for k in ("Bullets", "Skills", "JD keywords", "Related concepts"))
                for x in pay) and all(isinstance(v, (str, int)) for v in ref.values())
    pass14 = "\n## Concepts\n" in md and len(pay) == n_c and shape
    print(f"[14] export-md has a Concepts section: {'## Concepts' in md} · notion-payload concepts: {len(pay)} rows, "
          f"payload shape {'ok' if shape else 'BAD'} → {'PASS' if pass14 else 'FAIL'}")
    ok &= pass14

    # [15] user-data tables: add/list on a temp copy, survive a simulated init, real DB untouched
    import hashlib
    import tempfile
    ids_file = NOTION_IDS_FILE
    sha0 = hashlib.sha1(ids_file.read_bytes()).hexdigest() if ids_file.exists() else ""
    with tempfile.TemporaryDirectory() as tmp:
        tdb = Path(tmp) / "copy.sqlite"
        dst = sqlite3.connect(tdb)
        con.backup(dst)
        dst.close()
        t = connect(tdb)
        n_real = (con.execute("SELECT count(*) FROM interview_qa").fetchone()[0],
                  con.execute("SELECT count(*) FROM outreach").fetchone()[0])
        code = t.execute("SELECT code FROM concepts LIMIT 1").fetchone()[0]
        bcode = t.execute("SELECT code FROM bullets LIMIT 1").fetchone()[0]
        qid = qa_add(t, {"jd": "TOMRA", "question": "selftest q", "kind": "technical", "concepts": code,
                         "bullets": bcode, "answer": "a"})
        oid = outreach_add(t, {"jd": "TOMRA", "person": "Test P", "channel": "email", "kind": "follow-up"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            qa_list(t, "TOMRA", "technical", code, None, False)
            outreach_list(t, "TOMRA", "draft")
        listed = "selftest q" in buf.getvalue() and "Test P" in buf.getvalue()
        dup = qa_add(t, {"jd": "TOMRA", "question": "selftest q"}, skip_dup=True) is None
        try:
            qa_add(t, {"jd": "TOMRA", "question": "x", "kind": "bogus"})
            bad = False
        except PickError:
            bad = True
        t.close()
        init_db(SEED, tdb, ids_path=Path(tmp) / "ids.json")
        t = connect(tdb)
        kept = (t.execute("SELECT count(*) FROM interview_qa WHERE id=?", (qid,)).fetchone()[0] == 1
                and t.execute("SELECT count(*) FROM outreach WHERE id=?", (oid,)).fetchone()[0] == 1)
        t.close()
    sha1 = hashlib.sha1(ids_file.read_bytes()).hexdigest() if ids_file.exists() else ""
    untouched = sha0 == sha1 and n_real == (con.execute("SELECT count(*) FROM interview_qa").fetchone()[0],
                           con.execute("SELECT count(*) FROM outreach").fetchone()[0])
    not_lib = "interview_qa" not in LIBRARY_TABLES and "outreach" not in LIBRARY_TABLES
    pass15 = listed and dup and bad and kept and untouched and not_lib
    print(f"[15] interview_qa + outreach: add/list {listed} · dup skipped {dup} · bad kind rejected {bad} · "
          f"survive init {kept} · real DB untouched {untouched} → {'PASS' if pass15 else 'FAIL'}")
    ok &= pass15
    print("selftest:", "PASS" if ok else "FAIL")
    return bool(ok)



# ---- sot-leads: batch lead proposals for SoT-supported JD terms ---------------------------------
def sot_leads(con: sqlite3.Connection, app_dir: Path, picks: list[tuple[str, str]], per_bullet: int = 1,
              max_terms: int = 3) -> dict[str, Any]:
    """For every picked bullet: the JD terms missing from the CV whose coverage is SoT-supported (or covered
    for another JD) and whose concept the bullet evidences → one lead proposal per bullet packing 2–3 of
    them (JD spelling, '&'-joined), fitted to the template line. Advisory; `--write` appends to the seed."""
    job = json.loads((app_dir / "07_job_keywords.json").read_text(encoding="utf-8"))
    sc = score_bullets(con, job)
    idx, jc = sc["idx"], sc["jd"]
    ats_p = app_dir / "07_external_ats.json"
    if ats_p.exists():
        missing = json.loads(ats_p.read_text(encoding="utf-8"))["keyword_match"]["missing"]
    else:
        missing = [t["term"] for t in jc["terms"]]
    cv_p = app_dir / "03_cv.md"
    with contextlib.redirect_stderr(io.StringIO()):
        text = ats_text(cv_with_picks(con, cv_p, picks)) if cv_p.exists() else " ".join(
            r["line"].replace("**", "") for r in resolve(con, picks))
    low = text.lower()
    missing = [t for t in missing if not has_term(t, low)]
    kw = {r["term"].casefold(): r for r in con.execute("SELECT term, coverage, concept_code FROM keywords")}
    weight = {t["term"].casefold(): t["w"] for t in jc["terms"]}
    writable = {"SoT-supported wording", "covered"}
    cand: dict[str, list[dict[str, Any]]] = {}      # concept → missing writable terms
    stems_seen: set[str] = set()
    for t in sorted(missing, key=lambda t: (-weight.get(t.casefold(), 1), -len(t.split()), len(t))):
        k = kw.get(t.casefold())
        if not (k and k["coverage"] in writable and k["concept_code"] and k["concept_code"] not in idx["excluded"]):
            continue
        if t.casefold() in SOT_SKIP or (len(t.split()) == 1 and t.islower() and len(t) < 5):
            continue                                       # adjectives / bare words make stuffed leads
        st = " ".join(sorted(kw_stems(t)))
        if st in stems_seen:
            continue                                       # reliable vs reliability: one is enough
        stems_seen.add(st)
        cand.setdefault(k["concept_code"], []).append({"term": t, "w": weight.get(t.casefold(), 1)})
    res = resolve(con, picks)
    bullets = [r for r in res if r["kind"] == "bullet"]
    used: set[str] = set()
    out = []
    for r in bullets:
        code = r["code"]
        b = idx["bullets"][code]
        cur = idx["leads"][code][r["variant_ord"]]["lead"]
        pool = []
        for c, strength in sorted(idx["ev"][code].items(), key=lambda cs: -cs[1]):
            for t in cand.get(c, []):
                if t["term"].casefold() not in used:
                    pool.append({**t, "concept": c, "strength": strength})
        if not pool:
            continue
        # strongest evidence first, then JD weight, multi-word nouns before single words, then shorter
        pool.sort(key=lambda t: (-t["strength"], -t["w"], len(t["term"].split()) == 1, len(t["term"]), t["term"].casefold()))
        proposals = []
        for _ in range(per_bullet):
            chosen: list[dict[str, Any]] = []
            for t in pool:
                if t["term"].casefold() in used or len(chosen) >= max_terms:
                    continue
                trial = chosen + [t]
                lead = _join_lead([x["term"] for x in trial])
                if fit_line("bullet", f"{code}:sot", f"- **{lead} —** {b['body']}")["over"]:
                    continue
                chosen = trial
            if not chosen:
                break
            lead = _join_lead([x["term"] for x in chosen])
            for x in chosen:
                used.add(x["term"].casefold())
            proposals.append({"lead": lead, "terms": [x["term"] for x in chosen],
                              "concepts": sorted({x["concept"] for x in chosen}),
                              "implied": [x["term"] for x in chosen if x["strength"] < 1.0]})
        if proposals:
            out.append({"bullet": code, "current": cur, "body": b["body"], "proposals": proposals})
    # projection: the picked leads replaced by the first proposal of each bullet
    proj = text
    for o in out:
        proj = proj.replace(f"{o['current']} — {o['body']}", f"{o['proposals'][0]['lead']} — {o['body']}")
    terms_all = [t["term"] for t in jc["terms"]]
    before = {t for t in terms_all if has_term(t, low)}
    after = {t for t in terms_all if has_term(t, proj.lower())}
    left = sorted({t["term"] for v in cand.values() for t in v if t["term"].casefold() not in used})
    # the JD's own sentence for every bundled term: the lead should be phrased after the JD's duty wording,
    # not as a list of nouns (writing_rules: bold lead = JD duty phrase)
    jd_p = next((q for q in (app_dir / "jd.md", app_dir / "01_jd.md") if q.exists()), None)
    sents = jd_sentences(jd_p.read_text(encoding="utf-8")) if jd_p else []
    for o in out:
        for pr in o["proposals"]:
            pr["jd_context"] = {t: [x for x in sents if has_term(t, x)][:2] for t in pr["terms"]}
    return {"rows": out, "before": len(before), "after": len(after), "n_terms": len(terms_all),
            "gained": sorted(after - before), "unplaced": left, "n_missing": len(missing)}


def jd_sentences(text: str) -> list[str]:
    """JD lines/sentences (markdown bullets and headings stripped), ≤ 220 chars each."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"^[#\-*\s]+", "", line).strip()
        if not line or line.startswith("|"):
            continue
        for sent in re.split(r"(?<=[.;!?])\s+", line):
            sent = sent.strip()
            if 12 <= len(sent) <= 220:
                out.append(sent)
    return out


SOT_SKIP = {"reliable", "secure", "scalable", "remote", "production-ready", "ai-enabled", "ai-native", "open-source",
            "offline", "implementation", "operation", "end-to-end", "production", "english", "fluent english", "german"}


def _join_lead(terms: list[str]) -> str:
    ts = [t[0].upper() + t[1:] if i == 0 else t for i, t in enumerate(terms)]
    return ts[0] if len(ts) == 1 else ", ".join(ts[:-1]) + " & " + ts[-1]


def print_sot_leads(r: dict[str, Any]) -> None:
    if not r["rows"]:
        print("no SoT-supported JD terms can be placed on the picked bullets")
    for o in r["rows"]:
        print(f"{o['bullet']}  current lead: {o['current']}")
        for p in o["proposals"]:
            imp = f" · implied evidence: {', '.join(p['implied'])}" if p["implied"] else ""
            print(f"  → \"{p['lead']}\"  [{', '.join(p['concepts'])}] +{len(p['terms'])} terms{imp}")
            for t, ctx in p.get("jd_context", {}).items():
                for c in ctx[:1]:
                    print(f"      JD · {t}: “{c}”")
    print(f"\nJD terms on the CV: {r['before']}/{r['n_terms']} → {r['after']}/{r['n_terms']} with the first proposal "
          f"per bullet applied · gained: {', '.join(r['gained']) or '-'}")
    if r["unplaced"]:
        print(f"writable but not placed ({len(r['unplaced'])}; no picked bullet evidences the concept, or line full): "
              + " · ".join(r["unplaced"]))


def check_sot_leads(con: sqlite3.Connection, r: dict[str, Any], leads: dict[str, list[str]]) -> list[str]:
    """Hand-written leads for the bundles: each must carry ≥ 1 of its bullet's bundle terms (JD spelling) and fit."""
    problems = []
    bundle = {o["bullet"]: {t.casefold() for p in o["proposals"] for t in p["terms"]} for o in r["rows"]}
    for code, lst in leads.items():
        b = con.execute("SELECT body FROM bullets WHERE code=?", (code,)).fetchone()
        if not b:
            problems.append(f"{code}: no such bullet")
            continue
        for lead in lst:
            hit = [t for t in bundle.get(code, set()) if has_term(t, lead)]
            if not hit:
                problems.append(f"{code} {lead!r}: carries none of its bundle terms {sorted(bundle.get(code, set()))}")
            if fit_line("bullet", f"{code}:sot", f"- **{lead} —** {b['body']}")["over"]:
                problems.append(f"{code} {lead!r}: over the line limit")
    return problems


def write_sot_leads(r: dict[str, Any], jd: str, seed: Path = SEED, leads: dict[str, list[str]] | None = None) -> int:
    data = json.loads(seed.read_text(encoding="utf-8"))
    by = {b["code"]: b for b in data["bullets"]}
    n = 0
    todo = leads if leads is not None else {o["bullet"]: [p["lead"] for p in o["proposals"]] for o in r["rows"]}
    for code, lst in todo.items():
        for lead in lst:
            if lead not in by[code]["leads"]:
                by[code]["leads"].append(lead)
                n += 1
    if n:
        data["_meta"]["note"] += f" | {dt.date.today().isoformat()}: +{n} SoT leads for {jd} (sot-leads --write)"
        seed.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return n


# ---------------------------------------------------------------- cli
class OrderedPick(argparse.Action):
    """--scope/--card/--bullet/--skills-row keep their command-line order in args.ordered."""

    def __call__(self, parser, ns, value, option_string=None):
        ns.ordered = (getattr(ns, "ordered", None) or []) + [(self.dest, value)]


def add_pick_args(p: argparse.ArgumentParser) -> None:
    for flag, dest in (("--scope", "scope"), ("--card", "card"), ("--bullet", "bullet"), ("--skills-row", "skill_row")):
        p.add_argument(flag, dest=dest, action=OrderedPick, metavar="SPEC")
    p.add_argument("--picks", help='"scope=...;card=VI:0:0;bullet=VI1:0;skill_row=..."')
    p.add_argument("--jd", help="use the picks recorded for this JD")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--seed", type=Path, default=SEED)
    p.add_argument("--fresh", action="store_true", help="also drop recorded applications and Notion ids")
    sub.add_parser("cards").add_argument("--for", dest="archetype")
    for name in ("card", "bullet", "leads"):
        sub.add_parser(name).add_argument("code")
    p = sub.add_parser("skills")
    p.add_argument("--category")
    p.add_argument("--rows", action="store_true")
    p = sub.add_parser("keywords")
    p.add_argument("--jd")
    p.add_argument("--coverage")
    p.add_argument("--term")
    sub.add_parser("match").add_argument("path", type=Path)
    sub.add_parser("resolve", help="term → concept (spec v3 §3.3, rules 1–4)").add_argument("term")
    p = sub.add_parser("align", help="JD nouns into the leads of the picked bullets (spec v3 §3.5)")
    p.add_argument("--jd", required=True)
    p.add_argument("--dir", type=Path, help="application dir (default: the recorded dir of --jd)")
    p.add_argument("--picks", help="pick string (default: the recorded picks of --jd)")
    p = sub.add_parser("gaps", help="JD concepts not claimable as covered (spec v3 §3.6)")
    p.add_argument("--jd", required=True)
    p.add_argument("--dir", type=Path, help="application dir (default: the recorded dir of --jd)")
    p = sub.add_parser("sot-leads", help="lead proposals packing the JD's SoT-supported terms onto the picked bullets")
    p.add_argument("--jd", required=True)
    p.add_argument("--dir", type=Path, help="application dir (default: the recorded dir of --jd)")
    p.add_argument("--picks", help="pick string (default: the recorded picks of --jd)")
    p.add_argument("--per-bullet", type=int, default=1)
    p.add_argument("--max-terms", type=int, default=3)
    p.add_argument("--json", action="store_true", help="print the bundles as JSON (for hand-writing the leads)")
    p.add_argument("--write", action="store_true", help="append the mechanical drafts to the seed leads (then run init)")
    p.add_argument("--write-from", type=Path, help='JSON {"PS1": ["lead", ...]} of hand-written leads → seed (checked)')
    p = sub.add_parser("set-concept", help="assign a term to a concept (survives init)")
    p.add_argument("term")
    p.add_argument("code")
    p = sub.add_parser("render")
    add_pick_args(p)
    p.add_argument("--part", choices=["all", "skills", "delta"], default="all")
    p.add_argument("--crlf", action="store_true", help="CRLF line endings, as in 03_cv.md")
    p = sub.add_parser("diff")
    add_pick_args(p)
    p.add_argument("--cv", type=Path, help="03_cv.md (default: <recorded dir>/03_cv.md with --jd)")
    p = sub.add_parser("record")
    for a in ("--jd", "--company", "--title", "--dir", "--picks"):
        p.add_argument(a, required=True)
    p.add_argument("--headline", help="L2 text without ** (default: from <dir>/03_cv.md)")
    p.add_argument("--country", help="relocation country (default: from <dir>/03_cv.md)")
    sub.add_parser("picks").add_argument("jd")
    p = sub.add_parser("normalize-keywords")
    p.add_argument("path", type=Path)
    p.add_argument("--write", action="store_true", help="save in place and append to _note")
    p = sub.add_parser("fit")
    add_pick_args(p)
    p = sub.add_parser("pick")
    p.add_argument("--jd", required=True)
    p.add_argument("--dir", type=Path, help="application dir (default: the recorded dir of --jd)")
    p.add_argument("--archetype")
    p.add_argument("--cards", type=int, choices=[2, 3])
    p = sub.add_parser("ingest-ats")
    p.add_argument("--jd", required=True)
    p.add_argument("--dir", type=Path, help="application dir (default: the recorded dir of --jd)")
    p = sub.add_parser("set-coverage")
    p.add_argument("term")
    p.add_argument("coverage", help='covered | gap | "do not claim" | sot-supported')
    p.add_argument("--note")
    sub.add_parser("status")
    sub.add_parser("export-md").add_argument("--out", type=Path, default=EXPORT)
    p = sub.add_parser("notion-payload")
    p.add_argument("table", choices=list(NOTION_KEYS) + ["applications"])
    p.add_argument("--only-new", action="store_true", help="rows without a notion_id")
    p.add_argument("--jd", help="applications: only this JD")
    p = sub.add_parser("set-notion-id")
    p.add_argument("table", choices=list(NOTION_KEYS) + ["applications"])
    p.add_argument("code")
    p.add_argument("id")
    p = sub.add_parser("concepts", help="list concepts (spec v3)")
    p.add_argument("--kind")
    p.add_argument("--claim", help="covered | sot | gap | dnc")
    p.add_argument("--orphans", action="store_true", help="keywords without a concept, claimable concepts without evidence")
    sub.add_parser("concept").add_argument("code")
    p = sub.add_parser("concepts-bootstrap", help="cluster the keyword map into concept proposals (spec v3 §3.1)")
    p.add_argument("--out", type=Path, help="write the proposal JSON here")
    p.add_argument("--min-link", type=float, default=0.34)
    p = sub.add_parser("qa", help="interview question/answer memory (spec v3 §8)")
    qs = p.add_subparsers(dest="qcmd", required=True)
    q = qs.add_parser("add")
    q.add_argument("--jd", required=True)
    q.add_argument("--question", required=True)
    q.add_argument("--kind", default="other", help=" | ".join(QA_KINDS))
    q.add_argument("--concepts")
    q.add_argument("--bullets")
    q.add_argument("--answer")
    q.add_argument("--answer-file", type=Path)
    q.add_argument("--source", default="generated", help=" | ".join(QA_SOURCES))
    q.add_argument("--outcome")
    q.add_argument("--notes")
    q = qs.add_parser("import")
    q.add_argument("path", type=Path)
    q.add_argument("--update", action="store_true", help="upsert answer/kind/concepts/bullets/notes/source on (jd, question)")
    q.add_argument("--prune", action="store_true", help="delete DB rows of the file's JDs that are not in the file (source generated only)")
    q = qs.add_parser("list")
    q.add_argument("--jd")
    q.add_argument("--kind")
    q.add_argument("--concept")
    q.add_argument("--source")
    q.add_argument("--full", action="store_true")
    q = qs.add_parser("set")
    q.add_argument("id", type=int)
    q.add_argument("--outcome")
    q.add_argument("--notes")
    q.add_argument("--answer")
    p = sub.add_parser("outreach", help="who was contacted at which company (spec v3 §8)")
    os_ = p.add_subparsers(dest="ocmd", required=True)
    o = os_.add_parser("add")
    o.add_argument("--jd", required=True)
    o.add_argument("--person", required=True)
    o.add_argument("--role")
    o.add_argument("--channel", required=True, help=" | ".join(OUT_CHANNELS))
    o.add_argument("--kind", required=True, help=" | ".join(OUT_KINDS))
    o.add_argument("--status", default="draft", help=" | ".join(OUT_STATUS))
    o.add_argument("--file")
    o.add_argument("--notes")
    o = os_.add_parser("list")
    o.add_argument("--jd")
    o.add_argument("--status")
    o = os_.add_parser("set")
    o.add_argument("id", type=int)
    o.add_argument("--status")
    o.add_argument("--sent")
    o.add_argument("--replied")
    o.add_argument("--notes")
    sub.add_parser("selftest")
    args = ap.parse_args()

    if args.cmd == "init":
        print(init_db(args.seed, args.db, args.fresh))
        return 0
    if not args.db.exists():
        print(f"{args.db} missing — run `library_db.py init` first", file=sys.stderr)
        return 2
    con = connect(args.db)
    try:
        if args.cmd == "cards":
            print_cards(con, args.archetype)
        elif args.cmd == "card":
            print_card(con, args.code)
        elif args.cmd in ("bullet", "leads"):
            print_bullet(con, args.code, leads_only=args.cmd == "leads")
        elif args.cmd == "skills":
            print_skills(con, args.category, args.rows)
        elif args.cmd == "keywords":
            print_keywords(con, args.jd, args.coverage, args.term)
        elif args.cmd == "match":
            print_match(match_keywords(con, args.path))
            print_concept_match(score_bullets(con, json.loads(args.path.read_text(encoding="utf-8"))))
        elif args.cmd == "resolve":
            print_resolve(con, args.term)
        elif args.cmd == "align":
            d = args.dir or recorded_dir(con, args.jd)
            print_align(align(con, d, parse_picks(args.picks) if args.picks else recorded_picks(con, args.jd)))
        elif args.cmd == "gaps":
            d = args.dir or recorded_dir(con, args.jd)
            print_gaps(gaps(con, json.loads((d / "07_job_keywords.json").read_text(encoding="utf-8"))))
        elif args.cmd == "sot-leads":
            d = args.dir or recorded_dir(con, args.jd)
            r = sot_leads(con, d, parse_picks(args.picks) if args.picks else recorded_picks(con, args.jd),
                          args.per_bullet, args.max_terms)
            if args.json:
                print(json.dumps({o["bullet"]: {"terms": [t for p in o["proposals"] for t in p["terms"]],
                                                "draft": o["proposals"][0]["lead"], "body": o["body"],
                                                "jd_context": {t: c for p in o["proposals"] for t, c in p.get("jd_context", {}).items()}}
                                  for o in r["rows"]},
                                 ensure_ascii=False, indent=1))
            else:
                print_sot_leads(r)
            if args.write_from:
                leads = json.loads(args.write_from.read_text(encoding="utf-8"))
                probs = check_sot_leads(con, r, leads)
                if probs:
                    raise PickError("\n".join(probs))
                print(f"appended {write_sot_leads(r, args.jd, leads=leads)} hand-written lead(s) to {SEED} — run `init`")
            elif args.write:
                print(f"appended {write_sot_leads(r, args.jd)} draft lead(s) to {SEED} — run `init`, then re-pick with the new lead ords")
        elif args.cmd == "set-concept":
            print(set_concept(con, args.term, args.code))
        elif args.cmd == "render":
            skills, delta = render_lines(con, picks_from_args(con, args))
            lines = (skills if args.part != "delta" else []) + ([""] if args.part == "all" and skills and delta else []) \
                + (delta if args.part != "skills" else [])
            sys.stdout.write(("\r\n" if args.crlf else "\n").join(lines) + ("\r\n" if args.crlf else "\n"))
        elif args.cmd == "diff":
            cv = args.cv
            if cv is None and args.jd:
                row = con.execute("SELECT dir FROM applications WHERE jd=?", (args.jd,)).fetchone()
                cv = ROOT / row["dir"] / "03_cv.md" if row else None
            if cv is None:
                raise PickError("diff needs --cv (or --jd with a recorded dir)")
            problems = diff_cv(con, picks_from_args(con, args), cv)
            print("\n".join(problems) if problems else f"identical: Skills rows + Delta block of {cv}")
            return 1 if problems else 0
        elif args.cmd == "record":
            n = record(con, args.jd, args.company, args.title, args.dir, parse_picks(args.picks),
                       args.headline, args.country)
            print(f"recorded {n} picks for {args.jd}")
        elif args.cmd == "picks":
            print(picks_to_spec(recorded_picks(con, args.jd)))
        elif args.cmd == "normalize-keywords":
            job = json.loads(args.path.read_text(encoding="utf-8"))
            jd_md = args.path.parent / "jd.md"
            new, diff = normalize_keywords(job, jd_md.read_text(encoding="utf-8") if jd_md.exists() else "")
            n0, n1 = kw_count(job), kw_count(new)
            print("\n".join(diff) or "  (no change)")
            print(f"terms {'/'.join(map(str, n0))} = {sum(n0)} → {'/'.join(map(str, n1))} = {sum(n1)}"
                  + ("" if jd_md.exists() else " · no jd.md next to the file: hyphen variants skipped"))
            if args.write and diff:
                new["_note"] = (job.get("_note", "") + f" | normalised {dt.date.today().isoformat()} by "
                                f"library_db.py normalize-keywords: {sum(n0)} → {sum(n1)} terms").lstrip(" |")
                args.path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"wrote {args.path}")
        elif args.cmd == "fit":
            f = fit_picks(con, picks_from_args(con, args))
            print_fit(f)
            return 0 if f["ok"] else 1
        elif args.cmd == "pick":
            r = suggest_picks(con, args.dir or recorded_dir(con, args.jd), args.archetype, args.cards)
            print(f"archetype: {r['archetype']} ({r['archetype_src']}) · cards {', '.join(r['cards'])} · "
                  f"{r['n_terms']} JD terms · {r['n_duties']} duty phrases")
            print(picks_to_spec(r["picks"]))
            print("\n".join(r["why"]))
            print_fit(r["fit"])
            return 0 if r["fit"]["ok"] else 1
        elif args.cmd == "ingest-ats":
            r = ingest_ats(con, args.jd, args.dir or recorded_dir(con, args.jd))
            print(f"{args.jd}: new {len(r['new'])} · updated {len(r['updated'])} · unclassified {r['unclassified']}"
                  + (f" · found but 'do not claim' (kept): {', '.join(r['conflicts'])}" if r["conflicts"] else ""))
            if r["new"]:
                print("new: " + " · ".join(r["new"]))
            if r["updated"]:
                print("updated: " + " · ".join(r["updated"]))
            if r.get("resolved"):
                print("concept assigned (rules 1–4): " + " · ".join(r["resolved"]))
            if r.get("unresolved"):
                aliases = alias_index(con)
                print("no concept — confirm with `set-concept \"<term>\" <CODE>` (or add a concept to the seed):")
                for t in r["unresolved"]:
                    cands = resolve_term(t, aliases)["cands"]
                    print(f"  {t}: " + (", ".join(f"{c} {s:.2f} ({al})" for c, s, al in cands) or "no candidate"))
        elif args.cmd == "set-coverage":
            print(set_coverage(con, args.term, args.coverage, args.note))
        elif args.cmd == "status":
            print_status(app_status(con))
        elif args.cmd == "export-md":
            args.out.write_text(export_md(con), encoding="utf-8")
            print(f"wrote {args.out}")
        elif args.cmd == "notion-payload":
            rows = (application_payload(con, args.jd) if args.table == "applications"
                    else notion_payload(con, args.table, args.only_new))
            if args.table == "applications" and args.only_new:
                rows = [r for r in rows if not r["notion_id"]]
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        elif args.cmd == "set-notion-id":
            key = NOTION_KEYS.get(args.table, "jd")
            with con:
                n = con.execute(f"UPDATE {args.table} SET notion_id=? WHERE {key}=?", (args.id, args.code)).rowcount
            if not n:
                raise PickError(f"no {args.table} row {args.code!r}")
            save_notion_ids(con)
            print(f"{args.table} {args.code} → {args.id}")
        elif args.cmd == "concepts":
            print_concepts(con, args.kind, args.claim, args.orphans)
        elif args.cmd == "concept":
            print_concept(con, args.code)
        elif args.cmd == "concepts-bootstrap":
            r = bootstrap_concepts(con, args.min_link)
            print_bootstrap(r)
            if args.out:
                args.out.write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                print(f"\nwrote {args.out}")
        elif args.cmd == "qa":
            if args.qcmd == "add":
                d = {k: getattr(args, k) for k in QA_FIELDS}
                if args.answer_file:
                    d["answer"] = args.answer_file.read_text(encoding="utf-8").strip()
                print(qa_add(con, d))
            elif args.qcmd == "import":
                rows = json.loads(args.path.read_text(encoding="utf-8"))
                if not isinstance(rows, list):
                    raise PickError("qa import expects a JSON list of objects")
                res = qa_import(con, rows, args.update, args.prune)
                print(f"imported {res['inserted']} · updated {res['updated']} · skipped {res['skipped']}"
                      + (f" · pruned {res['pruned']}" if args.prune else ""))
                if res["stale"]:
                    print(f"stale (in the DB, not in the file){'; generated ones pruned' if args.prune else ''}:")
                    for sid, sjd, ssrc, sq in res["stale"]:
                        gone = args.prune and ssrc == "generated"
                        print(f"  {sid} | {sjd} | {ssrc} | {sq[:80]}{' [deleted]' if gone else ''}")
            elif args.qcmd == "list":
                qa_list(con, args.jd, args.kind, args.concept, args.source, args.full)
            else:
                print(qa_set(con, args.id, outcome=args.outcome, notes=args.notes, answer=args.answer))
        elif args.cmd == "outreach":
            if args.ocmd == "add":
                print(outreach_add(con, {k: getattr(args, k) for k in
                                         ("jd", "person", "role", "channel", "kind", "status", "file", "notes")}))
            elif args.ocmd == "list":
                outreach_list(con, args.jd, args.status)
            else:
                print(outreach_set(con, args.id, args.status, args.sent, args.replied, args.notes))
        elif args.cmd == "selftest":
            return 0 if selftest(con) else 1
    except PickError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
