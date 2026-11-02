"""A/B test: regex vs Jev inbox classifier on identical inputs (`morning.py abtest`).

Data
  fixtures  scripts/fixtures/inbox_cases.json — labelled (gold kind / gold company)
  real      last N days via the local Google login (read-only): the recruiter/ATS query of fetch_gmail plus a
            sample of job alerts as negatives. Written once to data/morning/abtest_mails_<date>.json (gitignored)
            so both arms classify the same inputs; rerun with --refetch to replace it.
Gold for real mails: cv/digest/abtest_labels_<date>.csv with gold_kind / gold_company EMPTY — the candidate fills them
in, then `morning.py abtest --score <csv>`. Neither classifier pre-fills gold.

Metrics per arm: accuracy (abstain counted wrong) and accuracy on answered mails, per-kind P/R/F1, company accuracy,
WRONG STATUS PROPOSALS (the arm proposes a 狀態 the gold kind would not) vs MISSED proposals vs abstentions ("needs
review", Jev 0.4–0.8 band), latency per mail, API calls, input tokens, cost, regex fallbacks.
Proposals are computed for a row in 履歷送出 (the common case) so the metric measures the classifier, not the tracker.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import time
import urllib.parse
from pathlib import Path

import jev_questions as JQ
import morning as M

KINDS = ["rejection", "interview", "offer", "ack", "info_request", "auto_reply", "other"]
ASSUMED_STATUS = "履歷送出"
ALERT_SAMPLE_Q = "from:(jobalerts-noreply@linkedin.com OR jobagent.stepstone.de OR noreply@glassdoor.com)"


def display_kind(kind: str, phrase: str = "", why: str = "") -> str:
    if kind == "request":
        return "info_request"
    if kind == "other" and ("Auto-Submitted" in phrase or re.search(M.AUTO_RE, phrase or "", re.I) or why == "auto_reply"):
        return "auto_reply"
    return kind


def internal_kind(k: str) -> str:
    return {"info_request": "request", "auto_reply": "other"}.get(k, k)


# ---- data -----------------------------------------------------------------------------------------
def fixture_items() -> tuple[list[dict], list[dict]]:
    fx = json.loads((M.HERE / "fixtures" / "inbox_cases.json").read_text(encoding="utf-8"))
    items = []
    for i, c in enumerate(fx["classify"]):
        items.append({"id": f"fx-classify-{i}", "from": "", "subject": "", "snippet": c["text"], "gold_kind": display_kind(c["gold"]),
                      "gold_company": "", "why": c["why"]})
    for i, c in enumerate(fx["match"]):
        items.append({"id": f"fx-match-{i}", "from": c["from"], "subject": c["subject"], "snippet": c["snippet"],
                      "gold_kind": c.get("kind") or "", "gold_company": c["company"] or "none", "why": c["why"]})
    for i, c in enumerate(fx["inbox"]):
        items.append({"id": f"fx-inbox-{i}", "from": c["from"], "subject": c["subject"], "snippet": c["snippet"],
                      "gold_kind": c["kind"], "gold_company": "", "why": c["why"]})
    for i, c in enumerate(fx["alerts"]):
        items.append({"id": f"fx-alert-{i}", "from": c["from"], "subject": c["subject"], "snippet": "",
                      "gold_kind": "other" if c["alert"] else "ack", "gold_company": "",
                      "why": "job alert" if c["alert"] else "board mail about own application"})
    return items, fx["rows"]


def real_items(days: int, rows: list[dict], today: dt.date, refetch: bool, n_alerts: int = 10) -> tuple[list[dict], Path]:
    path = M.PIPE_DATA.parent / "morning" / f"abtest_mails_{today.isoformat()}.json"
    if path.exists() and not refetch:
        return json.loads(path.read_text(encoding="utf-8")), path
    start = today - dt.timedelta(days=days)
    fetched = M.fetch_gmail(start, rows, max_n=200)
    # the mails the morning run keeps (not a job alert) + a sample of job alerts as negatives
    kept = [m for m in fetched if not M.is_job_alert(m)]
    alerts = [m for m in fetched if M.is_job_alert(m)]
    step = max(1, len(alerts) // n_alerts) if alerts else 1
    mails = kept + [{**m, "sampled_alert": True} for m in alerts[::step][:n_alerts]]
    if len([m for m in mails if m.get("sampled_alert")]) < n_alerts:   # the query excludes the big alert senders
        http = M.google_http("gmail")
        q = urllib.parse.urlencode({"q": f"after:{start:%Y/%m/%d} {ALERT_SAMPLE_Q}", "maxResults": n_alerts})
        seen = {m["id"] for m in mails}
        for x in http.request("GET", f"{M.GMAIL}?{q}").get("messages", []):
            if x["id"] in seen or len([m for m in mails if m.get("sampled_alert")]) >= n_alerts:
                continue
            meta = http.request("GET", f"{M.GMAIL}/{x['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject")
            h = {y["name"].lower(): y["value"] for y in meta.get("payload", {}).get("headers", [])}
            ms = int(meta.get("internalDate", 0)) / 1000
            mails.append({"id": x["id"], "from": h.get("from", ""), "subject": h.get("subject", ""),
                          "snippet": meta.get("snippet", ""), "date": dt.datetime.fromtimestamp(ms, M.TZ).isoformat(),
                          "sampled_alert": True})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mails, ensure_ascii=False, indent=1), encoding="utf-8")
    path.chmod(0o600)
    return mails, path


# ---- arms -----------------------------------------------------------------------------------------
def run_arms(items: list[dict], rows: list[dict], con, client) -> list[dict]:
    ctx_r = M.MailCtx(rows, con, "regex")
    ctx_j = M.MailCtx(rows, con, "jev", client)
    ctx_j.cache, ctx_j.deadline = {}, float("inf")   # the A/B measures fresh calls: no cache, no deadline
    out = []
    for m in items:
        mp = M.mail_parts(m, ctx_r.doms)
        t0 = time.perf_counter()
        r = M.classify_regex(m, mp, ctx_r, prefilter=False)
        r_ms = (time.perf_counter() - t0) * 1000
        j = M.classify_jev(m, mp, ctx_j) if client else None
        rec = {**{k: m.get(k, "") for k in ("id", "date", "from", "subject", "gold_kind", "gold_company", "why")},
               "regex_kind": display_kind(r["kind"], r["phrase"]), "regex_phrase": r["phrase"],
               "company_regex": r["hit"]["company"] if r["hit"] else "none", "regex_ms": round(r_ms, 2)}
        if j:
            why = (j.get("phrase") or "").split(" ")[1] if (j.get("phrase") or "").startswith("jev ") else ""
            rec.update({"jev_kind": "review" if j["kind"] == "review" else display_kind(j["kind"], j.get("phrase", ""), why),
                        "jev_prob": "" if j.get("prob") is None else round(j["prob"], 3), "jev_why": why,
                        "jev_band": j.get("band", ""), "company_jev": j["hit"]["company"] if j.get("hit") else
                        ("review:" + (j.get("company_jev") or {}).get("name", "") if (j.get("company_jev") or {}).get("band") == "review" else "none"),
                        "jev_ms": round(1000 * (j.get("latency") or 0), 1), "fallback": j.get("fallback", False),
                        "tokens": j.get("tokens") or 0, "candidates": j.get("candidates", []), "nouls": j.get("nouls", {})})
        out.append(rec)
    return out


# ---- metrics --------------------------------------------------------------------------------------
def proposal(kind: str) -> str | None:
    if kind in ("review", "", None):
        return None
    return M.propose(internal_kind(kind), ASSUMED_STATUS)[0]


def same_company(a: str, b: str) -> bool:
    a, b = (a or "none"), (b or "none")
    if a == "none" or b == "none":
        return a == b
    return M.norm_co(M.clean_name(a)) == M.norm_co(M.clean_name(b))


def metrics(recs: list[dict], arm: str) -> dict:
    kcol, ccol = f"{arm}_kind", f"company_{arm}"
    gold = [r for r in recs if r.get("gold_kind") and r.get(kcol) not in (None, "")]
    n = len(gold)
    correct = sum(r[kcol] == r["gold_kind"] for r in gold)
    abst = sum(r[kcol] == "review" for r in gold)
    answered = [r for r in gold if r[kcol] != "review"]
    per = {}
    for k in KINDS:
        tp = sum(r[kcol] == k and r["gold_kind"] == k for r in gold)
        fp = sum(r[kcol] == k and r["gold_kind"] != k for r in gold)
        fn = sum(r[kcol] != k and r["gold_kind"] == k for r in gold)
        p = tp / (tp + fp) if tp + fp else None
        rc = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * rc / (p + rc) if p and rc else (0.0 if (p is not None and rc is not None) else None)
        per[k] = {"p": p, "r": rc, "f1": f1, "support": tp + fn}
    wrong = [r for r in gold if proposal(r[kcol]) is not None and proposal(r[kcol]) != proposal(r["gold_kind"])]
    missed = [r for r in gold if r[kcol] != "review" and proposal(r["gold_kind"]) is not None and proposal(r[kcol]) is None]
    cg = [r for r in recs if r.get("gold_company") and r.get(ccol) not in (None, "")]
    c_ok = sum(same_company(r[ccol], r["gold_company"]) for r in cg)
    ms = [float(r[f"{arm}_ms"]) for r in recs if r.get(f"{arm}_ms") not in (None, "")]
    return {"n": n, "accuracy": correct / n if n else None, "answered_n": len(answered),
            "accuracy_answered": sum(r[kcol] == r["gold_kind"] for r in answered) / len(answered) if answered else None,
            "abstain": abst, "abstain_rate": abst / n if n else None, "per_kind": per, "wrong_proposals": wrong,
            "missed_proposals": missed, "company_n": len(cg), "company_accuracy": c_ok / len(cg) if cg else None,
            "latency_ms_mean": sum(ms) / len(ms) if ms else None, "latency_ms_max": max(ms) if ms else None}


def pct(x) -> str:
    return "–" if x is None else f"{100 * x:.0f}%"


def report(fx_recs: list[dict], real_recs: list[dict], client, today: dt.date, real_path, labels_path, stub: bool,
           real_note: str = "") -> str:
    L_ = [f"# Inbox classifier A/B — regex vs Jev ({today.isoformat()})", ""]
    if stub:
        L_ += ["> **STUB** — Jev answers are recorded fakes; this proves the pipeline, it is **not a result**.", ""]
    calls, toks = (client.calls, client.input_tokens) if client else (0, 0)
    L_ += [f"Model `{JQ.MODEL}` · thresholds yes ≥ {JQ.YES}, review {JQ.NO}–{JQ.YES}, no ≤ {JQ.NO} · "
           f"proposals evaluated for a row in {ASSUMED_STATUS}.", ""]
    for title, recs in (("Fixtures (labelled)", fx_recs), ("Real mail (gold from the labels CSV)", real_recs)):
        if not recs:
            continue
        mr, mj = metrics(recs, "regex"), metrics(recs, "jev")
        L_ += [f"## {title}", "", f"n with gold kind = {mr['n']} (company gold: {mr['company_n']})", "",
               "| Metric | Regex | Jev |", "|---|---|---|"]
        if not mr["n"]:
            L_ += ["| (no gold labels yet) | – | – |"]
        L_ += [f"| Accuracy (abstain = wrong) | {pct(mr['accuracy'])} | {pct(mj['accuracy'])} |",
               f"| Accuracy on answered | {pct(mr['accuracy_answered'])} | {pct(mj['accuracy_answered'])} (n {mj['answered_n']}) |",
               f"| **Wrong status proposals** | {len(mr['wrong_proposals'])} | {len(mj['wrong_proposals'])} |",
               f"| Missed proposals | {len(mr['missed_proposals'])} | {len(mj['missed_proposals'])} |",
               f"| Needs review (abstain) | {mr['abstain']} | {mj['abstain']} ({pct(mj['abstain_rate'])}) |",
               f"| Company accuracy | {pct(mr['company_accuracy'])} | {pct(mj['company_accuracy'])} |",
               f"| Latency per mail (mean / max) | {mr['latency_ms_mean'] or 0:.2f} / {mr['latency_ms_max'] or 0:.2f} ms | "
               f"{(mj['latency_ms_mean'] or 0):.0f} / {(mj['latency_ms_max'] or 0):.0f} ms |", ""]
        L_ += ["| Kind | Regex P / R / F1 | Jev P / R / F1 | support |", "|---|---|---|---|"]
        for k in KINDS:
            a, b = mr["per_kind"][k], mj["per_kind"][k]
            if not a["support"] and a["p"] is None and b["p"] is None:
                continue
            L_.append(f"| {k} | {pct(a['p'])} / {pct(a['r'])} / {pct(a['f1'])} | {pct(b['p'])} / {pct(b['r'])} / {pct(b['f1'])} | {a['support']} |")
        L_.append("")
        for arm, m_ in (("Regex", mr), ("Jev", mj)):
            for r in m_["wrong_proposals"]:
                L_.append(f"- {arm} wrong proposal: `{r['id']}` {r['subject'][:50] or r.get('why', '')[:50]!r} — "
                          f"gold {r['gold_kind']}, said {r[arm.lower() + '_kind']} → {proposal(r[arm.lower() + '_kind'])}")
        L_.append("")
        if mr["n"]:
            ok = len(mj["wrong_proposals"]) <= len(mr["wrong_proposals"]) and (mj["accuracy"] or 0) >= (mr["accuracy"] or 0)
            L_ += [f"**Rule** (switch the default to Jev only if wrong proposals ≤ regex AND accuracy ≥ regex, n = {mr['n']}): "
                   + ("**met**" if ok else "**not met**") + ("" if not stub else " (STUB — ignore)"), ""]
    allrecs = fx_recs + real_recs
    dis = [r for r in allrecs if r.get("jev_kind") and r["jev_kind"] != r["regex_kind"]]
    L_ += [f"## Disagreements ({len(dis)} of {len(allrecs)})", "",
           "| id | subject / case | regex | jev (p, question) | gold |", "|---|---|---|---|---|"]
    for r in dis[:60]:
        L_.append(f"| {r['id'][:16]} | {M.cell(r['subject'] or r.get('why', ''), 48)} | {r['regex_kind']} | "
                  f"{r['jev_kind']} ({r.get('jev_prob', '')}, {r.get('jev_why', '')}) | {r.get('gold_kind') or '?'} |")
    fb = sum(1 for r in allrecs if r.get("fallback"))
    L_ += ["", "## Cost and calls", "",
           f"- Jev requests: {calls} (one per mail, all questions in one request) · regex fallbacks: {fb}",
           f"- Input tokens: {toks} · cost at ${JQ.PRICE_PER_MTOK}/Mtok (input only, docs models.md): "
           f"${toks * JQ.PRICE_PER_MTOK / 1e6:.5f}",
           f"- Real mails: {len(real_recs)} ({real_note or 'none'}){' · set: ' + str(real_path) if real_path else ''}",
           f"- Labels CSV (fill gold_kind / gold_company, then `morning.py abtest --score <csv>`): {labels_path or '–'}",
           f"- Gold kinds: {', '.join(KINDS)} · gold_company: the tracker company, or `none`"]
    return "\n".join(L_) + "\n"


LABEL_COLS = ["id", "date", "from", "subject", "regex_kind", "jev_kind", "jev_prob", "company_regex", "company_jev",
              "gold_kind", "gold_company"]


def write_labels(recs: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_COLS + ["regex_ms", "jev_ms", "jev_band", "jev_why"], extrasaction="ignore")
        w.writeheader()
        for r in recs:
            w.writerow({**r, "gold_kind": "", "gold_company": ""})   # gold stays empty: the candidate labels it
    path.chmod(0o600)


def score_csv(path: Path) -> str:
    with path.open(encoding="utf-8") as f:
        recs = list(csv.DictReader(f))
    bad = [r["id"] for r in recs if r.get("gold_kind") and r["gold_kind"] not in KINDS]
    if bad:
        raise SystemExit(f"unknown gold_kind in rows {bad[:5]} (allowed: {', '.join(KINDS)})")
    return report([], recs, None, dt.date.today(), None, path, False, f"scored from {path.name}")


def stub_responder(gold_by_text: dict):
    """STUB answers derived from the fixture gold labels (pipeline proof only)."""
    m = {"rejection": "rejection", "interview": "interview_invitation", "offer": "offer", "info_request": "info_request",
         "ack": "ack", "auto_reply": "auto_reply", "other": "mass_mailing"}

    def respond(payload):
        e = payload["state"]["email"]
        g = gold_by_text.get(e["subject"] + "|" + e["text"][:80], "other")
        ans = {k: {"type": "noul", "noul": 0.95 if m.get(g) == k else 0.05} for k in JQ.NOULS}
        if "company" in payload["questions"]:
            opts = list(payload["questions"]["company"]["criteria"])
            ans["company"] = {"type": "choice", "choice": opts[0], "confidence": 1.0, "probabilities": {o: (1.0 if i == 0 else 0.0) for i, o in enumerate(opts)}}
        return ans
    return respond
