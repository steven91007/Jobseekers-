#!/usr/bin/env python3
"""Stand-in for `claude -p … --output-format json` in pipeline tests. Logs every prompt + allowedTools."""
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
tools = next(a for a in args if a.startswith("--allowedTools=")).split("=", 1)[1]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps({"prompt": prompt[:4000], "tools": tools, "env_claudecode": "CLAUDECODE" in os.environ}) + "\n")

def reply(obj):
    print(json.dumps({"type": "result", "is_error": False, "total_cost_usd": 0.5,
                      "result": "done\n```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"}))
    sys.exit(0)

if prompt.startswith("/daily-job-tracker"):
    reply(json.loads(Path(os.environ["FAKE_CLAUDE_JOBS"]).read_text()))
if prompt.startswith("Headless daily pipeline run"):
    company = re.search(r"Job: (.*?) —", prompt).group(1)
    if "FailCo" in company:
        reply({"status": "failed", "reason": "JD 無法讀取"})
    out = Path(os.environ["FAKE_CLAUDE_OUT"]) / company.replace(" ", "")
    out.mkdir(parents=True, exist_ok=True)
    (out / "CV.pdf").write_bytes(b"%PDF-1.4 fake")
    (out / "CL.pdf").write_bytes(b"%PDF-1.4 fake")
    reply({"status": "ok", "jd_key": company, "company": company, "title": "T", "app_dir": f"cv/applications/20261101_{company}_T",
           "cv_pdf": str(out / "CV.pdf"), "cover_letter_pdf": str(out / "CL.pdf"), "cover_dir": "", "ats": 72.5,
           "hr": 80, "hm": 78, "verdict": "READY", "letter_verdict": "READY", "pending_approvals": ["NEW LEAD for DT1"]})
if prompt.startswith("Create ONE row in the Notion database"):
    reply({"page_url": "https://www.notion.so/fake-row-" + str(abs(hash(prompt)) % 1000), "attached": 2})
if prompt.startswith("Create ONE Notion page titled"):
    reply({"page_url": "https://www.notion.so/fake-summary"})
if prompt.startswith("Find 2–4 people at"):
    reply(json.loads((Path(__file__).parent / "fixtures" / "referral_search.json").read_text()))
if prompt.startswith("Sync "):
    if os.environ.get("FAKE_NOTION_FAIL"):
        print(json.dumps({"is_error": True, "result": "notion down"}))
        sys.exit(1)
    tab = re.search(r"Notion database (\S+)", prompt).group(1)
    rows = json.loads(prompt.split("Rows (JSON):\n", 1)[1].split("\nEnd with ONE", 1)[0])
    with open(os.environ["FAKE_CLAUDE_LOG"] + ".notion", "a") as f:
        for r in rows:
            f.write(json.dumps({"tab": tab, **r}, ensure_ascii=False) + "\n")
    reply({"results": [{"key": r["key"], "ok": True, "page_url": r.get("page_url") or
                        "https://www.notion.so/" + ("daily" if tab == "每日職缺" else "interest") + "-" + re.sub(r"[^a-z0-9]", "", r["key"])[-12:]}
                       for r in rows]})
if prompt.startswith("Find the job description"):
    reply({"found": True, "company": "Interest Co", "title": "ML Engineer", "url_used": "https://interest.example/ml",
           "jd": "Train computer vision models with PyTorch. 3+ years."})
print(json.dumps({"is_error": True, "result": "unknown prompt"}))
sys.exit(1)
