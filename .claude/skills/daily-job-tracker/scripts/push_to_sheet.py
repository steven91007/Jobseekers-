"""Push job rows to the AI Job Tracker Google Sheet via its Apps Script web app.

Config (never commit it): ~/.config/ai-job-tracker/sheet.json
    {"url": "https://script.google.com/macros/s/.../exec", "token": "..."}

Usage:
    python push_to_sheet.py rows.json            # rows.json = list of job dicts, or a dir of *.json job docs
    python push_to_sheet.py --list               # print URLs already in both tabs
    python push_to_sheet.py --migrate exclude.json  # one-time: move auto-added rows from 求職追蹤表 to 求職追蹤表_AI 投遞, dropping excluded URLs
    python push_to_sheet.py --delete urls.json  # delete auto-added rows whose job link is in urls.json
    python push_to_sheet.py --fix-dropdown      # copy the 狀態 dropdown rule onto rows that lack it
    python push_to_sheet.py --statuses          # show the 狀態 dropdown options and the current default
    python push_to_sheet.py --set-default-status <value>   # default 狀態 for new rows
"""
import json
import sys
import urllib.request
from pathlib import Path

CONFIG = Path.home() / ".config" / "ai-job-tracker" / "sheet.json"
BATCH = 200


def call(payload):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload = {**payload, "token": cfg["token"]}
    req = urllib.request.Request(
        cfg["url"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )
    # Apps Script answers POST with a 302 to the result page; urllib follows it as GET.
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        sys.exit(f"Sheet error: {result.get('error')}")
    return result


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


def main():
    args = sys.argv[1:]
    with_arg = {
        "--set-default-status": lambda v: {"action": "set_default_status", "status": v},
        "--migrate": lambda p: {"action": "migrate", "exclude": read_json(p)},
        "--delete": lambda p: {"action": "delete_rows", "urls": read_json(p)},
    }
    no_arg = {"--fix-dropdown": "fix_dropdown", "--statuses": "statuses"}
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
