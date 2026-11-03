"""Upload tailored CV / cover-letter PDFs to Google Drive "CV for referrals/<Company>/" with "anyone with the link
can view", and return the view links (for referral messages, 投遞表單 and Notion).

Re-uploading a file with the same name into the same company folder replaces its content (same file id → the link
never changes). Scope `drive.file`: the app sees only the files it created.

    python3 drive_upload.py --auth                         # once, interactive: prints the consent URL
    python3 drive_upload.py --upload "<Company>" a.pdf [b.pdf]
    python3 drive_upload.py --drive-backfill [--dry-run]   # the existing cv/applications + cv/cover_letters PDFs

Setup: see google_auth.py (one OAuth login for Drive + Sheets) and tools/pipeline/README.md.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import google_auth

CONF_DIR = Path(os.environ.get("AI_JOB_TRACKER_CONF_DIR") or Path.home() / ".config" / "ai-job-tracker")
FILES = CONF_DIR / "drive_files.json"  # "<Company>/<file name>" → {"id", "link"}
ROOT_FOLDER = "CV for referrals"
BASE = os.environ.get("DRIVE_API_BASE", "https://www.googleapis.com").rstrip("/")  # tests: a local fake Drive
API = f"{BASE}/drive/v3"
UPLOAD = f"{BASE}/upload/drive/v3"
REPO = Path(__file__).resolve().parents[4]
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(RuntimeError):
    pass


def credentials(interactive=False):
    """Shared Google login (Drive + Sheets), see google_auth.py."""
    try:
        return google_auth.credentials(interactive=interactive)
    except google_auth.AuthError as e:
        raise DriveError(str(e))


def urllib_http(method, url, token, body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": f"Bearer {token}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise DriveError(f"Drive API {method} {url.split('?')[0]} → {e.code}: {e.read()[:300]!r}")
    return json.loads(raw) if raw else {}


class Drive:
    def __init__(self, token=None, http=urllib_http, files_path=FILES):
        self._token = token
        self.http = http
        self.files_path = Path(files_path)
        try:
            self.files = json.loads(self.files_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.files = {}
        self.folders = {}

    @property
    def token(self):
        if self._token is None:
            self._token = os.environ.get("DRIVE_ACCESS_TOKEN") or credentials().token
        return self._token

    def call(self, method, url, body=None, headers=None):
        return self.http(method, url, self.token, body, headers)

    def find(self, name, parent=None, folder=False):
        q = [f"name = '{name.replace(chr(39), chr(92) + chr(39))}'", "trashed = false"]
        if parent:
            q.append(f"'{parent}' in parents")
        if folder:
            q.append(f"mimeType = '{FOLDER_MIME}'")
        url = f"{API}/files?" + urllib.parse.urlencode({"q": " and ".join(q), "fields": "files(id,name,webViewLink)", "spaces": "drive"})
        got = self.call("GET", url).get("files", [])
        return got[0] if got else None

    def folder(self, name, parent=None):
        key = (name, parent)
        if key not in self.folders:
            hit = self.find(name, parent, folder=True)
            if not hit:
                meta = {"name": name, "mimeType": FOLDER_MIME, **({"parents": [parent]} if parent else {})}
                hit = self.call("POST", f"{API}/files?fields=id", json.dumps(meta).encode(), {"Content-Type": "application/json"})
            self.folders[key] = hit["id"]
        return self.folders[key]

    def upload(self, company, path):
        """Upload or replace <company>/<basename>; returns the anyone-with-link view URL."""
        path = Path(path)
        company = re.sub(r"[\\/]+", "-", company).strip() or "Unknown"
        key = f"{company}/{path.name}"
        data = path.read_bytes()
        parent = self.folder(company, self.folder(ROOT_FOLDER))
        known = self.files.get(key) or self.find(path.name, parent)
        if known:
            got = self.call("PATCH", f"{UPLOAD}/files/{known['id']}?uploadType=media&fields=id,webViewLink", data,
                            {"Content-Type": "application/pdf"})
        else:
            boundary = "pipeline-" + uuid.uuid4().hex
            meta = json.dumps({"name": path.name, "parents": [parent], "mimeType": "application/pdf"})
            body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
                    f"--{boundary}\r\nContent-Type: application/pdf\r\n\r\n").encode() + data + f"\r\n--{boundary}--".encode()
            got = self.call("POST", f"{UPLOAD}/files?uploadType=multipart&fields=id,webViewLink", body,
                            {"Content-Type": f"multipart/related; boundary={boundary}"})
            self.call("POST", f"{API}/files/{got['id']}/permissions", json.dumps({"role": "reader", "type": "anyone"}).encode(),
                      {"Content-Type": "application/json"})
        link = got.get("webViewLink") or (known or {}).get("link") or f"https://drive.google.com/file/d/{got['id']}/view"
        self.files[key] = {"id": got["id"], "link": link}
        self.files_path.parent.mkdir(parents=True, exist_ok=True)
        self.files_path.write_text(json.dumps(self.files, ensure_ascii=False, indent=1), encoding="utf-8")
        return link


def upload_application(company, cv_pdf=None, cover_letter_pdf=None, drive=None):
    """→ {"cv": link|None, "cover_letter": link|None}; paths are repo-relative or absolute."""
    drive = drive or Drive()
    out = {}
    for k, p in (("cv", cv_pdf), ("cover_letter", cover_letter_pdf)):
        out[k] = drive.upload(company, REPO / p) if p and (REPO / p).exists() else None
    return out


def backfill_plan(repo=REPO):
    """Existing tailored PDFs per company: v3_cards CV + the newest cover letter (a *_v2 dir wins)."""
    plan = {}
    for pdf in sorted(repo.glob("cv/applications/*/v3_cards/CV_*.pdf")):
        co = pdf.stem.rsplit("_", 1)[-1]
        plan.setdefault(co, {})["cv"] = str(pdf.relative_to(repo))
    letters = sorted(repo.glob("cv/cover_letters/*/CoverLetter_*.pdf"), key=lambda p: (p.parent.name.endswith("_v2"), p.parent.name))
    for pdf in letters:  # later entries (v2, newer dates) overwrite earlier ones
        co = pdf.stem.rsplit("_", 1)[-1]
        plan.setdefault(co, {})["cover_letter"] = str(pdf.relative_to(repo))
    return plan


def backfill(dry_run=False, drive=None, out=None):
    plan = backfill_plan()
    if dry_run:
        return plan
    drive = drive or Drive()
    links = {co: upload_application(co, p.get("cv"), p.get("cover_letter"), drive) for co, p in plan.items()}
    out = Path(out or REPO / "data" / "pipeline" / "drive_links.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    prev.update(links)
    out.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
    return links


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auth", action="store_true")
    ap.add_argument("--upload", nargs="+", metavar=("COMPANY", "PDF"))
    ap.add_argument("--drive-backfill", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        if a.auth:
            credentials(interactive=True)
            print(f"已授權(Drive + Sheets),token 存在 {google_auth.TOKEN}")
        elif a.upload:
            d = Drive()
            print(json.dumps({p: d.upload(a.upload[0], p) for p in a.upload[1:]}, ensure_ascii=False, indent=2))
        elif a.drive_backfill:
            print(json.dumps(backfill(dry_run=a.dry_run), ensure_ascii=False, indent=2))
        else:
            ap.print_help()
    except DriveError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
