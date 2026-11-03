"""Read/write the AI Job Tracker Google Sheet with the Google Sheets API (sheets_api.py, shared Google login in
google_auth.py). The Apps Script web app is only used for the one-time legacy --migrate / --delete (or with
PUSH_TRANSPORT=apps-script); its onEdit trigger (結束追蹤日期) keeps running from the deployed project.

Config (never commit it): ~/.config/ai-job-tracker/sheet.json
    {"spreadsheet_id": "<optional, default = the tracker>", "sheet_url": "...", "default_status": "準備中",
     "url": "<legacy Apps Script /exec>", "token": "<legacy>"}
    tests add "api_base": "http://127.0.0.1:<fake>", "access_token": "fake"

Usage:
    python push_to_sheet.py rows.json            # rows.json = list of job dicts, or a dir of *.json job docs
    python push_to_sheet.py --list               # print URLs already in both tabs
    python push_to_sheet.py --migrate exclude.json  # one-time: move auto-added rows from 求職追蹤表 to 求職追蹤表_AI 投遞, dropping excluded URLs
    python push_to_sheet.py --delete urls.json  # delete auto-added rows whose job link is in urls.json
    python push_to_sheet.py --fix-dropdown      # copy the 狀態 dropdown rule onto rows that lack it
    python push_to_sheet.py --statuses          # show the 狀態 dropdown options and the current default
    python push_to_sheet.py --set-default-status <value>   # default 狀態 for new rows
    python push_to_sheet.py --set-status rows.json  # rows with "status" (+ "note"): update the existing row's 狀態
                                                    # (matched by URL or company+title); rows not found are appended;
                                                    # 投遞表單 rows with the same URL get the same 狀態

Daily pipeline tabs (每日職缺 · 興趣職缺 · 投遞表單). These commands
talk to the real Sheet only with --live (or when AI_JOB_TRACKER_SHEET_CONFIG names a config explicitly, e.g. the fake):
    python push_to_sheet.py --setup-pipeline               # create/format the tabs + the two 依日期 views (idempotent)
    python push_to_sheet.py --pipeline-backfill [狀態]       # copy 求職追蹤表_AI 投遞 rows with 狀態 (default 準備中) into 每日職缺
    python push_to_sheet.py --pipeline-append rows.json     # [{date, company, title, url, type, city, country, mode, score, reasons}]
    python push_to_sheet.py --pipeline-list [--all]         # queue (決定=是 & 處理狀態=待決定) + undecided (+ all rows)
    python push_to_sheet.py --pipeline-update rows.json     # [{url, decision|note|state|score|reasons|submission_row|notion|…}]
    python push_to_sheet.py --interest-list [--all]         # 興趣職缺 rows with empty 處理狀態 (+ pending_cv)
    python push_to_sheet.py --interest-append rows.json     # [{url, note}] (date = today)
    python push_to_sheet.py --interest-update rows.json
    python push_to_sheet.py --submissions-append rows.json  # [{company, title, url, type, source, …, cv, cv_label, cover_letter, app_dir, notion}]
    python push_to_sheet.py --submissions-list
    python push_to_sheet.py --submissions-update rows.json  # [{url, referrers, referral_message, referral_data, notion, cv, …}]
    python push_to_sheet.py --referral-sent rows.json       # [{url, name}] → 內推狀態 ⚠️等待回覆 + 求職過程 line
    python push_to_sheet.py --action NAME [body.json|-]     # any action, JSON in/out (the dashboard uses this)
  rows.json may be "-" (stdin).

Env: AI_JOB_TRACKER_SHEET_CONFIG = alternative config path (tests point it at the fake Sheets server).
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also under `python3 -I` (no script dir on sys.path)
import google_auth  # noqa: E402
import sheets_api  # noqa: E402

CONFIG = Path(os.environ.get("AI_JOB_TRACKER_SHEET_CONFIG") or Path.home() / ".config" / "ai-job-tracker" / "sheet.json")
BATCH = 200
APPS_SCRIPT_ONLY = {"migrate", "delete_rows"}  # one-time legacy actions, still on the deployed web app


class SheetError(RuntimeError):
    pass


def config():
    return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}


def apps_script(payload):
    """Legacy transport: the deployed Apps Script web app (sheet.json url + token)."""
    cfg = config()
    if not cfg.get("url"):
        raise SheetError("sheet.json 沒有 Apps Script url")
    req = urllib.request.Request(cfg["url"], data=json.dumps({**payload, "token": cfg.get("token")}, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "text/plain;charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:  # 302 → GET result page
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise SheetError(str(result.get("error")))
    return result


def request(payload):
    """One action through the Google Sheets API backend (sheets_api.py); raises SheetError."""
    action = payload.get("action") or "append"
    if action in APPS_SCRIPT_ONLY or os.environ.get("PUSH_TRANSPORT") == "apps-script":
        return apps_script(payload)
    try:
        return sheets_api.Backend.from_config(CONFIG).call(action, payload)
    except google_auth.AuthError as e:
        raise SheetError(str(e))
    except sheets_api.SheetError as e:
        raise SheetError(str(e))
    except RuntimeError as e:  # Google API errors after retries
        raise SheetError(str(e))


def call(payload):
    try:
        return request(payload)
    except SheetError as e:
        sys.exit(f"Sheet error: {e}")


def pipeline(action, **kw):
    return request({"action": action, **kw})


def load_rows(path):
    p = Path(path)
    if p.is_dir():
        rows = [json.loads(f.read_text(encoding="utf-8")) for f in p.glob("*.json")]
    else:
        rows = json.loads(p.read_text(encoding="utf-8"))
    # ArtifactData batch format: [{"op": "set", "data": {...}}, ...]
    rows = [r.get("data", r) for r in rows]
    return sorted(rows, key=lambda r: (r.get("added", ""), -int(r.get("priority") or 0)))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


PIPELINE_FILE_CMDS = {
    "--pipeline-append": "pipeline_append", "--pipeline-update": "pipeline_update",
    "--interest-append": "interest_append", "--interest-update": "interest_update",
    "--submissions-append": "submissions_append", "--submissions-update": "submissions_update",
    "--referral-sent": "referral_sent",
}
PIPELINE_LIST_CMDS = {"--pipeline-list": "pipeline_list", "--interest-list": "interest_list",
                      "--submissions-list": "submissions_list", "--setup-pipeline": "setup_pipeline",
                      "--version": "version"}


def pipeline_main(args):
    live = "--live" in args
    args = [a for a in args if a != "--live"]
    if not live and not os.environ.get("AI_JOB_TRACKER_SHEET_CONFIG"):
        sys.exit("預設不連正式 Google Sheet:加 --live 才會讀寫正式 Sheet(測試請把 AI_JOB_TRACKER_SHEET_CONFIG 指向假伺服器)")
    try:
        if args[0] in PIPELINE_FILE_CMDS and len(args) == 2:
            rows = json.loads(sys.stdin.read()) if args[1] == "-" else read_json(args[1])
            rows = [r.get("data", r) for r in rows]
            r = pipeline(PIPELINE_FILE_CMDS[args[0]], rows=rows)
        elif args[0] in PIPELINE_LIST_CMDS and len(args) <= 2:
            r = pipeline(PIPELINE_LIST_CMDS[args[0]], all="--all" in args[1:])
        elif args[0] == "--action" and len(args) in (2, 3):  # generic: --action NAME [body.json|-] (the dashboard)
            body = {}
            if len(args) == 3:
                body = json.loads(sys.stdin.read() if args[2] == "-" else Path(args[2]).read_text(encoding="utf-8"))
            r = pipeline(args[1], **body)
        elif args[0] == "--pipeline-backfill" and len(args) <= 2:
            r = pipeline("pipeline_backfill", status=args[1] if len(args) == 2 else "準備中")
        else:
            sys.exit(__doc__)
    except SheetError as e:
        sys.exit(f"Sheet error: {e}")
    r.pop("ok", None)
    print(json.dumps(r, ensure_ascii=False, indent=2))


def main():
    args = sys.argv[1:]
    if args and args[0] == "--live" and len(args) > 1:
        args = args[1:] + ["--live"]
    if args and (args[0] in PIPELINE_FILE_CMDS or args[0] in PIPELINE_LIST_CMDS or args[0] in ("--pipeline-backfill", "--action")):
        return pipeline_main(args)
    args = [x for x in args if x != "--live"]  # legacy commands are not gated; accept the flag anywhere
    with_arg = {
        "--set-default-status": lambda v: {"action": "set_default_status", "status": v},
        "--migrate": lambda p: {"action": "migrate", "exclude": read_json(p)},
        "--delete": lambda p: {"action": "delete_rows", "urls": read_json(p)},
    }
    no_arg = {"--fix-dropdown": "fix_dropdown", "--statuses": "statuses"}
    if len(args) == 2 and args[0] == "--set-status":
        rows = load_rows(args[1])
        r = call({"action": "set_status", "rows": rows})
        if not r.get("ok"):
            sys.exit(f"set_status failed: {r}")
        added = skipped = 0
        if r["not_found"]:
            a = call({"action": "append", "rows": r["not_found"]})
            added, skipped = a["added"], a["skipped"]
        for x in r["rejected"]:
            print(f"rejected status {x['status']!r} for {x['url']}", file=sys.stderr)
        print(f"updated={len(r['updated'])} added={added} skipped(duplicate)={skipped} rejected={len(r['rejected'])} "
              f"submissions_updated={len(r.get('submissions_updated') or [])}")
        return
    if len(args) == 2 and args[0] in with_arg:
        print(json.dumps(call(with_arg[args[0]](args[1])), ensure_ascii=False))
        return
    if len(args) != 1:
        sys.exit(__doc__)
    if args[0] in no_arg:
        print(json.dumps(call({"action": no_arg[args[0]]}), ensure_ascii=False, indent=2))
        return
    if args[0] == "--list":
        for u in call({"action": "list"})["urls"]:
            print(u)
        return
    rows = load_rows(args[0])
    added = skipped = 0
    for i in range(0, len(rows), BATCH):
        r = call({"action": "append", "rows": rows[i:i + BATCH]})
        added += r["added"]
        skipped += r["skipped"]
    print(f"added={added} skipped(duplicate)={skipped}")


if __name__ == "__main__":
    main()
