#!/usr/bin/env python3
"""Stand-in for `codex exec` in tests: checks the flags, logs the call, answers per job from title markers."""
import json, os, re, sys

args = sys.argv[1:]
assert args[0] == "exec", args
assert args[args.index("--sandbox") + 1] == "read-only", args
schema = json.load(open(args[args.index("--output-schema") + 1]))
assert schema["properties"]["jobs"]["items"]["required"]
out = args[args.index("-o") + 1]
prompt = sys.stdin.read()
log = os.environ.get("FAKE_CODEX_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"n_jobs": prompt.count("### Job id="), "has_profile": "# Candidate profile" in prompt,
                            "has_cards": "DT1" in prompt}) + "\n")
if os.environ.get("FAKE_CODEX_FAIL"):
    sys.exit(2)
jobs = []
for jid, body in re.findall(r"### Job id=(\S+)\n(.*?)(?=\n### Job id=|\Z)", prompt, re.S):
    title = re.search(r"Title: (.*)", body).group(1).strip()
    j = {"id": jid, "company": "Fixture Labs", "title": title or "AI Agent Engineer", "type": "AI agent", "score": 9, "reasons": ["LangGraph agent(DT1)對應", "FastAPI 服務(PS)"],
         "location": {"city": "Berlin", "country": "德國", "mode": "Hybrid"}, "min_years": 4,
         "level_excluded": False, "german_required": False}
    if "Staff" in title:
        j.update(level_excluded=True, score=8)
    if "Deutsch" in title:
        j.update(german_required=True, score=7)
    if "Vision" in title:
        j.update(type="ML learning", score=6, location={"city": "", "country": "荷蘭", "mode": "Remote"})
    if "Skip" in title:
        continue
    jobs.append(j)
json.dump({"jobs": jobs}, open(out, "w"), ensure_ascii=False)
