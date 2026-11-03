"""Stand-in for daily-job-tracker/scripts/push_to_sheet.py --set-status: records the rows to $FAKE_SHEET_OUT."""
import json, os, sys
args = [a for a in sys.argv[1:] if a != "--live"]  # the server adds --live under PIPELINE_SHEET_LIVE=1
assert args[0] == "--set-status", sys.argv
rows = json.load(open(args[1], encoding="utf-8"))
with open(os.environ["FAKE_SHEET_OUT"], "a", encoding="utf-8") as f:
    f.write(json.dumps(rows, ensure_ascii=False) + "\n")
print(f"updated=0 added={len(rows)} skipped(duplicate)=0 rejected=0")
