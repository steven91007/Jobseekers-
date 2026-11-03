"""Score + classify job postings against the candidate's real experience with one `codex exec` call per batch.

Input rows: [{company, title, url, jd?}] — `jd` = JD text; missing → the page is fetched (plain HTTP; a page that
blocks robots/needs login, e.g. LinkedIn, is not worked around: the row comes back with error "JD 無法取得").
Output per row: {url, company, title, type, score, band, reasons[≤3, 繁體中文], location{city,country,mode},
min_years, level_excluded, german_required, source, scored_at} or {url, error}.

Score = fit of the candidate's REAL experience (library cards/bullets/skills + confirmed facts) to the JD's
must-haves, not keyword count: 8–10 apply · 5–7 maybe · ≤4 skip. Cached by URL key in data/pipeline/scores.json.

    python3 score_jobs.py rows.json [--out scored.json] [--batch 5] [--dry-run] [--no-cache]

Env: CODEX_BIN (default `codex`), PIPELINE_DATA (default <repo>/data/pipeline), SCORE_MODEL (codex -m, optional).
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
LIBDB = REPO / ".claude" / "skills" / "tailor-cv" / "scripts" / "library_db.py"
DATA = Path(os.environ.get("PIPELINE_DATA") or REPO / "data" / "pipeline")
TYPES = ["AI agent", "ML learning", "automation"]
MODES = ["Hybrid", "Remote", "On-site", "未註明"]
JD_LIMIT = 12000

# Facts from cv/profile/candidate_confirmations.md that decide eligibility (not CV claims).
CANDIDATE_FACTS = """\
- 6 years of industry experience (Jan 2021 – present; CONF-i2). Current employer: Delta Electronics (Taiwan); earlier Mirle Automation.
- English C1 (confirmed). German: not confirmed (treat a German-language requirement as NOT met).
- Moves to Germany on 2027-01-01 (CONF-i4). Visa: no sponsorship needed for Germany, the Netherlands, Ireland; the United Kingdom requires sponsorship (CONF-i3).
- MaterialCheck: phase 1 in production, used by 200+ NPE and engineers (CONF-an); phases 2–3 not delivered."""

RUBRIC = """\
You score job postings for ONE candidate. Use ONLY the candidate profile below (project cards, bullets,
skills, confirmed facts). Do not assume skills that are not listed.

For each job:
1. List the JD's must-haves (required skills, years, domain, language, location/visa constraints).
2. For each must-have decide: proven by the profile (name the card/bullet code, e.g. DT1, PS2) / partly / not.
3. score (integer 1–10) = how well the candidate's REAL experience covers the must-haves:
   - 9–10: nearly every must-have is proven by production work on the cards; the core duty matches a card.
   - 8: core duty matches; at most one minor must-have only partly covered.
   - 5–7: some core must-haves proven, others missing or only adjacent (maybe apply).
   - 3–4: core duty is outside the profile (different stack/domain) or several hard must-haves missing.
   - 1–2: unrelated role, or a hard blocker (German required, sponsorship needed in a country that requires it
     when the JD says none is offered, location outside DE/NL/IE/UK without remote-from-there).
   Keyword overlap alone never raises the score. Required German (B2+/fluent) → score ≤ 3.
   Minimum years > 6 or a title level of Staff/Lead/Principal/Head/Director/VP/Manager → level_excluded=true, score ≤ 4.
4. type — the job's main work: "AI agent" (LLM agents, agentic workflows, RAG, tool calling, GenAI apps),
   "ML learning" (model training, computer vision, ML research/engineering, MLOps for models),
   "automation" (Python backend/integration, workflow/RPA, platform/DevOps, data pipelines).
5. reasons: up to 3 short bullets in Traditional Chinese (繁體中文), each naming a concrete match or gap
   (e.g. "LangGraph 採購信件 agent(DT1)直接對應 agent 開發職責", "要求 Kubernetes 生產經驗,履歷沒有"). Keep
   English tool/job names in English.
6. location: city in English as the JD writes it (e.g. "Berlin", "Munich"), country in Traditional Chinese (德國/荷蘭/英國/愛爾蘭/其他國家名), mode one of
   Hybrid / Remote / On-site / 未註明.
7. min_years: the minimum years the JD requires (integer) or null if not stated.
8. german_required: true only if German is required (not "nice to have").
9. company, title: as the JD states them (English, strip "(m/w/d)"); echo the given values when present.

Return JSON matching the schema: {"jobs": [one object per job, same order, echo the job's id]}.
"""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["jobs"],
    "properties": {"jobs": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "company", "title", "type", "score", "reasons", "location", "min_years", "level_excluded", "german_required"],
        "properties": {
            "id": {"type": "string"},
            "company": {"type": "string"},
            "title": {"type": "string"},
            "type": {"type": "string", "enum": TYPES},
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "location": {"type": "object", "additionalProperties": False, "required": ["city", "country", "mode"],
                         "properties": {"city": {"type": "string"}, "country": {"type": "string"},
                                        "mode": {"type": "string", "enum": MODES}}},
            "min_years": {"type": ["integer", "null"]},
            "level_excluded": {"type": "boolean"},
            "german_required": {"type": "boolean"},
        }}}},
}


def url_key(u):
    """Port of apps_script.gs urlKey_ (LinkedIn job id, else host+path without query)."""
    s = str(u or "").strip()
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d+)", s) or re.search(r"linkedin\.com/jobs/.*[?&]currentJobId=(\d+)", s)
    if m:
        return "li:" + m.group(1)
    s = s.split("#")[0].split("?")[0]
    return re.sub(r"^https?://(www\.)?", "", s, flags=re.I).rstrip("/").lower()


def band(score):
    return "apply" if score >= 8 else "maybe" if score >= 5 else "skip"


def sha(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def now():
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


# ---- JD text ----------------------------------------------------------------

def html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def fetch_jd(url, timeout=30):
    """Plain GET. Returns text or None (blocked / login wall / too short)."""
    if re.search(r"linkedin\.com", url or "", re.I):
        return None  # LinkedIn blocks fetching; the skill's rule is not to work around it
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (job-pipeline; personal use)",
                                               "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return None
    text = html_to_text(raw)
    return text[:JD_LIMIT] if len(text) >= 400 else None


# ---- candidate profile ------------------------------------------------------

def libdb(*args):
    p = subprocess.run([sys.executable, str(LIBDB), *args], capture_output=True, text=True, cwd=REPO, timeout=120)
    if p.returncode:
        raise RuntimeError(f"library_db.py {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


def build_profile():
    cards = libdb("cards")
    codes = [ln.split(" · ", 1)[0].strip() for ln in cards.splitlines() if " · " in ln]
    detail = "\n\n".join(libdb("card", c) for c in codes)
    skills = "\n".join(ln for ln in libdb("skills", "--rows").splitlines() if ln.startswith("**"))
    seed = REPO / "cv" / "profile" / "library_seed.json"
    facts = json.loads(seed.read_text(encoding="utf-8")).get("facts", []) if seed.exists() else []
    return "\n\n".join([
        "## Confirmed facts\n" + CANDIDATE_FACTS + "\n" + "\n".join("- " + f for f in facts),
        "## Project cards (summary)\n" + cards,
        "## Project cards with bullets\n" + detail,
        "## Skills rows\n" + skills,
    ])


# ---- scoring ----------------------------------------------------------------

def prompt_for(batch, profile):
    jobs = "\n\n".join(
        f"### Job id={j['_id']}\nCompany: {j.get('company', '')}\nTitle: {j.get('title', '')}\nURL: {j.get('url', '')}\n"
        f"JD text:\n{(j.get('jd') or '')[:JD_LIMIT]}" for j in batch)
    return f"{RUBRIC}\n# Candidate profile\n{profile}\n\n# Jobs ({len(batch)})\n{jobs}\n"


def run_codex(prompt, timeout=900):
    codex = os.environ.get("CODEX_BIN", "codex")
    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "schema.json"
        out = Path(tmp) / "out.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        cmd = [codex, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
               "--output-schema", str(schema), "-o", str(out), "-C", tmp]
        if os.environ.get("SCORE_MODEL"):
            cmd += ["-m", os.environ["SCORE_MODEL"]]
        p = subprocess.run(cmd + ["-"], input=prompt, capture_output=True, text=True, timeout=timeout, cwd=tmp)
        if p.returncode or not out.exists():
            raise RuntimeError(f"codex exec failed ({p.returncode}): {(p.stderr or p.stdout).strip()[-400:]}")
        text = out.read_text(encoding="utf-8").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("codex returned no JSON: " + text[:200])
    return json.loads(m.group(0))


def fake_score(job):
    """Dry-run: deterministic score from the URL, type from title words. No model call."""
    h = int(hashlib.sha1(job["url"].encode()).hexdigest(), 16)
    t = (job.get("title", "") + " " + (job.get("jd") or "")[:500]).lower()
    typ = "AI agent" if re.search(r"agent|llm|genai|gen ai|rag", t) else \
        "ML learning" if re.search(r"machine learning|\bml\b|vision|mlops|data scien", t) else "automation"
    return {"company": job.get("company") or "(dry-run) Company", "title": job.get("title") or "(dry-run) Title",
            "type": typ, "score": 3 + h % 7, "reasons": ["(dry-run) 假評分,未呼叫 Codex"],
            "location": {"city": job.get("city", ""), "country": job.get("country", ""), "mode": "未註明"},
            "min_years": None, "level_excluded": False, "german_required": False}


def finish(job, res, source):
    score = max(1, min(10, int(res.get("score") or 1)))
    reasons = [str(r).strip() for r in (res.get("reasons") or []) if str(r).strip()][:3]
    if res.get("level_excluded") or (res.get("min_years") or 0) > 6:
        if score > 4:
            score = 4
            reasons = (reasons[:2] + ["職級或年資超出條件(>6 年或 Staff/Lead 級)"])
    if res.get("german_required") and score > 3:
        score = 3
        reasons = reasons[:2] + ["要求德語"]
    loc = res.get("location") or {}
    mode = loc.get("mode") if loc.get("mode") in MODES else "未註明"
    return {"url": job["url"], "company": job.get("company") or res.get("company", ""),
            "title": job.get("title") or res.get("title", ""),
            "type": res.get("type") if res.get("type") in TYPES else "automation",
            "score": score, "band": band(score), "reasons": reasons,
            "location": {"city": loc.get("city") or job.get("city", ""), "country": loc.get("country") or job.get("country", ""),
                         "mode": mode},
            "min_years": res.get("min_years"), "level_excluded": bool(res.get("level_excluded")),
            "german_required": bool(res.get("german_required")),
            "source": source, "scored_at": now(), "jd_sha": sha(job.get("jd"))}


def load_cache(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_cache(path, cache):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def score_jobs(rows, dry_run=False, batch_size=5, use_cache=True, cache_path=None, fetch=fetch_jd, log=print):
    """Return one result per input row (same order). Cache hits skip the model call."""
    cache_path = Path(cache_path or DATA / "scores.json")
    cache = load_cache(cache_path)
    results = {}
    todo = []
    for i, row in enumerate(rows):
        job = dict(row)
        job["url"] = str(job.get("url") or "").strip()
        if not re.match(r"https?://", job["url"]):
            results[i] = {"url": job["url"], "error": "沒有職缺連結"}
            continue
        hit = cache.get(url_key(job["url"]))
        if use_cache and hit and hit.get("source") != "dry-run" and (not job.get("jd") or hit.get("jd_sha") == sha(job["jd"])):
            results[i] = {**hit, "cached": True}
            continue
        if not job.get("jd"):
            job["jd"] = fetch(job["url"]) if fetch else None
        if not job.get("jd"):
            results[i] = {"url": job["url"], "error": "JD 無法取得(頁面擋抓取或需登入)"}
            continue
        job["_id"] = str(i)
        todo.append((i, job))
    profile = None
    for b in range(0, len(todo), batch_size):
        chunk = todo[b:b + batch_size]
        if dry_run:
            out = {str(i): fake_score(j) for i, j in chunk}
            source = "dry-run"
        else:
            profile = profile or build_profile()
            try:
                got = run_codex(prompt_for([j for _, j in chunk], profile))
                out = {str(x.get("id")): x for x in got.get("jobs", [])}
            except Exception as e:  # one failed batch never stops the run
                log(f"score batch failed: {e}")
                out = {}
            source = "codex"
        for i, job in chunk:
            res = out.get(str(i))
            if not res:
                results[i] = {"url": job["url"], "error": "評分失敗(Codex 沒有回傳這筆)"}
                continue
            results[i] = finish(job, res, source)
            if source != "dry-run":  # a dry run never touches the real cache
                cache[url_key(job["url"])] = results[i]
        if not dry_run:
            save_cache(cache_path, cache)
    return [results[i] for i in range(len(rows))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rows")
    ap.add_argument("--out")
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--print-prompt", action="store_true", help="print the prompt for the first batch and exit")
    a = ap.parse_args()
    rows = json.loads(Path(a.rows).read_text(encoding="utf-8"))
    if a.print_prompt:
        print(prompt_for([{**r, "_id": str(i)} for i, r in enumerate(rows[:a.batch])], build_profile()))
        return
    res = score_jobs(rows, dry_run=a.dry_run, batch_size=a.batch, use_cache=not a.no_cache,
                     log=lambda m: print(m, file=sys.stderr))
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
