"""Start the fake Google Sheets REST server (tools/pipeline/fake_sheets/server.py) for tests."""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "fake_sheets" / "server.py"
REPO = HERE.parents[2]
SCRIPTS = REPO / ".claude" / "skills" / "daily-job-tracker" / "scripts"


class FakeSheet:
    def __init__(self, seed=None):
        self.tmp = tempfile.TemporaryDirectory()
        cmd = [sys.executable, str(SERVER), "--port", "0"]
        if seed:
            cmd += ["--seed", str(seed)]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        line = self.proc.stdout.readline().strip()
        assert line.startswith("LISTENING"), line
        self.url = f"http://127.0.0.1:{line.split()[1]}"
        self.config = Path(self.tmp.name) / "sheet.json"
        self.config.write_text(json.dumps({"api_base": self.url, "access_token": "fake-token",
                                           "sheet_url": "https://docs.google.com/spreadsheets/d/FAKE/edit"}))
        self.env = {**os.environ, "AI_JOB_TRACKER_SHEET_CONFIG": str(self.config)}

    def post(self, path, body=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(body or {}).encode(), method="POST")
        return json.loads(urllib.request.urlopen(req).read())

    def call(self, action, **kw):
        return self.post("/", {"token": "test-token", "action": action, **kw})

    def dump(self):
        return json.loads(urllib.request.urlopen(self.url + "/__dump").read())

    def table(self, name):
        """Rows of a tab as dicts keyed by its header row."""
        d = self.dump()[name]["display"]
        return [dict(zip(d[0], r)) for r in d[1:] if any(r)]

    def reset(self):
        self.post("/__reset")

    def close(self):
        self.proc.terminate()
        self.proc.wait(5)
        self.proc.stdout.close()
        self.tmp.cleanup()
