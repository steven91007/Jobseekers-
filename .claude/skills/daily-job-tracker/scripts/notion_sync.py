"""Mirror the Sheet tabs 每日職缺 / 興趣職缺 into the Notion databases of the same name under GERMAN PLAN.

The Sheet is the source of truth (the candidate decides there or in the dashboard). Each run: rows without a
Notion page → create; rows whose mirrored fields (決定, 處理狀態, 評分, …) changed since the last sync → update.
Fingerprints live in data/pipeline/notion_state.json. Notion writes go through one headless `claude -p` per batch
of ≤ 20 pages (Notion MCP; there is no Notion API token on this machine). New page URLs are written back to the
Sheet's Notion column.
"""
import hashlib
import json
from pathlib import Path

NOTION = "mcp__plugin_Notion_notion__notion"
TOOLS = ["ToolSearch", f"{NOTION}-create-pages", f"{NOTION}-update-page", f"{NOTION}-fetch", f"{NOTION}-get-async-task"]
BATCH = 20
TABS = {
    "每日職缺": {"ds": "9db94811-3eaa-4b51-a4cd-ee97d7dd5b45", "list": "pipeline_list", "update": "pipeline_update"},
    "興趣職缺": {"ds": "3c036c71-f05f-4c92-a3b5-111686a146e9", "list": "interest_list", "update": "interest_update"},
}
COUNTRIES = {"德國", "荷蘭", "英國", "愛爾蘭"}
TYPES = {"AI agent", "ML learning", "automation"}
MODES = {"Hybrid", "Remote", "On-site", "未註明"}


def url_key(u):
    from score_jobs import url_key as k
    return k(u)


def properties(tab, r, application_url=""):
    p = {"公司": r.get("company") or r.get("url"), "職位": r.get("title", ""), "連結": r.get("url"),
         "城市": r.get("city", ""), "評分理由": r.get("reasons", ""), "備註": r.get("note", ""),
         "處理狀態": r.get("state", ""), "投遞紀錄": application_url}
    p["date:日期:start"] = r.get("date") or ""
    p["類型"] = r.get("type") if r.get("type") in TYPES else ""
    p["國家"] = r.get("country") if r.get("country") in COUNTRIES else ""
    p["工作型態"] = r.get("mode") if r.get("mode") in MODES else ""
    p["評分"] = r["score"] if isinstance(r.get("score"), (int, float)) else None
    if tab == "每日職缺":
        p["決定"] = r.get("decision") or "未確認"
    return p


def fingerprint(props):
    return hashlib.sha1(json.dumps(props, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def plan(tab, rows, state, applications):
    """→ list of ops {op: create|update, key, url, page?, properties, fp}."""
    seen = state.setdefault(tab, {})
    ops = []
    for r in rows:
        if not r.get("url"):
            continue
        k = url_key(r["url"])
        props = properties(tab, r, applications.get(k, ""))
        fp = fingerprint(props)
        known = seen.get(k) or ({"page": r["notion_url"], "fp": None} if r.get("notion_url") else None)
        if not known:
            ops.append({"op": "create", "key": k, "url": r["url"], "properties": props, "fp": fp})
        elif known.get("fp") != fp:
            ops.append({"op": "update", "key": k, "url": r["url"], "page": known["page"], "properties": props, "fp": fp})
    return ops


def prompt(tab, ops):
    ds = TABS[tab]["ds"]
    items = [{"key": o["key"], "op": o["op"], **({"page_url": o["page"]} if o["op"] == "update" else {}),
              "properties": o["properties"]} for o in ops]
    return f"""Sync {len(ops)} rows into the Notion database {tab} (data source collection://{ds}).
- op "create": create the page with notion-create-pages, parent {{"data_source_id": "{ds}"}} (one call for all creates,
  properties exactly as given).
- op "update": set exactly the given properties on page_url with notion-update-page (update_properties).
  Empty strings and null explicitly clear the corresponding property; do not omit them.
Do not touch any other page and do not add page content. Rows (JSON):
{json.dumps(items, ensure_ascii=False)}
End with ONE fenced ```json block: {{"results": [{{"key": "<key>", "page_url": "<url of the created/updated page>", "ok": true}}]}}"""


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def sync(sheet, run_claude, state_path, log, dry_run=False, tabs=tuple(TABS)):
    """sheet: object with read(action, **kw) / update(action, **kw); run_claude(prompt, tools, label) → (json, cost).
    Returns {tab: {"created": n, "updated": n, "failed": n}, "cost": $}."""
    state = load_state(state_path)
    applications = {}
    for r in sheet.read("submissions_list").get("rows", []):
        if r.get("url") and r.get("resume_version_url"):
            applications[url_key(r["url"])] = r["resume_version_url"]
    out, cost = {"cost": 0.0}, 0.0
    for tab in tabs:
        rows = sheet.read(TABS[tab]["list"], all=True).get("rows", [])
        ops = plan(tab, rows, state, applications)
        stats = {"created": 0, "updated": 0, "failed": 0, "pending": len(ops)}
        out[tab] = stats
        if not ops:
            continue
        if dry_run:
            log(f"notion: dry-run,{tab} 有 {len(ops)} 筆待同步(未呼叫 Claude)")
            continue
        for b in range(0, len(ops), BATCH):
            chunk = ops[b:b + BATCH]
            try:
                got, c = run_claude(prompt(tab, chunk), TOOLS, f"notion_sync_{'daily' if tab == '每日職缺' else 'interest'}_{b // BATCH + 1}")
                cost += c
            except Exception as e:
                log(f"notion: {tab} 同步失敗: {e}")
                stats["failed"] += len(chunk)
                continue
            res = {str(x.get("key")): x for x in got.get("results", []) if x.get("ok", True) and x.get("page_url")}
            back = []
            for o in chunk:
                hit = res.get(o["key"])
                if not hit:
                    stats["failed"] += 1
                    continue
                state[tab][o["key"]] = {"page": hit["page_url"], "fp": o["fp"]}
                stats["created" if o["op"] == "create" else "updated"] += 1
                if o["op"] == "create":
                    back.append({"url": o["url"], "notion": hit["page_url"]})
            save_state(state_path, state)
            if back:
                sheet.update(TABS[tab]["update"], rows=back)
        stats["pending"] = 0
    out["cost"] = cost
    return out
