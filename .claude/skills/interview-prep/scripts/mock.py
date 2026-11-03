#!/usr/bin/env python3
"""mock.py - mock-interview sessions for /interview-prep (stdlib; DB read-only).

  start   --jd <Co> [--stage mixed|hr|tech|hm] [--n 5] [--kinds k1,k2] [--followups 2] [--out-dir D]
                                   pick questions from $D/10_interview.json (reported first, then never practised,
                                   then lowest score; kinds round-robin), write a session file, print the
                                   interviewer brief (for the agent only: it holds the reference answers)
  answer  <session> --q N --file turn.json    store the candidate's answer + follow-ups, print the auto checks
  score   <session> --q N --file score.json   store rubric scores, feedback and a better answer (truth-checked)
  brief   <session> [--json]                  the interviewer brief again (JSON for the dashboard)
  skip    <session> --q N                     mark a question skipped
  status  <session>                           done / skipped / next
  report  <session>                           -> <session>.md (scores, weakest dimension, claims to verify)
  history --jd <Co> [--out-dir D]             per question: attempts, last and best score over all sessions
  selftest                                    end-to-end on the TOMRA prep file in a temp dir

turn.json  = {"answer": "...", "followups": [{"q": "...", "a": "..."}]}
score.json = {"scores": {<rubric key>: 1-5, ...}, "feedback": "...", "better": "...",
              "better_bullets": ["PS4"], "notes": "CONF-o2", "honesty_note": "..."}

Auto checks never decide a score; they flag what the agent must look at: numbers the candidate said that are in no
library bullet, card, CONF item or the CV ("unverified"), sentences that name a gap / do-not-claim term without
saying it is missing ("gap claim"), MaterialCheck phases 2-3 as delivered, target concepts not mentioned, length,
I/we balance. A `better` answer is held to the same truth check as /interview-prep answers.
"""
import argparse, datetime as dt, glob, json, os, re, statistics, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import interview_prep as ip  # noqa: E402
import truth  # noqa: E402
from truth import (toks, word_toks, fmt, numbers, conf_items, conf_refs, corpus_for, check_numbers, mc_phase_claims,  # noqa: E402
                   sentences, SKIP_MARK)

MOCK_DIR = "12_mock"
STAGE_KINDS = {
    "mixed": ["intro", "behavioural", "project", "technical", "system-design", "gap", "motivation"],
    "hr": ["intro", "motivation", "behavioural", "gap"],
    "tech": ["technical", "system-design", "project", "gap"],
    "hm": ["intro", "project", "behavioural", "system-design", "gap", "motivation"],
}
OPENERS_FIRST = {"hr"}   # these stages ask every intro question before anything else

# Answer frameworks: each is a list of chunks (label, what the chunk must do). The scorer maps the candidate's
# answer onto the chunks and writes the stronger answer chunk by chunk, so the candidate learns the shape.
FRAMEWORKS = {
    "PPF": {"name": "Present → Past → Future", "chunks": [
        ("Present", "現在的角色與你負責的範圍,一句話定位自己"),
        ("Past", "1–2 個最相關的證明(專案、數字),連到這個職缺"),
        ("Future", "為什麼想要這個職位/公司,下一步想做什麼")]},
    "STAR": {"name": "STAR", "chunks": [
        ("Situation", "一兩句背景:哪個專案、誰在用、限制是什麼"),
        ("Task", "你負責的目標或問題(你的,不是團隊的)"),
        ("Action", "你做了哪些決定和動作,用 I,講取捨"),
        ("Result", "結果與怎麼量的;沒有數字就說清楚狀態")]},
    "CAR": {"name": "Context → Action → Result", "chunks": [
        ("Context", "專案和問題,一兩句"),
        ("Action", "你的設計與關鍵決定,為什麼這樣做"),
        ("Result", "結果、指標定義、學到什麼")]},
    "PREP": {"name": "PREP", "chunks": [
        ("Point", "第一句就回答問題(結論先講)"),
        ("Reason", "為什麼:原則或取捨"),
        ("Example", "你做過的具體例子(引用你的經驗)"),
        ("Point", "收尾,再講一次結論並連回這個職缺")]},
    "DESIGN": {"name": "Requirements → Design → Trade-offs → Operate", "chunks": [
        ("Requirements", "先確認需求與限制(資料量、延遲、on-prem/edge)"),
        ("Design", "主要元件與資料流,從你做過的架構出發"),
        ("Trade-offs", "選擇的取捨與替代方案"),
        ("Operate", "監控、回滾、擴展:上線後怎麼維運")]},
    "GAP": {"name": "Honest gap → Closest experience → Plan", "chunks": [
        ("Gap", "直接承認沒做過什麼(點名技術)"),
        ("Closest", "最接近的實際經驗,可以帶過去的部分"),
        ("Plan", "具體怎麼補:第一個月做什麼")]},
}
KIND_FRAMEWORKS = {"intro": ["PPF", "STAR"], "behavioural": ["STAR"], "project": ["STAR", "CAR"],
                   "technical": ["PREP"], "system-design": ["DESIGN"], "gap": ["GAP"], "motivation": ["PREP", "PPF"]}


# Interviewer roles. Each question is asked, followed up and scored from one role's point of view; the agent (chat
# mode) and the dashboard (server/mock.ts) build their prompts from these texts, so the three roles stay consistent.
ROLES = {
    "hr": {
        "name": "HR / Recruiter",
        "zh": "HR 面試官",
        "goal": "Decide whether the candidate is a credible, motivated fit worth passing to the hiring manager, and "
                "whether there are red flags (motivation, teamwork, logistics, consistency with the CV).",
        "listens_for": "plain language a non-engineer could repeat to the hiring manager; who used the work and what "
                       "got better for them; teamwork and how the candidate works with people; why this company and "
                       "why now; clear logistics (location, start date, salary, visa) without hedging",
        "followups": "clarify in plain terms ('What did that change for the people using it?'), ask about the "
                     "candidate's part in the team, motivation, or logistics. Never ask a deep technical question.",
        "rubric": {
            "structure": "answers the question in the first sentence; a short, easy story (PPF / STAR / PREP)",
            "evidence": "concrete outcomes in human terms: who used it, time saved, what improved; not a tool list",
            "relevance": "ties the answer to this role and company; shows motivation",
            "honesty": "consistent with the CV; gaps and logistics stated plainly",
            "clarity": "a non-engineer understands it: at most 2 technical terms, each explained in a few plain words; "
                       "30-90 s",
            "followups": "answers the clarification simply and directly",
        },
        "feedback": "Coach for an HR listener: flag every unexplained technical term or acronym (quote it and give a "
                    "plain-English replacement), lists of tools, missing 'who benefited', unclear motivation or logistics.",
        "better": "Plain spoken English for a non-engineer. Outcome and people first, at most 2 technical terms, each "
                  "explained in a few words (e.g. 'a model registry, a catalogue of approved model versions'). No "
                  "tool lists.",
    },
    "hm": {
        "name": "Hiring manager",
        "zh": "Hiring manager",
        "goal": "Decide whether the candidate can own problems in this team: sound decisions, delivery, working with "
                "stakeholders, and impact that matters to the team's goals in the JD.",
        "listens_for": "WHY behind every decision (problem → options → choice → trade-off); what the candidate owned vs "
                       "the team; impact on users or the business and how it was measured; prioritisation and risk "
                       "handling; what they learned or would do differently",
        "followups": "ask for the reason or the alternative ('Why X and not Y?', 'What would have happened otherwise?'), "
                     "the cost or risk, how priorities were set, how success was measured.",
        "rubric": {
            "structure": "problem → decision → reason → result is easy to follow (STAR / CAR / PREP)",
            "evidence": "the candidate's own decisions with their reasons, and a measured outcome",
            "relevance": "maps to the problems this team has according to the JD",
            "honesty": "own vs team work is clear; limits and unfinished parts stated",
            "clarity": "technical terms named but not lectured; the decision logic is the focus; 45-120 s",
            "followups": "gives the reason or the trade-off directly when asked 'why'",
        },
        "feedback": "Coach for a hiring manager: flag every decision stated without its reason, missing alternatives "
                    "or trade-offs, unclear ownership ('we'), impact without how it was measured, no link to the team's "
                    "needs.",
        "better": "Spoken English for a hiring manager: pair each decision with its reason ('I chose X because Y'), say "
                  "the trade-off, end with the impact and how it was measured. Name tools, do not explain internals.",
    },
    "tech": {
        "name": "Engineer (technical interviewer)",
        "zh": "工程師面試官",
        "goal": "Decide whether the candidate really understands and built what they claim, and would be productive on "
                "this stack.",
        "listens_for": "precise mechanisms and correct terminology; how metrics are defined and measured; failure modes, "
                       "monitoring, testing; trade-offs against alternatives; scaling; honest limits",
        "followups": "probe one level deeper ('How exactly…?', 'What breaks at 10× load?', 'How was that measured?', "
                     "'Why not <alternative tool>?').",
        "rubric": {
            "structure": "direct answer, then mechanism, then trade-off (PREP / DESIGN / STAR)",
            "evidence": "concrete mechanisms, tools, configurations and numbers the candidate actually used",
            "relevance": "covers the concepts the question targets on this stack",
            "honesty": "admits what was not done; no hand-waving past a gap",
            "clarity": "precise and correct terms; no vague words ('optimized', 'handled') without the how",
            "followups": "goes deeper correctly when probed",
        },
        "feedback": "Coach for an engineer: flag vague verbs without mechanism, metrics without definition, missing "
                    "failure modes / monitoring / testing, wrong or loose terminology, missing trade-offs.",
        "better": "Precise technical spoken English: mechanism, metric definition, trade-off and failure handling, using "
                  "the exact tools and numbers from the evidence.",
    },
}
MIXED_ROLE = {"intro": "hr", "motivation": "hr", "behavioural": "hm", "project": "hm", "gap": "hm",
              "technical": "tech", "system-design": "tech"}


def role_for(stage, kind):
    return stage if stage in ROLES else MIXED_ROLE.get(kind, "hm")


def role_view(key):
    r = ROLES[key]
    return {"key": key, "name": r["name"], "zh": r["zh"], "goal": r["goal"], "listens_for": r["listens_for"],
            "followups": r["followups"], "rubric": r["rubric"], "feedback": r["feedback"], "better": r["better"]}


# technical terms an HR listener would not know: acronyms, CamelCase / mixed-case tool names, and a few common words
JARGON_WORDS = {"pipeline", "pipelines", "registry", "inference", "containerization", "kubernetes", "docker", "repo",
                "microservice", "microservices", "endpoint", "endpoints", "latency", "throughput", "deployment",
                "post-processing", "fine-tuning", "lora", "on-prem", "air-gapped", "staging", "api", "apis"}
JARGON_OK = {"I", "OK", "CV", "HR", "AI", "IT", "UK", "US", "EU", "QA"}


def jargon_terms(text):
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9+.#-]*[A-Za-z0-9+#]|[A-Za-z]", text):
        w0 = w.strip(".-")
        if w0 in JARGON_OK:
            continue
        if (re.fullmatch(r"[A-Z0-9]{2,}s?", w0) and re.search(r"[A-Z]", w0)) or re.search(r"[a-z][A-Z]|^[A-Z]{2,}[a-z]", w0) \
                or w0.lower() in JARGON_WORDS:
            out.append(w0)
    return list(dict.fromkeys(out))


def frameworks_for(it):
    """Allowed framework keys for an item, the default first ('Tell me about yourself' → PPF, other intro → STAR)."""
    keys = list(KIND_FRAMEWORKS.get(it["kind"], ["PREP"]))
    if it["kind"] == "intro" and not re.search(r"\byourself\b|\bintroduc", it["question"], re.I):
        keys = ["STAR", "PPF"]
    return keys


def framework_view(key):
    f = FRAMEWORKS[key]
    return {"key": key, "name": f["name"], "chunks": [{"label": l, "goal": g} for l, g in f["chunks"]]}
RUBRIC = {
    "structure": "answers the question first, then a clear shape: STAR for behavioural; context -> decision -> "
                 "trade-off -> result for project/technical/system-design",
    "evidence": "concrete own actions ('I'), real numbers, tools and names that the library holds",
    "relevance": "covers the concepts the question targets and ties back to this role",
    "honesty": "no overclaim: gaps said plainly, staging is not production, only what survives a follow-up",
    "clarity": "spoken length about 30-90 s, plain English, no rambling or filler",
    "followups": "follow-ups answered directly and consistently with the first answer; limits admitted",
}
SEEDS = {
    "intro": ["Which of those would you say is your strongest skill?", "Why are you looking for something new now?",
              "What exactly was your part in that project?"],
    "behavioural": ["What exactly was your part, and what did the team do?", "How did you know it worked? What did you measure?",
                    "What would you do differently now?"],
    "project": ["Why this design and not <the obvious alternative>?", "What broke first, and how did you find out?",
                "How is <the headline metric> defined and measured?"],
    "technical": ["What is the trade-off?", "How does it fail, and how would you notice?", "Where would it not fit?"],
    "system-design": ["Where is the bottleneck at 10x load?", "How do you monitor it and roll back a bad model?",
                      "What do you build first, what later?"],
    "gap": ["How long until you are productive with <term>?", "What is the closest thing you have actually done?",
            "How would you learn it in the first month?"],
    "motivation": ["Why leave now?", "What do you know about our product?", "What if the role turns out to be mostly <X>?"],
}
# a gap term is "claimed" unless a negation / hypothetical sits just before it ("haven't used Jetson", "would learn
# CUDA") or a missing-marker just after it ("Jetson is a gap for me"); a "no" elsewhere in the sentence does not count
NEG_BEFORE = re.compile(r"\b(?:not|never|no|without|lack|lacking|haven't|hasn't|didn't|don't|cannot|can't|learn|learning|"
                        r"would|could|want|plan|planning|hope|aim|yet|outside|beyond)\b|n't\b", re.I)
NEG_AFTER = re.compile(r"^\W*(?:\w+\W+){0,4}?(?:is|are|was|remains?)\s+(?:a\s+)?(?:gap|missing|new to me|not something)|"
                       r"^\W*(?:\w+\W+){0,3}?(?:experience\s+)?(?:i\s+)?(?:have not|haven't|lack|don't have)", re.I)
NEG_WINDOW = 5
# ambiguous one-word gap terms only count in their technical sense
AMBIG = {"edge": r"\bedge\s+(?:device|devices|deployment|hardware|inference|computing|ai|box|boxes|gpu|gpus)\b|\b(?:on|at|to) the edge\b",
         "cloud": r"\bcloud\s+(?:deployment|infrastructure|platform|native|services?|cluster|training|hosting|account)\b|"
                  r"\b(?:to|on|in|into) the cloud\b|\b(?:aws|azure|gcp|google cloud)\b"}
WPM = 140


def now():
    return dt.datetime.now().replace(microsecond=0)


def load(p):
    return json.load(open(p, encoding="utf-8"))


def save(p, data):
    tmp = p + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def mock_dir(jd, out_dir=None):
    if out_dir:
        return out_dir
    return os.path.join(ip.app(jd)[3], MOCK_DIR)


def overall(scores):
    vals = [v for k, v in (scores or {}).items() if k in RUBRIC]
    return round(statistics.mean(vals), 2) if vals else None


# ---------------------------------------------------------------- history
def sessions(d):
    out = []
    for p in sorted(glob.glob(os.path.join(d, "mock_*.json"))):
        try:
            out.append((p, load(p)))
        except Exception:
            continue
    return out


def history(d):
    """question -> list of (started, overall) for scored items, oldest first."""
    h = {}
    for _p, s in sessions(d):
        for it in s.get("items", []):
            for att in (it.get("past") or []) + [it]:
                o = overall(att.get("scores"))
                if o is not None:
                    h.setdefault(it["question"], []).append((att.get("scored") or s.get("started", ""), o))
    for v in h.values():
        v.sort()
    return h


# ---------------------------------------------------------------- start
def pick(items, kinds, n, hist, first_intro=False):
    by = {k: [] for k in kinds}
    for i, q in enumerate(items):
        k = ip.classify(q)
        if k in by:
            h = hist.get(q["question"])
            opener = k == "intro" and re.search(r"\byourself\b|\bintroduc", q["question"], re.I)
            key = (0 if opener else 1, 0 if ip.csv(q.get("reported")) else 1, 1 if h else 0, h[-1][1] if h else 0, i)
            by[k].append((key, i))
    for k in by:
        by[k].sort()
    chosen = []
    if "intro" in by and first_intro:
        while by["intro"] and len(chosen) < n:
            chosen.append(by["intro"].pop(0)[1])
    while len(chosen) < n and any(by.values()):
        for k in kinds:
            if by[k] and len(chosen) < n:
                chosen.append(by[k].pop(0)[1])
    return chosen


def new_session(jd, stage="mixed", n=5, kinds=None, followups=2, out_dir=None):
    jd, company, title, d = ip.app(jd)
    prep = os.path.join(d, "10_interview.json")
    if not os.path.exists(prep):
        sys.exit(f"no {prep}: run /interview-prep for {jd} first (the mock asks the prepared questions)")
    items = load(prep)
    kinds = kinds or STAGE_KINDS[stage]
    md = mock_dir(jd, out_dir)
    os.makedirs(md, exist_ok=True)
    idx = pick(items, kinds, n, history(md), stage in OPENERS_FIRST)
    if not idx:
        sys.exit(f"no prepared questions of kinds {kinds}")
    sess = {"jd": jd, "company": company, "title": title, "stage": stage, "followups": followups,
            "started": now().isoformat(), "prep": prep, "rubric": RUBRIC, "items": []}
    for n_, i in enumerate(idx, 1):
        q = items[i]
        sess["items"].append({"n": n_, "ref": i, "question": q["question"], "kind": ip.classify(q),
                              "concepts": ip.csv(q["concepts"]), "bullets": ip.csv(q["bullets"]),
                              "reported": ip.csv(q.get("reported")), "notes": q.get("notes") or "",
                              "reference": q["answer"], "attempts": 0, "answer": None, "followups": [],
                              "auto": None, "scores": None, "feedback": "", "better": ""})
        sess["items"][-1]["frameworks"] = [framework_view(k) for k in frameworks_for(sess["items"][-1])]
        sess["items"][-1]["role"] = role_for(stage, sess["items"][-1]["kind"])
    path = os.path.join(md, f"mock_{now().strftime('%Y%m%d_%H%M%S')}.json")
    while os.path.exists(path):
        path = path[:-5] + "_1.json"
    save(path, sess)
    return path, sess


def item_role(sess, it):
    return it.get("role") or role_for(sess.get("stage"), it["kind"])


def brief_data(sess):
    """Per-question interviewer brief as data (the dashboard's mock page uses it server-side; reference answers
    never go to the browser before the question is scored)."""
    con = ip.db()
    cname = {r[0]: r[1] for r in con.execute("select code,name from concepts")}
    cj = ip.company_json(sess["jd"]) or {}
    reps = {r.get("id"): r for r in cj.get("interview_reports") or []}
    gaps = ip.gap_sections(ip.lib("gaps", "--jd", sess["jd"])[1])
    cv = ip.read(os.path.join(ip.app(sess["jd"])[3], "03_cv.md"))
    m = re.search(r"\*\*PROFESSIONAL PROFILE\*\*(.*?)(?=\*\*EDUCATION\*\*|\Z)", cv, re.S)
    cv_body = re.sub(r"\*\*", "", (m.group(1) if m else cv)).strip()[:3500]
    items = []
    for it in sess["items"]:
        ev = []
        for b in it["bullets"]:
            row = con.execute("select body,metric from bullets where code=?", (b,)).fetchone()
            if row:
                ev.append(f"{b}: {row[0]}" + (f" | metric: {row[1]}" if row[1] else ""))
        items.append({"n": it["n"], "question": it["question"], "kind": it["kind"],
                      "targets": [f"{c} ({cname.get(c, c)})" for c in it["concepts"]], "evidence": ev,
                      "reported": [f"{r}: {reps.get(r, {}).get('stage', '?')} · {reps.get(r, {}).get('date', '?')}" for r in it["reported"]],
                      "seeds": SEEDS.get(it["kind"], []), "reference": it["reference"], "notes": it.get("notes") or "",
                      "frameworks": [framework_view(k) for k in frameworks_for(it)],
                      "role": role_view(item_role(sess, it)),
                      "cv": cv_body if it["kind"] == "intro" else ""})
    return {"jd": sess["jd"], "company": sess["company"], "title": sess["title"], "stage": sess["stage"],
            "followups": sess["followups"], "rubric": RUBRIC,
            "process": [p["text"] for p in cj.get("interview_process") or []],
            "gap_terms": sorted({t for g in gaps for t in g[2]}), "items": items}


def brief(sess):
    con = ip.db()
    cname = {r[0]: r[1] for r in con.execute("select code,name from concepts")}
    cj = ip.company_json(sess["jd"]) or {}
    reps = {r.get("id"): r for r in cj.get("interview_reports") or []}
    gaps = ip.gap_sections(ip.lib("gaps", "--jd", sess["jd"])[1])
    o = [f"# Mock interview brief: {sess['company']} · {sess['title']} ({sess['jd']}) · stage {sess['stage']}",
         "FOR THE INTERVIEWER AGENT ONLY. Never show a reference answer before the candidate has answered and been scored.",
         f"Persona: the {sess['stage'] if sess['stage'] != 'mixed' else 'hiring manager + engineer'} interviewer for this role. "
         f"Ask one question at a time, in English; then up to {sess['followups']} follow-ups, one at a time, aimed where "
         "the answer was vague, claimed more than the evidence, or skipped how a metric was measured.", ""]
    for p in cj.get("interview_process") or []:
        o.append(f"Reported process: {p['text']}")
    gap_terms = sorted({t for g in gaps for t in g[2]})
    if gap_terms:
        o.append("Gap / do-not-claim terms (probe, never let the candidate claim): " + ", ".join(gap_terms[:25]))
    o.append("")
    for it in sess["items"]:
        o.append(f"## Q{it['n']} [{it['kind']} · asked by {ROLES[item_role(sess, it)]['name']}] {it['question']}")
        o.append(f"- role listens for: {ROLES[item_role(sess, it)]['listens_for']}")
        for r in it["reported"]:
            rr = reps.get(r, {})
            o.append(f"- reported {r}: {rr.get('stage', '?')} · {rr.get('role', '?')} · {rr.get('date', '?')}")
        if it["concepts"]:
            o.append("- targets: " + ", ".join(f"{c} ({cname.get(c, c)})" for c in it["concepts"]))
        for b in it["bullets"]:
            row = con.execute("select body,metric from bullets where code=?", (b,)).fetchone()
            if row:
                o.append(f"- evidence {b}: {row[0]}" + (f" | metric: {row[1]}" if row[1] else ""))
        o.append("- follow-up seeds: " + " / ".join(SEEDS.get(it["kind"], [])))
        fk = frameworks_for(it)[0]
        o.append(f"- framework: {FRAMEWORKS[fk]['name']} (" + " → ".join(l for l, _ in FRAMEWORKS[fk]["chunks"]) + ")")
        o.append(f"- reference answer (hidden): {it['reference']}")
        o.append("")
    o.append("Rubric (1-5 each): " + " · ".join(f"{k} = {v}" for k, v in RUBRIC.items()))
    return "\n".join(o)


# ---------------------------------------------------------------- auto checks
def library_text(con, cv):
    parts = [cv]
    for b, m in con.execute("select body,metric from bullets"):
        parts += [b or "", m or ""]
    parts += [r[0] or "" for r in con.execute("select text from project_contexts")]
    parts += [r[0] or "" for r in con.execute("select metrics from projects")]
    conf, skipped, _ = conf_items()
    for ref, texts in conf.items():
        if ref in skipped:
            continue
        parts += [x for x in texts if not SKIP_MARK.search(x)]
    return "\n".join(parts)


def gap_terms_for(jd):
    out = set()
    for code, name, terms, sec in ip.gap_sections(ip.lib("gaps", "--jd", jd)[1]):
        out |= {t.lower() for t in terms if t}
        if sec != "tnc":
            out.add(name.lower())
    return {t for t in out if len(t) > 2}


def concept_terms(con, code):
    row = con.execute("select name, canonical from concepts where code=?", (code,)).fetchone()
    terms = {x.lower() for x in (row or ()) if x}
    terms |= {r[0].lower() for r in con.execute("select alias from concept_aliases where concept_code=?", (code,))}
    return {t for t in terms if len(t) > 2}


def has(term, low):
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", low) is not None


def gap_claims(text, terms):
    out = []
    for sent in re.split(r"(?<=[.!?])\s+|\n", text):
        hit = []
        for g in sorted(terms):
            pat = AMBIG.get(g) or (r"(?<![a-z0-9])" + re.escape(g) + r"(?![a-z0-9])")
            for m in re.finditer(pat, sent, re.I):
                before = " ".join(re.findall(r"[\w']+", sent[:m.start()])[-NEG_WINDOW:])
                if not NEG_BEFORE.search(before) and not NEG_AFTER.search(sent[m.end():]):
                    hit.append(g)
                    break
        if hit:
            out.append(f"{', '.join(hit)}: \"{sent.strip()[:140]}\"")
    return out


def profile_text():
    return re.sub(r"<!--.*?-->", "", ip.read(ip.PROFILE), flags=re.S)


def evidence(sess, it, extra_bullets=(), con=None):
    """-> (hard corpus, company corpus, company names) for one item. Hard = the cited bullets (body, metric, card
    contexts, the CV line carrying the bullet) + the profile for motivation; never the question (it may come from a
    public review) nor the whole CV (its bare numbers and years would verify anything)."""
    con = con or ip.db()
    d = ip.app(sess["jd"])[3]
    cv = ip.read(os.path.join(d, "03_cv.md"))
    bs = list(dict.fromkeys(list(it["bullets"]) + list(extra_bullets)))
    hard = "\n".join(corpus_for(con, b, cv) for b in bs)
    ctext = names = ""
    if it["kind"] == "intro":   # a self-introduction summarises the submitted CV
        hard += "\n" + cv + "\n" + profile_text()
    if it["kind"] in ("motivation", "ask"):
        hard += "\n" + profile_text()
        ctext = (ip.company_text(sess["jd"]) + "\n" + ip.read(os.path.join(d, "jd.md"))).strip()
        names = truth.company_names(sess.get("company") or "", ctext, (ip.company_json(sess["jd"]) or {}).get("names") or ())
    return hard, ctext, names, cv


def classify_numbers(text, hard, ctext, names, lib, conf_ok):
    """-> (unverified, library_only). Bare numbers must be in the cited evidence; a JD/company figure passes only in a
    sentence about the company (truth.company_number_ok)."""
    allowed = numbers(hard) | conf_ok
    lib_nums = numbers(lib)
    unverified, library_only = [], []
    for tok in list(dict.fromkeys(toks(text))) + list(dict.fromkeys(word_toks(text))):
        if tok in allowed:
            continue
        if ctext and tok in numbers(ctext) and any(truth.company_number_ok(x, tok, ctext, names, "first")[0]
                                                   for x in sentences(text) if tok in numbers(x)):
            continue
        label = fmt(tok) if tok in toks(text) else f"{fmt(tok)} (number word)"
        # the same value elsewhere in the library counts only next to a shared word ("70% OCR accuracy" does not
        # back "rollback time by 70%")
        near = tok[1] and tok in lib_nums and any(
            len(truth.context_words(x, tok) & truth.context_words(lib, tok)) >= 2 for x in sentences(text) if tok in numbers(x))
        (library_only if near else unverified).append(label)
    return unverified, library_only


def conf_numbers(notes):
    conf, skipped, _ = conf_items()
    out = set()
    for ref in conf_refs(notes):
        if ref in skipped:
            continue
        texts = conf.get(ref) if re.search(r"\d", ref) else [x for k, v in conf.items() if re.fullmatch(re.escape(ref) + r"\d+", k) for x in v]
        for x in texts or []:
            if not SKIP_MARK.search(x):
                out |= numbers(x)
    return out


def mc_trap(it, text):
    if (any(b.startswith("MC") for b in it["bullets"]) or re.search(r"material\s*check", text, re.I)) and mc_phase_claims(text):
        return ["MaterialCheck: only phase 1 is in production (CONF-an); phases 2-3 are the plan"]
    return []


def auto_checks(sess, it, turn, con=None):
    con = con or ip.db()
    main = turn.get("answer") or ""
    fus = turn.get("followups") or []
    text = "\n".join([main] + [f.get("a") or "" for f in fus])
    low = text.lower()
    hard, ctext, names, cv = evidence(sess, it, con=con)
    unverified, library_only = classify_numbers(text, hard, ctext, names, library_text(con, cv), conf_numbers(it.get("notes")))
    traps = mc_trap(it, text)
    if re.search(r"\bidentical output", low):
        traps.append("'identical output' is banned wording (writing rules)")
    hits, miss = [], []
    for c in it["concepts"]:
        (hits if any(has(t, low) for t in concept_terms(con, c)) else miss).append(c)
    words = len(main.split())
    i_n = len(re.findall(r"\bI\b|\bI'(?:m|ve|d)\b|\bmy\b", text))
    we_n = len(re.findall(r"\b(?:we|our|us)\b", text, re.I))
    flags = []
    if words < 40:
        flags.append(f"main answer {words} words: too short to show evidence")
    elif words > 220:
        flags.append(f"main answer {words} words (~{round(words / WPM * 60)} s): cut to 60-150")
    if it["kind"] in ("behavioural", "project") and we_n > 2 * max(i_n, 1):
        flags.append(f"'we' {we_n}x vs 'I' {i_n}x: say what you did")
    if it["kind"] == "behavioural" and not (toks(main) or re.search(r"\b(result|so that|which meant|reduced|cut|saved|improved|grew)\b", main, re.I)):
        flags.append("no result stated (STAR: R)")
    if re.search(r"[\u4e00-\u9fff]", text):
        flags.append("answer contains Chinese: practise it in English")
    jargon = jargon_terms(main)
    if item_role(sess, it) == "hr" and len(jargon) > 3:
        flags.append(f"HR listener: {len(jargon)} technical terms ({', '.join(jargon[:8])}): keep at most 2, explain them plainly")
    return {"words": words, "seconds": round(words / WPM * 60), "followups": len(fus), "i": i_n, "we": we_n,
            "concepts_hit": hits, "concepts_missed": miss, "unverified_numbers": unverified,
            "library_only_numbers": library_only, "gap_claims": gap_claims(text, gap_terms_for(sess["jd"])),
            "traps": traps, "flags": flags, "jargon": jargon}


def print_auto(n, a):
    print(f"Q{n} auto: {a['words']} words (~{a['seconds']} s) · follow-ups {a['followups']} · I {a['i']} / we {a['we']}")
    print(f"  concepts hit: {', '.join(a['concepts_hit']) or '-'} · missed: {', '.join(a['concepts_missed']) or '-'}")
    for x in a["unverified_numbers"]:
        print(f"  UNVERIFIED number '{x}': in no library bullet, card, CONF item or the CV")
    for x in a["library_only_numbers"]:
        print(f"  note: '{x}' is in the library but not on this CV or the cited bullets: fine if asked, be ready to back it")
    for x in a["gap_claims"]:
        print(f"  GAP CLAIM {x}")
    for x in a["traps"]:
        print(f"  TRAP {x}")
    for x in a["flags"]:
        print(f"  flag: {x}")


ATTEMPT_KEYS = ("answer", "followups", "auto", "scores", "feedback", "better", "framework", "chunks", "other_roles", "honesty_note",
                "scored", "answered")


def record_answer(path, n, turn):
    sess = load(path)
    it = item(sess, n)
    if not (turn.get("answer") or "").strip():
        sys.exit("turn.json needs a non-empty 'answer'")
    if it.get("answer"):   # keep the earlier attempt (and its score) for history
        it.setdefault("past", []).append({k: it.get(k) for k in ATTEMPT_KEYS})
    for k, v in (("scores", None), ("feedback", ""), ("better", ""), ("framework", None), ("chunks", None), ("other_roles", []),
                 ("honesty_note", ""), ("scored", None)):
        it[k] = v
    it["auto"] = auto_checks(sess, it, turn)
    it["answer"], it["followups"] = turn["answer"], turn.get("followups") or []
    it["answered"] = now().isoformat()
    it["attempts"] = it.get("attempts", 0) + 1
    it["skipped"] = False
    save(path, sess)
    return it["auto"]


def record_skip(path, n):
    sess = load(path)
    it = item(sess, n)
    it["skipped"] = True
    save(path, sess)


def record_score(path, n, sc):
    """-> (errors, warnings); nothing is stored when there are errors."""
    sess = load(path)
    it = item(sess, n)
    errs, warns = [], []
    con = ip.db()
    if not it.get("answer"):
        errs.append(f"Q{n} has no answer yet (run answer first)")
    scores = sc.get("scores") or {}
    for k in RUBRIC:
        v = scores.get(k)
        if k == "followups" and v is None and not it.get("followups"):
            continue   # no follow-up asked
        if type(v) is not int or not 1 <= v <= 5:
            errs.append(f"score '{k}' must be an integer 1-5 (got {v!r})")
    for k in scores:
        if k not in RUBRIC:
            errs.append(f"unknown score key '{k}' (rubric: {', '.join(RUBRIC)})")
    a = it.get("auto") or {}
    if (a.get("unverified_numbers") or a.get("gap_claims") or a.get("traps")) and type(scores.get("honesty")) is int \
            and scores["honesty"] >= 4 and not (sc.get("honesty_note") or "").strip():
        errs.append("auto checks flagged unverified numbers / gap claims / traps but honesty >= 4: lower it or explain in honesty_note")
    if not (sc.get("feedback") or "").strip():
        errs.append("feedback is empty")
    extra = list(sc.get("better_bullets") or [])
    known = {r[0] for r in con.execute("select code from bullets")}
    for b in extra:
        if b not in known:
            errs.append(f"better_bullets: unknown bullet {b}")
    fw, chunks = sc.get("framework"), sc.get("chunks")
    allowed = frameworks_for(it)
    if chunks is not None or fw is not None:
        if fw not in allowed:
            errs.append(f"framework {fw!r} not allowed for a {it['kind']} question (use one of {allowed})")
        elif not isinstance(chunks, list) or [c.get("label") for c in chunks if isinstance(c, dict)] != [l for l, _ in FRAMEWORKS[fw]["chunks"]]:
            errs.append(f"chunks must follow {fw} in order: {[l for l, _ in FRAMEWORKS[fw]['chunks']]}")
        else:
            for c in chunks:
                for k in ("yours", "tip", "better"):
                    if not (c.get(k) or "").strip():
                        errs.append(f"chunk {c.get('label')}: empty '{k}'")
            sc = dict(sc, better=" ".join((c.get("better") or "").strip() for c in chunks))
    for o_ in sc.get("other_roles") or []:
        if not isinstance(o_, dict) or o_.get("role") not in ROLES or o_.get("role") == item_role(sess, it) or not (o_.get("tip") or "").strip():
            errs.append(f"other_roles entries need a role in {sorted(set(ROLES) - {item_role(sess, it)})} and a tip")
            break
    better = sc.get("better") or ""
    if better and item_role(sess, it) == "hr" and len(jargon_terms(better)) > 4:
        warns.append(f"HR better answer uses {len(jargon_terms(better))} technical terms ({', '.join(jargon_terms(better)[:6])}): explain or drop some")
    if better:
        hard, ctext, names, _cv = evidence(sess, it, [b for b in extra if b in known], con)
        conf, skipped, _ = conf_items()
        notes = " ".join([it.get("notes") or "", sc.get("notes") or ""])
        e2, w2 = check_numbers("better", better, hard, notes, conf, skipped, ",".join(it["bullets"] + extra))
        if ctext:
            e2 = truth.filter_company_numbers("better", better, e2, ctext, names, "first")
        errs += e2
        warns += w2
        errs += [f"better: {x}" for x in mc_trap(it, better)]
        gc = gap_claims(better, gap_terms_for(sess["jd"]))
        errs += [f"better claims a gap term: {x}" for x in gc]
        words = len(better.split())
        if not ip.ANS_MIN <= words <= ip.ANS_MAX + 30:
            errs.append(f"better answer {words} words (need {ip.ANS_MIN}-{ip.ANS_MAX + 30})")
    if errs:
        return errs, warns
    it["scores"] = {k: v for k, v in scores.items() if k in RUBRIC}
    it["feedback"], it["better"] = sc.get("feedback", ""), better
    it["framework"], it["chunks"] = sc.get("framework"), sc.get("chunks")
    it["other_roles"] = sc.get("other_roles") or []
    it["honesty_note"] = sc.get("honesty_note", "")
    it["scored"] = now().isoformat()
    save(path, sess)
    return [], warns


def item(sess, n):
    for it in sess["items"]:
        if it["n"] == n:
            return it
    sys.exit(f"no Q{n} in this session (1-{len(sess['items'])})")


# ---------------------------------------------------------------- report
def report(path):
    sess = load(path)
    its = sess["items"]
    done = [it for it in its if it.get("scores")]
    o = [f"# Mock interview: {sess['company']} · {sess['title']} ({sess['jd']})", "",
         f"Stage {sess['stage']} · started {sess['started']} · {len(done)}/{len(its)} questions scored · "
         "scores 1-5 per rubric dimension.", ""]
    if done:
        o += ["| # | Kind | Question | Overall | Weakest |", "|---|---|---|---|---|"]
        for it in done:
            s = it["scores"]
            weak = min(s, key=s.get)
            o.append(f"| {it['n']} | {it['kind']} | {it['question'].replace('|', '/')} | {overall(s)} | {weak} ({s[weak]}) |")
        o += ["", "## By dimension", ""]
        for k in RUBRIC:
            vals = [it["scores"][k] for it in done if k in it["scores"]]
            if vals:
                o.append(f"- **{k}** {statistics.mean(vals):.1f} · {RUBRIC[k]}")
        avg = statistics.mean(overall(it["scores"]) for it in done)
        o += ["", f"**Session average {avg:.2f}**", ""]
    autos = [(it["n"], att.get("auto") or {}) for it in its for att in (it.get("past") or []) + [it]]
    claims = list(dict.fromkeys((n, x) for n, a in autos for x in a.get("unverified_numbers", [])))
    gapc = list(dict.fromkeys((n, x) for n, a in autos for x in a.get("gap_claims", []) + a.get("traps", [])))
    if claims or gapc:
        o += ["## Claims to check", "",
              "Numbers below are in no library bullet, card, CONF item or the CV. If true, add them through tailor-cv "
              "library mode or a CONF item (your approval); if not sure, do not say them in the interview.", ""]
        o += [f"- Q{n}: number '{x}'" for n, x in claims]
        o += [f"- Q{n}: {x}" for n, x in gapc]
        o.append("")
    redo = [it for it in done if overall(it["scores"]) < 3.5]
    if redo:
        o += ["## Practise again", ""] + [f"- Q{it['n']} {it['question']} ({overall(it['scores'])})" for it in redo] + [""]
    o += ["## Per question", ""]
    for it in its:
        o.append(f"### Q{it['n']} [{it['kind']} · {ROLES[item_role(sess, it)]['zh']}] {it['question']}")
        if not it.get("answer"):
            o += ["(skipped)" if it.get("skipped") else "(not answered)", ""]
            continue
        if it.get("past"):
            o.append(f"*attempt {it.get('attempts')}; earlier scores: "
                     + ", ".join(str(overall(p_.get("scores"))) for p_ in it["past"] if p_.get("scores")) + "*")
        o += ["", f"**Your answer** ({it['auto']['words']} words, ~{it['auto']['seconds']} s): {it['answer']}"]
        for f in it.get("followups") or []:
            o.append(f"- *Follow-up:* {f.get('q', '')}  \n  **You:** {f.get('a', '')}")
        if it.get("scores"):
            o += ["", "Scores: " + " · ".join(f"{k} {v}" for k, v in it["scores"].items()) + f" → **{overall(it['scores'])}**",
                  "", f"**Feedback:** {it['feedback']}"]
            for o_ in it.get("other_roles") or []:
                o.append(f"- *換成 {ROLES[o_['role']]['zh']}:* {o_['tip']}")
            if it.get("chunks"):
                o += ["", f"**Framework: {FRAMEWORKS[it['framework']]['name']}**", "",
                      "| Chunk | 你說的 | 怎麼改 | Stronger version |", "|---|---|---|---|"]
                cell = lambda x: (x or "").replace("|", "/").replace("\n", " ")
                o += [f"| {c['label']} | {cell(c.get('yours'))} | {cell(c.get('tip'))} | {cell(c.get('better'))} |" for c in it["chunks"]]
            elif it.get("better"):
                o += ["", f"**Stronger version:** {it['better']}"]
        o.append("")
    text = "\n".join(o)
    out = path[:-5] + ".md"
    open(out, "w", encoding="utf-8").write(text)
    return out, (statistics.mean(overall(it["scores"]) for it in done) if done else None), len(done), len(its)


# ---------------------------------------------------------------- CLI
def cmd_start(a):
    kinds = ip.csv(a.kinds) or None
    if kinds:
        bad = [k for k in kinds if k not in ip.KINDS or k == "ask"]
        if bad:
            sys.exit(f"bad kinds {bad}")
    path, sess = new_session(a.jd, a.stage, a.n, kinds, a.followups, a.out_dir)
    print(f"session: {path}")
    print(brief(sess))


def cmd_brief(a):
    sess = load(a.session)
    if a.json:
        print(json.dumps(brief_data(sess), ensure_ascii=False))
    else:
        print(brief(sess))


def cmd_answer(a):
    auto = record_answer(a.session, a.q, load(a.file))
    print_auto(a.q, auto)


def cmd_score(a):
    errs, warns = record_score(a.session, a.q, load(a.file))
    for w in warns:
        print("WARN ", w)
    for e in errs:
        print("ERROR", e)
    if errs:
        sys.exit(1)
    it = item(load(a.session), a.q)
    print(f"Q{a.q} scored: {overall(it['scores'])} ({', '.join(f'{k} {v}' for k, v in it['scores'].items())})")


def cmd_status(a):
    sess = load(a.session)
    nxt = None
    for it in sess["items"]:
        st = (f"scored {overall(it['scores'])}" if it.get("scores") else "skipped" if it.get("skipped")
              else "answered" if it.get("answer") else "open")
        if nxt is None and not it.get("scores") and not it.get("skipped"):
            nxt = it["n"]
        print(f"Q{it['n']} [{it['kind']}] {st} · {it['question'][:80]}")
    print(f"next: Q{nxt}" if nxt else "all scored: run report")


def cmd_skip(a):
    record_skip(a.session, a.q)
    print(f"Q{a.q} skipped")


def cmd_report(a):
    out, avg, n, tot = report(a.session)
    print(f"wrote {out} · scored {n}/{tot}" + (f" · average {avg:.2f}" if avg is not None else ""))


def cmd_history(a):
    h = history(mock_dir(a.jd, a.out_dir))
    if not h:
        print("(no scored mock answers yet)")
    for q, v in sorted(h.items(), key=lambda x: x[1][-1][1]):
        print(f"{len(v)}x · last {v[-1][1]} · best {max(x[1] for x in v)} · {q[:90]}")


def selftest():
    ok = True

    def chk(name, cond, msg=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f": {msg}" if msg and not cond else ""))
        ok &= bool(cond)

    try:
        ip.app("TOMRA")
    except SystemExit:
        print("SKIP no TOMRA application"); sys.exit(0)
    import shutil
    tmp = tempfile.mkdtemp(prefix="mock_selftest_")
    try:
        path, sess = new_session("TOMRA", "mixed", 4, None, 2, tmp)
        kinds = [it["kind"] for it in sess["items"]]
        chk("start picks 4 questions over 4 kinds", len(kinds) == 4 and len(set(kinds)) == 4, str(kinds))
        chk("brief marks reference answers as hidden", "reference answer (hidden)" in brief(sess))
        it = next(x for x in sess["items"] if x["kind"] not in ("intro", "motivation"))
        QN = it["n"]
        pad = " I owned the detector work end to end and reviewed results with the operators on site every week."
        A = lambda ans, fus=(): record_answer(path, QN, {"answer": ans + pad * 2, "followups": list(fus)})
        a = A("I cut inference latency by 37% and I deployed the model on Jetson at the plant.", [{"q": "How?", "a": "I measured it."}])
        chk("invented 37% is unverified", "37%" in a["unverified_numbers"], str(a["unverified_numbers"]))
        chk("'Jetson' said as done is a gap claim", any("jetson" in x for x in a["gap_claims"]), str(a["gap_claims"]))
        a = A("I have not used Jetson in production yet; I would learn it on a dev kit.")
        chk("negated gap term is not a claim", not a["gap_claims"], str(a["gap_claims"]))
        for ans in ("I deployed the model on Jetson with no downtime.", "I ran the Jetson box next to the line.",
                    "Without any help I wrote the CUDA kernels myself."):
            a = A(ans)
            chk(f"claim despite a far 'no/next/without': {ans[:40]}", bool(a["gap_claims"]), str(a["gap_claims"]))
        a = A("I handled the edge cases in post-processing and called cloud LLM APIs as a consumer.")
        chk("'edge cases' / 'cloud LLM APIs' are not gap claims", not a["gap_claims"], str(a["gap_claims"]))
        a = A("I deployed it to the cloud on AWS.")
        chk("'to the cloud on AWS' is a gap claim", bool(a["gap_claims"]), str(a["gap_claims"]))
        a = A("I cut latency by forty percent and doubled throughput.")
        chk("number words are unverified", any("number word" in x for x in a["unverified_numbers"]), str(a["unverified_numbers"]))
        a = A("I improved recall by 3 percent and served 250 customers in 2019.")
        chk("bare numbers / years / '3 percent' do not verify a claim",
            {"250", "2019", "3%"} <= set(a["unverified_numbers"]), str(a["unverified_numbers"]))
        a = A("MaterialCheck second phase is in production now.")
        chk("MaterialCheck second phase is a trap", bool(a["traps"]), str(a["traps"]))
        A("I cut inference latency by 37% on Jetson.", [{"q": "How?", "a": "Profiling."}])
        good = {k: 3 for k in RUBRIC}
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=5), "feedback": "x"})
        chk("honesty 5 despite flags is refused", any("honesty" in x for x in e), str(e))
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=2), "feedback": "x",
                                      "better": "In my last role I improved precision by 45% on the sorting line." + pad * 3})
        chk("better answer with invented 45% is refused", any("45%" in x for x in e), str(e))
        e, w = record_score(path, QN, {"scores": dict(good, honesty=2), "feedback": "x",
                                      "better": "I doubled throughput and cut misses by half on the sorting line." + pad * 3})
        chk("better answer with number words is warned", any("number word" in x for x in w), str(w))
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=6), "feedback": "x"})
        chk("score out of range refused", any("1-5" in x for x in e), str(e))
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=True), "feedback": "x"})
        chk("boolean score refused", any("honesty" in x and "1-5" in x for x in e), str(e))
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=2), "feedback": "x", "better_bullets": ["ZZ99"]})
        chk("unknown better_bullets refused", any("ZZ99" in x for x in e), str(e))
        fk = frameworks_for(it)[0]
        labels = [l for l, _ in FRAMEWORKS[fk]["chunks"]]
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=2), "feedback": "x", "framework": fk,
                                      "chunks": [{"label": l, "yours": "y", "tip": "t", "better": "b"} for l in reversed(labels)]})
        chk("chunks out of framework order refused", any("must follow" in x for x in e), str(e))
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=2), "feedback": "x", "framework": "NOPE", "chunks": []})
        chk("unknown framework refused", any("not allowed" in x for x in e), str(e))
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=2), "feedback": "x",
                                       "other_roles": [{"role": item_role(sess, it), "tip": "same role"}]})
        chk("other_roles may not repeat the question's own role", any("other_roles" in x for x in e), str(e))
        chk("mixed stage: technical → engineer, motivation → HR, project → HM",
            (role_for("mixed", "technical"), role_for("mixed", "motivation"), role_for("mixed", "project"), role_for("hr", "project"))
            == ("tech", "hr", "hm", "hr"))
        jt = jargon_terms("I package MLflow models into Docker images and GitLab CI deploys them; the YOLO pipeline uses vLLM.")
        chk("jargon finds tool names and acronyms", {"MLflow", "Docker", "GitLab", "CI", "YOLO", "vLLM", "pipeline"} <= set(jt), str(jt))
        chk("plain sentence has no jargon", jargon_terms("I helped the quality team check drawings in five minutes instead of thirty.") == [])
        sent = "I owned the planning myself and kept the engineers and their managers aligned with regular demos and short written updates every week."
        e, _ = record_score(path, QN, {"scores": dict(good, honesty=2), "feedback": "Say the gap plainly.", "framework": fk,
                                      "chunks": [{"label": l, "yours": "你說了…", "tip": "先講結論", "better": sent} for l in labels]})
        chk("valid chunked score stored, better = joined chunks", not e and item(load(path), QN)["better"].count(sent) == len(labels), str(e))
        A("I have not used Jetson; I would learn it on a dev kit.")
        h = history(tmp).get(it["question"]) or []
        chk("re-answer keeps the earlier scored attempt in history", len(h) == 1, str(h))
        chk("re-answer clears the old feedback", item(load(path), QN)["feedback"] == "")
        record_skip(path, next(x['n'] for x in sess['items'] if x['n'] != QN))
        chk("skip is recorded", item(load(path), next(x['n'] for x in sess['items'] if x['n'] != QN)).get("skipped") is True)
        out, avg, n, tot = report(path)
        txt = open(out, encoding="utf-8").read()
        chk("report lists claims and the skip", "Claims to check" in txt and "(skipped)" in txt)
        mot = next((i for i, q in enumerate(load(sess["prep"])) if ip.classify(q) == "motivation"), None)
        if mot is not None:
            q = load(sess["prep"])[mot]
            mit = {"n": 9, "kind": "motivation", "question": q["question"], "bullets": ip.csv(q["bullets"]), "concepts": [],
                   "notes": "", "reported": []}
            un, _ = classify_numbers("In my last role I supported 9000 users and 23 sites.", *evidence(sess, mit)[:3],
                                     "", set())
            chk("motivation: a JD/profile figure about 'my' work is unverified", "9000" in un or "23" in un, str(un))
        rq = {"n": 8, "kind": "technical", "question": "How would you cut inference cost by 30% at 50k images a day?",
              "bullets": it["bullets"], "concepts": [], "notes": "", "reported": ["R1"]}
        un, _ = classify_numbers("I cut inference cost by 30% at 50k images a day.", *evidence(sess, rq)[:3], "", set())
        chk("numbers in a (reported) question are not evidence", "30%" in un and "50k" in un, str(un))
        lib = library_text(ip.db(), "")
        un, lo = classify_numbers("I cut rollback time by 70% with a script.", "", "", "", lib, set())
        chk("a library number in another context is unverified", "70%" in un, f"{un} {lo}")
        un, lo = classify_numbers("Our OCR recognition accuracy went from 70% to over 95%.", "", "", "", lib, set())
        chk("the same number next to its own words is library-only", "70%" in lo, f"{un} {lo}")
        path2, sess2 = new_session("TOMRA", "mixed", 4, None, 2, tmp)
        chk("next session moves the practised question back in its kind (reported questions stay first)",
            next((x for x in sess2["items"] if x["kind"] == it["kind"]), {}).get("question") != it["question"] or it["reported"]
            or sum(1 for q in load(sess["prep"]) if ip.classify(q) == it["kind"]) == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("start"); p.add_argument("--jd", required=True); p.add_argument("--stage", default="mixed", choices=STAGE_KINDS)
    p.add_argument("--n", type=int, default=5); p.add_argument("--kinds"); p.add_argument("--followups", type=int, default=2)
    p.add_argument("--out-dir"); p.set_defaults(f=cmd_start)
    for name, fn in (("answer", cmd_answer), ("score", cmd_score)):
        p = sp.add_parser(name); p.add_argument("session"); p.add_argument("--q", type=int, required=True)
        p.add_argument("--file", required=True); p.set_defaults(f=fn)
    p = sp.add_parser("brief"); p.add_argument("session"); p.add_argument("--json", action="store_true"); p.set_defaults(f=cmd_brief)
    p = sp.add_parser("skip"); p.add_argument("session"); p.add_argument("--q", type=int, required=True); p.set_defaults(f=cmd_skip)
    p = sp.add_parser("status"); p.add_argument("session"); p.set_defaults(f=cmd_status)
    p = sp.add_parser("report"); p.add_argument("session"); p.set_defaults(f=cmd_report)
    p = sp.add_parser("history"); p.add_argument("--jd", required=True); p.add_argument("--out-dir"); p.set_defaults(f=cmd_history)
    p = sp.add_parser("selftest"); p.set_defaults(f=lambda a: selftest())
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
