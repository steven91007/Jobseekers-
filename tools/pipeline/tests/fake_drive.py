"""In-memory fake of the Drive v3 endpoints drive_upload.py uses; as a callable or as an HTTP server."""
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeDrive:
    def __init__(self):
        self.files = {}  # id → {name, parents, mime, data, anyone}
        self.calls = []
        self.n = 0

    def _new(self, meta, data=b""):
        self.n += 1
        fid = f"F{self.n}"
        self.files[fid] = {"name": meta["name"], "parents": meta.get("parents", []), "mime": meta.get("mimeType"),
                           "data": data, "anyone": False}
        return fid

    def link(self, fid):
        return f"https://drive.google.com/file/d/{fid}/view?usp=drivesdk"

    def __call__(self, method, url, token, body=None, headers=None):
        assert token, "no token"
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        self.calls.append((method, u.path))
        if method == "GET" and u.path == "/drive/v3/files":
            expr = q["q"][0]
            name = re.search(r"name = '((?:[^'\\]|\\.)*)'", expr).group(1).replace("\\'", "'")
            parent = (re.search(r"'([^']+)' in parents", expr) or [None, None])[1]
            folder = "mimeType" in expr
            hits = [{"id": i, "name": f["name"], "webViewLink": self.link(i)} for i, f in self.files.items()
                    if f["name"] == name and (not parent or parent in f["parents"])
                    and (not folder or f["mime"] == "application/vnd.google-apps.folder")]
            return {"files": hits}
        if method == "POST" and u.path == "/drive/v3/files":
            return {"id": self._new(json.loads(body))}
        if method == "POST" and u.path.startswith("/upload/drive/v3/files"):
            boundary = {k.lower(): v for k, v in headers.items()}["content-type"].split("boundary=")[1]
            parts = body.split(("--" + boundary).encode())
            meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
            data = parts[2].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
            fid = self._new(meta, data)
            return {"id": fid, "webViewLink": self.link(fid)}
        m = re.match(r".*/upload/drive/v3/files/([^/]+)$", u.path)
        if method == "PATCH" and m:
            self.files[m.group(1)]["data"] = body
            return {"id": m.group(1), "webViewLink": self.link(m.group(1))}
        m = re.match(r".*/drive/v3/files/([^/]+)/permissions$", u.path)
        if method == "POST" and m:
            assert json.loads(body) == {"role": "reader", "type": "anyone"}
            self.files[m.group(1)]["anyone"] = True
            return {"id": "perm"}
        raise AssertionError(f"unexpected {method} {url}")


def serve(fake):
    """Run `fake` as an HTTP server (for subprocess tests); returns (server, base_url)."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def handle_any(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
            try:
                out = json.dumps(fake(self.command, f"http://x{self.path}", tok, body, dict(self.headers))).encode()
                self.send_response(200)
            except Exception as e:
                out = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = do_PATCH = handle_any

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
