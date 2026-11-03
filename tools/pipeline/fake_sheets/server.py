#!/usr/bin/env python3
"""Fake Google Sheets REST API (in memory) for the pipeline tests — no Google calls.

    python3 server.py [--port 0] [--seed seed.json]      → prints "LISTENING <port>"

Endpoints:
  GET  /v4/spreadsheets/<id>[?includeGridData=true&ranges='Tab']   (fields masks are accepted and ignored)
  POST /v4/spreadsheets/<id>:batchUpdate    addSheet, updateCells, setDataValidation, repeatCell, copyPaste,
                                            updateSheetProperties, updateDimensionProperties, appendDimension
  GET  /v4/spreadsheets/<id>/values/<A1>
  POST /            {"action": …, …}  → runs that action through the REAL sheets_api.Backend against this fake
  GET  /__dump      every tab: display / formulas / links / validations / frozen / id
  GET  /__log       every API request received (method + path)
  POST /__reset     reload the seed
Grid limits are enforced like the real API (writes outside rowCount/columnCount → 400).
"""
import argparse
import copy
import datetime as dt
import json
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[2] / ".claude" / "skills" / "daily-job-tracker" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import sheets_api  # noqa: E402

EPOCH = dt.date(1899, 12, 30)
SID = sheets_api.SPREADSHEET_ID


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def render(cell):
    """Computed fields the real API returns: formattedValue, effectiveValue, hyperlink."""
    out = {k: v for k, v in cell.items() if k in ("userEnteredValue", "textFormatRuns", "dataValidation", "userEnteredFormat")}
    uev = cell.get("userEnteredValue") or {}
    if "formulaValue" in uev:
        m = re.match(r'=HYPERLINK\("([^"]*)","([^"]*)"\)$', uev["formulaValue"])
        if m:
            out.update(formattedValue=m[2], hyperlink=m[1], effectiveValue={"stringValue": m[2]})
    elif "numberValue" in uev:
        n = uev["numberValue"]
        out["effectiveValue"] = {"numberValue": n}
        nf = (cell.get("userEnteredFormat") or {}).get("numberFormat") or {}
        out["formattedValue"] = (EPOCH + dt.timedelta(days=int(n))).isoformat() if nf.get("type") == "DATE" else \
            (str(int(n)) if float(n).is_integer() else str(n))
    elif "stringValue" in uev:
        out["formattedValue"] = uev["stringValue"]
        out["effectiveValue"] = {"stringValue": uev["stringValue"]}
        runs = cell.get("textFormatRuns") or []
        if runs and (runs[0].get("format") or {}).get("link"):
            out["hyperlink"] = runs[0]["format"]["link"]["uri"]
    elif "boolValue" in uev:
        out["formattedValue"] = "TRUE" if uev["boolValue"] else "FALSE"
    return out


class Sheet:
    def __init__(self, sid, title, index, rows=1000, cols=26):
        self.id, self.title, self.index = sid, title, index
        self.rows, self.cols, self.frozen = rows, cols, 0
        self.cells = {}  # (r, c) 0-based → raw cell
        self.widths = {}

    def check(self, r, c):
        if r >= self.rows or c >= self.cols or r < 0 or c < 0:
            raise ApiError(400, f"Range ({self.title}!R{r + 1}C{c + 1}) exceeds grid limits. Max rows: {self.rows}, max columns: {self.cols}")

    def get(self, r, c):
        return self.cells.setdefault((r, c), {})

    def props(self):
        return {"sheetId": self.id, "title": self.title, "index": self.index,
                "gridProperties": {"rowCount": self.rows, "columnCount": self.cols, "frozenRowCount": self.frozen}}

    def grid(self):
        if not self.cells:
            return [{"startRow": 0, "rowData": [], "columnMetadata": [{"pixelSize": self.widths.get(c, 100)} for c in range(self.cols)]}]
        by_row = {}
        for (r, c) in self.cells:
            by_row[r] = max(by_row.get(r, 0), c + 1)
        nr = max(by_row) + 1
        rows = [{"values": [render(self.cells.get((r, c), {})) for c in range(by_row[r])]} if r in by_row else {}
                for r in range(nr)]
        return [{"startRow": 0, "rowData": rows, "columnMetadata": [{"pixelSize": self.widths.get(c, 100)} for c in range(self.cols)]}]


class FakeSheets:
    def __init__(self, seed):
        self.seed = seed
        self.reset()

    def reset(self):
        self.sheets, self.log, self.next_id = [], [], 1000
        for s in self.seed["sheets"]:
            rows = s.get("rows") or []
            sh = Sheet(s.get("id") or self.new_id(), s["name"], len(self.sheets),
                       rows=max(1000, len(rows) + 10), cols=max(26, max((len(r) for r in rows), default=0)))
            self.sheets.append(sh)
            for i, row in enumerate(rows):
                for j, v in enumerate(row):
                    if v in ("", None):
                        continue
                    cell = sh.get(i, j)
                    if isinstance(v, dict) and "date" in v:
                        cell["userEnteredValue"] = {"numberValue": (dt.date.fromisoformat(v["date"]) - EPOCH).days}
                        cell["userEnteredFormat"] = {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}
                    elif isinstance(v, dict) and "f" in v:
                        cell["userEnteredValue"] = {"formulaValue": v["f"]}
                    elif isinstance(v, dict):
                        cell["userEnteredValue"] = {"stringValue": str(v.get("v", ""))}
                        if v.get("link"):
                            cell["textFormatRuns"] = [{"startIndex": 0, "format": {"link": {"uri": v["link"]}}}]
                    elif isinstance(v, (int, float)):
                        cell["userEnteredValue"] = {"numberValue": v}
                    else:
                        cell["userEnteredValue"] = {"stringValue": str(v)}
            head = rows[0] if rows else []
            rules = dict(s.get("validations") or {})
            if s.get("status_options"):
                rules[s.get("status_header", "狀態")] = s["status_options"]
            for header, opts in rules.items():
                c = head.index(header)
                for r in range(1, max(len(rows), 2)):
                    sh.get(r, c)["dataValidation"] = sheets_api.list_rule(opts)

    def new_id(self):
        self.next_id += 1
        return self.next_id

    def by_id(self, sid):
        sh = next((s for s in self.sheets if s.id == sid), None)
        if not sh:
            raise ApiError(400, f"No grid with id: {sid}")
        return sh

    def by_title(self, t):
        return next((s for s in self.sheets if s.title == t), None)

    # ---- REST ----
    def handle(self, method, url, body):
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        path = urllib.parse.unquote(u.path)
        self.log.append({"method": method, "path": path})
        if not path.startswith(f"/v4/spreadsheets/{SID}"):
            raise ApiError(404, "Requested entity was not found.")
        rest = path[len(f"/v4/spreadsheets/{SID}"):]
        if method == "GET" and rest == "":
            grid = q.get("includeGridData", ["false"])[0] == "true"
            want = [r.strip("'").split("!")[0].strip("'") for r in q.get("ranges", [])]
            out = []
            for s in sorted(self.sheets, key=lambda x: x.index):
                if want and s.title not in want:
                    continue
                d = {"properties": s.props()}
                if grid:
                    d["data"] = s.grid()
                out.append(d)
            return {"spreadsheetId": SID, "sheets": out}
        if method == "POST" and rest == ":batchUpdate":
            snapshot = copy.deepcopy((self.sheets, self.next_id))
            try:
                return {"spreadsheetId": SID, "replies": [self.apply(r) for r in body["requests"]]}
            except ApiError:
                self.sheets, self.next_id = snapshot  # batchUpdate is atomic
                raise
        if method == "GET" and rest.startswith("/values/"):
            a1 = rest[len("/values/"):]
            m = re.match(r"'?([^'!]+)'?!([A-Z]+)(\d+):([A-Z]+)(\d+)", a1)
            sh = self.by_title(m[1])
            c = col_index(m[2])
            return {"values": [[render(sh.cells.get((r, c), {})).get("formattedValue", "")] for r in range(int(m[3]) - 1, int(m[5]))]}
        raise ApiError(404, f"unsupported {method} {path}")

    def cells_in(self, rng):
        sh = self.by_id(rng["sheetId"])
        r0, r1 = rng.get("startRowIndex", 0), rng.get("endRowIndex", sh.rows)
        c0, c1 = rng.get("startColumnIndex", 0), rng.get("endColumnIndex", sh.cols)
        sh.check(r1 - 1, c1 - 1)
        return sh, [(r, c) for r in range(r0, r1) for c in range(c0, c1)]

    def apply(self, req):
        (kind, a), = req.items()
        if kind == "addSheet":
            p = a["properties"]
            if self.by_title(p["title"]):
                raise ApiError(400, f'A sheet with the name "{p["title"]}" already exists.')
            g = p.get("gridProperties", {})
            sh = Sheet(self.new_id(), p["title"], p.get("index", len(self.sheets)), g.get("rowCount", 1000), g.get("columnCount", 26))
            self.sheets.append(sh)
            return {"addSheet": {"properties": sh.props()}}
        if kind == "updateCells":
            rng = a["range"]
            sh = self.by_id(rng["sheetId"])
            fields = [f.strip() for f in a["fields"].split(",")]
            for i, row in enumerate(a["rows"]):
                for j, cd in enumerate(row.get("values", [])):
                    r, c = rng["startRowIndex"] + i, rng["startColumnIndex"] + j
                    sh.check(r, c)
                    cell = sh.get(r, c)
                    for f in fields:
                        top = f.split(".")[0]
                        if top == "userEnteredFormat" and "." in f:
                            sub = f.split(".", 1)[1]
                            fmt = cell.setdefault("userEnteredFormat", {})
                            if sub in (cd.get("userEnteredFormat") or {}):
                                fmt[sub] = cd["userEnteredFormat"][sub]
                            else:
                                fmt.pop(sub, None)
                        elif top in cd:
                            cell[top] = copy.deepcopy(cd[top])
                        else:
                            cell.pop(top, None)
            return {}
        if kind == "setDataValidation":
            sh, cells = self.cells_in(a["range"])
            for rc in cells:
                if a.get("rule"):
                    sh.get(*rc)["dataValidation"] = copy.deepcopy(a["rule"])
                else:
                    sh.get(*rc).pop("dataValidation", None)
            return {}
        if kind == "repeatCell":
            sh, cells = self.cells_in(a["range"])
            for rc in cells:
                fmt = (a["cell"].get("userEnteredFormat") or {})
                if fmt and rc in sh.cells:
                    sh.cells[rc].setdefault("userEnteredFormat", {}).update(copy.deepcopy(fmt))
            return {}
        if kind == "copyPaste":
            src, dst = a["source"], a["destination"]
            ssh, scells = self.cells_in(src)
            dsh = self.by_id(dst["sheetId"])
            for (r, c) in scells:
                rr, cc = dst["startRowIndex"] + r - src["startRowIndex"], dst["startColumnIndex"] + c - src["startColumnIndex"]
                dsh.check(rr, cc)
                dsh.cells[(rr, cc)] = copy.deepcopy(ssh.cells.get((r, c), {}))
            return {}
        if kind == "updateSheetProperties":
            sh = self.by_id(a["properties"]["sheetId"])
            g = a["properties"].get("gridProperties", {})
            if "frozenRowCount" in g:
                sh.frozen = g["frozenRowCount"]
            return {}
        if kind == "updateDimensionProperties":
            r = a["range"]
            sh = self.by_id(r["sheetId"])
            if r["dimension"] == "COLUMNS":
                if r["endIndex"] > sh.cols:
                    raise ApiError(400, "column index exceeds grid limits")
                for c in range(r["startIndex"], r["endIndex"]):
                    sh.widths[c] = a["properties"]["pixelSize"]
            return {}
        if kind == "appendDimension":
            sh = self.by_id(a["sheetId"])
            if a["dimension"] == "ROWS":
                sh.rows += a["length"]
            else:
                sh.cols += a["length"]
            return {}
        raise ApiError(400, f"unsupported request {kind}")

    def dump(self):
        out = {}
        for s in self.sheets:
            filled = [(r, c) for (r, c), x in s.cells.items() if x.get("userEnteredValue")]
            nr = max((r for r, _ in filled), default=-1) + 1
            nc = max((c for _, c in filled), default=-1) + 1
            grid = [[render(s.cells.get((r, c), {})) for c in range(nc)] for r in range(nr)]
            keep = [i for i, row in enumerate(grid) if any(x.get("formattedValue") or (x.get("userEnteredValue") or {}).get("formulaValue") for x in row)]
            nr = (keep[-1] + 1) if keep else 0
            grid = grid[:nr]
            out[s.title] = {
                "id": s.id, "frozen": s.frozen,
                "display": [[x.get("formattedValue", "") for x in row] for row in grid],
                "formulas": [[(x.get("userEnteredValue") or {}).get("formulaValue", "") for x in row] for row in grid],
                "links": [[x.get("hyperlink") for x in row] for row in grid],
                "validations": [[(x.get("dataValidation") or {}).get("condition", {}).get("type") and
                                 {"ONE_OF_LIST": "VALUE_IN_LIST", "DATE_IS_VALID": "DATE_IS_VALID_DATE"}.get(
                                     x["dataValidation"]["condition"]["type"], x["dataValidation"]["condition"]["type"])
                                 for x in row] for row in grid],
            }
        return out


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


class Direct:
    """sheets_api Http stand-in that calls the fake in-process (used by the POST / action endpoint)."""
    def __init__(self, fake):
        self.fake = fake

    def request(self, method, url, body=None, headers=None, raw=False):
        try:
            return self.fake.handle(method, url, body)
        except ApiError as e:
            raise RuntimeError(f"Google API {method} → {e.code}: {e}")


def make_server(seed_path, port=0):
    fake = FakeSheets(json.loads(Path(seed_path).read_text(encoding="utf-8")))
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, obj):
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def handle_any(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            with lock:
                try:
                    if self.path == "/__dump":
                        return self.send(200, fake.dump())
                    if self.path == "/__log":
                        return self.send(200, fake.log)
                    if self.path == "/__reset":
                        fake.reset()
                        return self.send(200, {"ok": True})
                    if self.path == "/" and self.command == "POST":
                        body = json.loads(raw or b"{}")
                        backend = sheets_api.Backend(sheets_api.Book(Direct(fake)), {})
                        try:
                            out = backend.call(body.get("action"), body)
                        except sheets_api.SheetError as e:
                            out = {"ok": False, "error": str(e)}
                        return self.send(200, out)
                    if not (self.headers.get("Authorization") or "").startswith("Bearer "):
                        return self.send(401, {"error": {"code": 401, "message": "missing token"}})
                    body = json.loads(raw) if raw else None
                    return self.send(200, fake.handle(self.command, self.path, body))
                except ApiError as e:
                    return self.send(e.code, {"error": {"code": e.code, "message": str(e)}})

        do_GET = do_POST = handle_any

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    return srv, fake


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--seed", default=str(HERE / "seed.json"))
    a = ap.parse_args()
    srv, _ = make_server(a.seed, a.port)
    print(f"LISTENING {srv.server_address[1]}", flush=True)
    srv.serve_forever()
