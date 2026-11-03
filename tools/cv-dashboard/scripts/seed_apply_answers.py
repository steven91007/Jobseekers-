"""Store the candidate's standing Auto-apply answers in the job-apply plugin's answer memory.

The candidate consented (2026-10-31) to both FILLING and REMEMBERING these, including the sensitive
ones, so sensitive records are written with --remember-sensitive. All writes go through the plugin's
own helper (scripts/job-apply-store.py answer-put): ~/.job-apply/answers.json, 0600, atomic.

    python3 tools/cv-dashboard/scripts/seed_apply_answers.py [--plugin-root DIR] [--root STORE_DIR]

Re-running is safe: answer-put replaces the record with the same key.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ANSWERS = [
    {
        "key": "source.how_did_you_hear",
        "question": "How did you hear about this job?",
        "aliases": [
            "How did you hear about us?",
            "How did you hear about this position?",
            "How did you find out about this job?",
            "Where did you hear about this job?",
            "Wie bist du auf uns aufmerksam geworden?",
            "Wie haben Sie von dieser Stelle erfahren?",
        ],
        "value": "LinkedIn",
        "state": "confirmed",
        "source": "candidate 2026-10-31; if LinkedIn is not an option use Job board, else Online",
    },
    {
        "key": "consent.data_protection",
        "question": "Data protection consent",
        "aliases": [
            "Datenschutzrechtliche Einwilligungserklärung / Data protection consent",
            "Datenschutzrechtliche Einwilligungserklärung",
            "Privacy policy consent",
            "I agree to the privacy policy",
            "Ich willige ein / I agree",
        ],
        "value": "checked",
        "state": "confirmed",
        "source": "candidate 2026-10-31: tick data-protection / privacy consent checkboxes",
    },
    {
        "key": "upload.certificates",
        "question": "Certificates",
        "aliases": ["Certificates (pdf, doc, docx, txt, rtf)", "Zeugnisse", "Additional documents"],
        "value": "",
        "state": "confirmed",
        "source": "candidate 2026-10-31: leave certificate uploads empty",
    },
    {
        "key": "eeo.disability",
        "question": "If you have a severe disability or equivalent status, would you like the representative body for employees with severe disabilities to be involved in your application process?",
        "aliases": [
            "Do you have a disability?",
            "Disability status",
            "Schwerbehinderung",
            "Do you have a severe disability?",
        ],
        "value": "No",
        "state": "sensitive",
        "sensitivity": "high",
        "source": "candidate 2026-10-31 (fill + remember consent)",
    },
    {
        "key": "eeo.gender",
        "question": "What gender do you identify as?",
        "aliases": [
            "What gender do you identify as? / Mit welchem Geschlecht identifizierst du dich?",
            "Mit welchem Geschlecht identifizierst du dich?",
            "Gender",
            "Geschlecht",
        ],
        "value": "Male",
        "state": "sensitive",
        "sensitivity": "high",
        "source": "candidate 2026-10-31 (fill + remember consent)",
    },
    {
        "key": "compensation.desired_salary",
        "question": "What is your desired salary?",
        "aliases": [
            "What is your desired salary (gross per year in euro)?",
            "Desired salary",
            "Salary expectations",
            "Gehaltsvorstellung",
            "Expected salary",
        ],
        "value": "0",
        "state": "sensitive",
        "sensitivity": "high",
        "source": "candidate 2026-10-31 rule: range/dropdown -> middle option; free number field -> 0; never invent a figure",
    },
]


def plugin_root() -> Path:
    reg = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    return Path(data["plugins"]["job-apply@neonwatty-plugins"][0]["installPath"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-root", type=Path)
    ap.add_argument("--root", help="store dir (tests); default ~/.job-apply")
    args = ap.parse_args()
    helper = (args.plugin_root or plugin_root()) / "scripts" / "job-apply-store.py"
    base = [sys.executable, "-I", str(helper)] + (["--root", args.root] if args.root else [])
    subprocess.run(base + ["init"], check=True, stdout=subprocess.DEVNULL)
    for rec in ANSWERS:
        fd, tmp = tempfile.mkstemp(prefix="answer-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            extra = ["--remember-sensitive"] if rec["state"] == "sensitive" else []
            r = subprocess.run(base + ["answer-put", "--input", tmp] + extra, capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"answer-put {rec['key']} failed: {r.stderr.strip()}")
            print(f"stored {rec['key']} ({rec['state']})")
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    main()
