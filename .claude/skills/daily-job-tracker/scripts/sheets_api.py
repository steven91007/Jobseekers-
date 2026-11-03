"""Google Sheets API backend for the job tracker (replaces the Apps Script web app for the pipeline).

Same actions and semantics as apps_script.gs: legacy `append` / `list` / `statuses` / `set_default_status` /
`fix_dropdown` / `set_status`, and the pipeline tabs 每日職缺 · 興趣職缺 · 投遞表單 (+ the two 依日期 views).
Reads: spreadsheets.get with includeGridData + field masks (rich-text links, formulas, data validation).
Writes: one spreadsheets.batchUpdate per action (updateCells / setDataValidation / …), retried on 429/5xx.
Only cells the pipeline owns are written; the candidate's rows, formats and formulas are never touched
(on the legacy tabs only 狀態 / 求職過程 of a matched row, as before).

    backend = Backend.from_config()          # ~/.config/ai-job-tracker/sheet.json (+ the shared Google login)
    backend.call("pipeline_list", {"all": True})
"""
import datetime as dt
import json
import os
import re
import urllib.parse
from pathlib import Path

import google_auth

SPREADSHEET_ID = "1G8xp6GmlGBZ0vOWvn4oPYXDIToh7SkS31CeRNn8OoHo"
API_BASE = "https://sheets.googleapis.com"
VERSION = "sheets-api-1"
MAIN_GID = 2074323844
MAIN_NAME = "求職追蹤表"
AUTO_NAME = "求職追蹤表_AI 投遞"
AUTO_TAG = "每日職缺追蹤"
PIPE_NAME, INTEREST_NAME, SUBMIT_NAME = "每日職缺", "興趣職缺", "投遞表單"
TZ = dt.timezone(dt.timedelta(hours=8))
EPOCH = dt.date(1899, 12, 30)

TYPE_OPTIONS = ["AI agent", "ML learning", "automation"]
MODE_OPTIONS = ["Hybrid", "Remote", "On-site", "未註明"]
DECISION_OPTIONS = ["未確認", "是", "否"]
PIPE_STATE_OPTIONS = ["待決定", "已產 CV", "已投遞", "略過", "失敗"]
INTEREST_STATE_OPTIONS = ["待產 CV", "已產 CV", "已投遞", "略過", "失敗"]
SOURCE_OPTIONS = ["AI 找的", "興趣"]
REFERRAL_SENT = "⚠️等待回覆"

# (key, 標題, 欄寬, 選項)
PIPE_COLS = [
    ("date", "日期", 95), ("company", "公司", 150), ("title", "職位", 240), ("url", "連結", 220),
    ("type", "類型", 105, TYPE_OPTIONS), ("city", "城市", 100), ("country", "國家", 80),
    ("mode", "工作型態", 90, MODE_OPTIONS), ("score", "評分", 55, "score"), ("reasons", "評分理由", 320),
    ("decision", "決定", 75, DECISION_OPTIONS), ("note", "備註（人工）", 220),
    ("state", "處理狀態", 90, PIPE_STATE_OPTIONS), ("submission_row", "投遞表單列", 90), ("notion", "Notion", 80),
]
INTEREST_COLS = [
    ("date", "日期", 95), ("url", "連結", 240), ("note", "備註（人工）", 220), ("company", "公司", 150),
    ("title", "職位", 240), ("type", "類型", 105, TYPE_OPTIONS), ("city", "城市", 100), ("country", "國家", 80),
    ("mode", "工作型態", 90, MODE_OPTIONS), ("score", "評分", 55, "score"), ("reasons", "評分理由", 320),
    ("state", "處理狀態", 90, INTEREST_STATE_OPTIONS), ("submission_row", "投遞表單列", 90), ("notion", "Notion", 80),
]
SUBMIT_EXTRA = [
    ("date", "日期", 95), ("url", "連結", 220), ("type", "類型", 105, TYPE_OPTIONS), ("source", "來源", 80, SOURCE_OPTIONS),
    ("city", "城市", 100), ("country", "國家", 80), ("mode", "工作型態", 90, MODE_OPTIONS), ("ats", "ATS", 60),
    ("app_dir", "應用資料夾", 260), ("notion", "Notion", 80), ("cv", "CV 連結", 110), ("cover_letter", "Cover letter 連結", 110),
    ("referral_message", "內推訊息", 320), ("referral_data", "內推資料", 120),
]
TABS = {PIPE_NAME: {"cols": PIPE_COLS, "by_date": PIPE_NAME + "_依日期"},
        INTEREST_NAME: {"cols": INTEREST_COLS, "by_date": INTEREST_NAME + "_依日期"}}
LINK_KEYS = {"submission_row", "notion", "cv", "cover_letter", "resume_version"}
SUBMIT_MAIN_KEYS = {"Industry": "industry", "順序": "seq", "狀態": "status", "公司": "company", "規模": "scale",
                    "應徵條件": "conditions", "地點": "location", "求職過程": "process", "內推人": "referrers",
                    "內推狀態": "referral_status", "面試紀錄": "interview_notes", "面試反思": "interview_reflection",
                    "面試回饋": "interview_feedback", "履歷版本": "resume_version", "結束追蹤日期": "end_date"}
CONFIG = Path(os.environ.get("AI_JOB_TRACKER_SHEET_CONFIG") or Path.home() / ".config" / "ai-job-tracker" / "sheet.json")
GRID_FIELDS = ("sheets(properties(sheetId,title,index,gridProperties),data(startRow,columnMetadata(pixelSize),"
               "rowData(values(userEnteredValue,effectiveValue,formattedValue,hyperlink,textFormatRuns,dataValidation))))")


class SheetError(RuntimeError):
    pass


# ---- values ----------------------------------------------------------------

class Formula(str):
    pass


class Link:
    def __init__(self, text, url):
        self.text, self.url = str(text), url


class Date:
    def __init__(self, iso):
        self.iso = iso

    @property
    def serial(self):
        return (dt.date.fromisoformat(self.iso) - EPOCH).days


def today():
    return dt.datetime.now(TZ).date().isoformat()


def to_date(v):
    if isinstance(v, Date):
        return v
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(v or ""))
    return Date(f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}") if m else None


def mmdd(v=None):
    d = to_date(v) or Date(today())
    return d.iso[5:7] + "/" + d.iso[8:10]


def norm_mode(v):
    s = str(v or "").lower()
    if re.search(r"hybrid|混合", s):
        return "Hybrid"
    if re.search(r"remote|遠端", s):
        return "Remote"
    if re.search(r"on-?site|office|辦公室|現場", s):
        return "On-site"
    return "未註明"


def url_key(u):
    s = str(u or "").strip()
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d+)", s) or re.search(r"linkedin\.com/jobs/.*[?&]currentJobId=(\d+)", s)
    if m:
        return "li:" + m.group(1)
    return re.sub(r"^https?://(www\.)?", "", s.split("#")[0].split("?")[0], flags=re.I).rstrip("/").lower()


def pair_key(company, title):
    norm = lambda v: re.sub(r"[^a-z0-9一-鿿]+", "", re.sub(r"\(.*?\)", "", str(v or "").lower()))
    return "ct:" + norm(company) + "|" + norm(title)


def hyperlink(url, label):
    return Formula(f'=HYPERLINK("{str(url).replace(chr(34), "%22")}","{str(label).replace(chr(34), chr(39))}")')


def cell_data(v):
    """Python value → (CellData, fields mask)."""
    if isinstance(v, Link):
        return ({"userEnteredValue": {"stringValue": v.text},
                 "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": v.url}}}]}, "userEnteredValue,textFormatRuns")
    if isinstance(v, Formula):
        return {"userEnteredValue": {"formulaValue": str(v)}}, "userEnteredValue"
    if isinstance(v, Date):
        return ({"userEnteredValue": {"numberValue": v.serial},
                 "userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}},
                "userEnteredValue,userEnteredFormat.numberFormat")
    if isinstance(v, bool):
        return {"userEnteredValue": {"boolValue": v}}, "userEnteredValue"
    if isinstance(v, (int, float)):
        return {"userEnteredValue": {"numberValue": v}}, "userEnteredValue"
    return {"userEnteredValue": {"stringValue": "" if v is None else str(v)}}, "userEnteredValue"


def list_rule(options):
    return {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": o} for o in options]},
            "strict": True, "showCustomUi": True}


# ---- spreadsheet model -------------------------------------------------------

class Tab:
    def __init__(self, props, data=None):
        self.id = props["sheetId"]
        self.title = props["title"]
        self.index = props.get("index", 0)
        g = props.get("gridProperties", {})
        self.rows, self.cols, self.frozen = g.get("rowCount", 1000), g.get("columnCount", 26), g.get("frozenRowCount", 0)
        self.grid, self.widths = [], []
        for d in data or []:
            start = d.get("startRow", 0)
            for i, rd in enumerate(d.get("rowData", [])):
                while len(self.grid) <= start + i:
                    self.grid.append([])
                self.grid[start + i] = list(rd.get("values", []))
            self.widths = [m.get("pixelSize", 100) for m in d.get("columnMetadata", [])] or self.widths

    def cell(self, r, c):
        row = self.grid[r - 1] if 0 < r <= len(self.grid) else []
        return row[c - 1] if 0 < c <= len(row) else {}

    def put(self, r, c, cd):
        while len(self.grid) < r:
            self.grid.append([])
        row = self.grid[r - 1]
        while len(row) < c:
            row.append({})
        disp = cd.get("userEnteredValue", {})
        new = dict(cd)
        if "formulaValue" in disp:
            m = re.match(r'=HYPERLINK\("([^"]*)","([^"]*)"\)', disp["formulaValue"])
            new["formattedValue"] = m[2] if m else ""
            if m:
                new["hyperlink"] = m[1]
        elif "numberValue" in disp:
            n = disp["numberValue"]
            new["effectiveValue"] = {"numberValue": n}
            new["formattedValue"] = (EPOCH + dt.timedelta(days=int(n))).isoformat() if cd.get("userEnteredFormat") else \
                (str(int(n)) if float(n).is_integer() else str(n))
        elif "stringValue" in disp:
            new["formattedValue"] = disp["stringValue"]
            new["effectiveValue"] = {"stringValue": disp["stringValue"]}
            runs = cd.get("textFormatRuns") or []
            if runs and runs[0].get("format", {}).get("link"):
                new["hyperlink"] = runs[0]["format"]["link"]["uri"]
        if "dataValidation" in row[c - 1] and "dataValidation" not in new:
            new["dataValidation"] = row[c - 1]["dataValidation"]
        row[c - 1] = new

    def last_row(self):
        for r in range(len(self.grid), 0, -1):
            if any(disp(x) or formula(x) for x in self.grid[r - 1]):
                return r
        return 0

    def last_col(self):
        return max((len([1 for _ in row]) for row in self.grid), default=0)


def disp(c):
    return str(c.get("formattedValue", "")) if c else ""


def formula(c):
    return (c or {}).get("userEnteredValue", {}).get("formulaValue", "")


def links(c):
    out = [r["format"]["link"]["uri"] for r in (c or {}).get("textFormatRuns", []) if r.get("format", {}).get("link")]
    if c and c.get("hyperlink") and c["hyperlink"] not in out:
        out.insert(0, c["hyperlink"])
    return out


def link(c):
    ls = links(c)
    return ls[0] if ls else None


def number(c):
    return (c or {}).get("effectiveValue", {}).get("numberValue")


class Book:
    def __init__(self, http, spreadsheet_id=SPREADSHEET_ID, base=API_BASE):
        self.http, self.sid, self.base = http, spreadsheet_id, base.rstrip("/")
        self.tabs = {}
        self.reqs = []

    def url(self, suffix="", **params):
        q = urllib.parse.urlencode(params, doseq=True)
        return f"{self.base}/v4/spreadsheets/{self.sid}{suffix}" + (f"?{q}" if q else "")

    def load(self, titles=None):
        """Properties of every tab; grid data for `titles` (None = the tabs the actions use)."""
        meta = self.http.request("GET", self.url(fields="sheets.properties"))
        props = {s["properties"]["title"]: s["properties"] for s in meta.get("sheets", [])}
        main = next((p for p in props.values() if p["sheetId"] == MAIN_GID), props.get(MAIN_NAME))
        want = set(titles or [MAIN_NAME, AUTO_NAME, PIPE_NAME, INTEREST_NAME, SUBMIT_NAME]) & set(props)
        if main:
            want.add(main["title"])
        self.tabs = {t: Tab(p) for t, p in props.items()}
        if want:
            got = self.http.request("GET", self.url(includeGridData="true", fields=GRID_FIELDS,
                                                    ranges=[f"'{t}'" for t in sorted(want)]))
            for s in got.get("sheets", []):
                self.tabs[s["properties"]["title"]] = Tab(s["properties"], s.get("data"))
        self.main_title = main["title"] if main else None
        return self

    def tab(self, title):
        return self.tabs.get(title)

    def main(self):
        if not self.main_title:
            raise SheetError("找不到分頁:" + MAIN_NAME)
        return self.tabs[self.main_title]

    def add_sheet(self, title, cols=26, rows=1000):
        self.flush()
        rep = self.http.request("POST", self.url(":batchUpdate"), {"requests": [{"addSheet": {"properties": {
            "title": title, "index": len(self.tabs), "gridProperties": {"rowCount": rows, "columnCount": cols}}}}]})
        props = rep["replies"][0]["addSheet"]["properties"]
        props.setdefault("gridProperties", {"rowCount": rows, "columnCount": cols})
        self.tabs[title] = Tab(props)
        return self.tabs[title]

    def rng(self, tab, r, c, nr=1, nc=1):
        return {"sheetId": tab.id, "startRowIndex": r - 1, "endRowIndex": r - 1 + nr,
                "startColumnIndex": c - 1, "endColumnIndex": c - 1 + nc}

    def ensure(self, tab, rows=0, cols=0):
        if rows > tab.rows:
            self.reqs.append({"appendDimension": {"sheetId": tab.id, "dimension": "ROWS", "length": rows - tab.rows + 50}})
            tab.rows = rows + 50
        if cols > tab.cols:
            self.reqs.append({"appendDimension": {"sheetId": tab.id, "dimension": "COLUMNS", "length": cols - tab.cols}})
            tab.cols = cols

    def set(self, tab, r, c, v):
        if c is None:
            return
        self.ensure(tab, r, c)
        cd, fields = cell_data(v)
        self.reqs.append({"updateCells": {"range": self.rng(tab, r, c), "rows": [{"values": [cd]}], "fields": fields}})
        tab.put(r, c, cd)

    def set_row(self, tab, r, c, values):
        for i, v in enumerate(values):
            self.set(tab, r, c + i, v)

    def validate(self, tab, r, c, rule, n=1, only_missing=False):
        """setDataValidation on [r, r+n); only_missing → only the cells without a rule (fillDropdown_)."""
        if not rule or c is None or n < 1:
            return 0
        self.ensure(tab, r + n - 1, c)
        todo = [x for x in range(r, r + n) if not (only_missing and tab.cell(x, c).get("dataValidation"))]
        runs, start, prev = [], None, None
        for x in todo:
            if start is None or x != prev + 1:
                if start is not None:
                    runs.append((start, prev))
                start = x
            prev = x
        if start is not None:
            runs.append((start, prev))
        for a, b in runs:
            self.reqs.append({"setDataValidation": {"range": self.rng(tab, a, c, b - a + 1), "rule": rule}})
            for x in range(a, b + 1):
                tab.put(x, c, {**tab.cell(x, c), "dataValidation": rule})
        return len(todo)

    def fmt(self, req):
        self.reqs.append(req)

    def flush(self):
        if not self.reqs:
            return []
        reqs, self.reqs = self.reqs, []
        return self.http.request("POST", self.url(":batchUpdate"), {"requests": reqs}).get("replies", [])

    def values(self, a1):
        return self.http.request("GET", self.url("/values/" + urllib.parse.quote(a1))).get("values", [])


# ---- legacy tracker helpers (求職追蹤表 layout) ---------------------------------

def headers(tab):
    for r in range(1, min(10, max(len(tab.grid), 1)) + 1):
        row = [disp(tab.cell(r, c)).strip() for c in range(1, len(tab.grid[r - 1]) + 1)] if r <= len(tab.grid) else []
        if "公司" in row and "Position" in row:
            cols, last = {}, 0
            for i, v in enumerate(row):
                if v:
                    cols.setdefault(v, i + 1)
                    last = i + 1
            return {"row": r, "cols": cols, "last": last}
    raise SheetError("找不到含「公司」與「Position」的標題列:" + tab.title)


def last_data_row(tab, h):
    c1, c2 = h["cols"]["公司"], h["cols"]["Position"]
    for r in range(len(tab.grid), h["row"], -1):
        if disp(tab.cell(r, c1)).strip() or disp(tab.cell(r, c2)).strip():
            return r
    return h["row"]


def existing_urls(tab, h):
    urls = set()
    for r in range(h["row"] + 1, last_data_row(tab, h) + 1):
        for c in range(1, len(tab.grid[r - 1]) + 1):
            x = tab.cell(r, c)
            urls.update(u.strip() for u in links(x))
            urls.update(u.strip() for u in re.findall(r"https?://[^\s,;)]+", disp(x)))
    return urls


def existing_pairs(tab, h):
    return [k for k in (pair_key(disp(tab.cell(r, h["cols"]["公司"])), disp(tab.cell(r, h["cols"]["Position"])))
                        for r in range(h["row"] + 1, last_data_row(tab, h) + 1)) if k != "ct:|"]


def status_rule(tab, h):
    c = h["cols"].get("狀態")
    for r in range(h["row"] + 1, max(len(tab.grid), h["row"] + 1) + 1):
        rule = tab.cell(r, c).get("dataValidation")
        if rule and rule.get("condition", {}).get("type") in ("ONE_OF_LIST", "ONE_OF_RANGE"):
            return rule
    return None


def column_rule(tab, h, title):
    c = h["cols"].get(title)
    if not c:
        return None
    for r in range(h["row"] + 1, max(len(tab.grid), h["row"] + 1) + 1):
        rule = tab.cell(r, c).get("dataValidation")
        if rule:
            return rule
    return None


def max_seq(tab, h, last):
    c = h["cols"].get("順序")
    vals = [number(tab.cell(r, c)) for r in range(h["row"] + 1, last + 1)] if c else []
    return int(max([v for v in vals if v is not None], default=0))


# ---- backend ---------------------------------------------------------------

class Backend:
    def __init__(self, book, config=None, config_path=None):
        self.book, self.config, self.config_path = book, config or {}, config_path

    @classmethod
    def from_config(cls, path=None):
        path = Path(path or CONFIG)
        cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        http = google_auth.Http(token=cfg.get("access_token"))
        return cls(Book(http, cfg.get("spreadsheet_id") or SPREADSHEET_ID, cfg.get("api_base") or API_BASE), cfg, path)

    def call(self, action, body=None):
        body = body or {}
        fn = getattr(self, "a_" + (action or "append"), None)
        if not fn:
            raise SheetError("unknown action: " + str(action))
        self.book.load()
        out = fn(body)
        self.book.flush()
        return {"ok": True, **out}

    # -- legacy tabs ---------------------------------------------------------
    def legacy(self):
        main = self.book.main()
        auto = self.book.tab(AUTO_NAME)
        mh = headers(main)
        for need in ("公司", "Position", "狀態"):
            if need not in mh["cols"]:
                raise SheetError("header not found: " + need)
        if auto is None:  # same as autoSheet_(): copy the header row + widths
            auto = self.book.add_sheet(AUTO_NAME, cols=max(main.cols, mh["last"]))
            self.copy_header(main, mh, auto)
            self.book.flush()
        return main, mh, auto, headers(auto)

    def copy_header(self, main, mh, dst):
        self.book.ensure(dst, 1, mh["last"])
        self.book.fmt({"copyPaste": {"source": self.book.rng(main, mh["row"], 1, 1, mh["last"]),
                                     "destination": self.book.rng(dst, 1, 1, 1, mh["last"]), "pasteType": "PASTE_NORMAL"}})
        for c in range(1, mh["last"] + 1):
            dst.put(1, c, {k: v for k, v in main.cell(mh["row"], c).items() if k != "dataValidation"})
            width = main.widths[c - 1] if c - 1 < len(main.widths) else 100
            self.book.fmt({"updateDimensionProperties": {"range": {"sheetId": dst.id, "dimension": "COLUMNS", "startIndex": c - 1,
                                                                   "endIndex": c}, "properties": {"pixelSize": width}, "fields": "pixelSize"}})
        self.book.fmt({"updateSheetProperties": {"properties": {"sheetId": dst.id, "gridProperties": {"frozenRowCount": 1}},
                                                 "fields": "gridProperties.frozenRowCount"}})
        dst.frozen = 1

    def status_options(self, main, mh):
        rule = status_rule(main, mh)
        if not rule:
            return []
        cond = rule["condition"]
        vals = [v.get("userEnteredValue", "") for v in cond.get("values", [])]
        if cond["type"] == "ONE_OF_LIST":
            return vals
        return [x[0] for x in self.book.values(vals[0].lstrip("=")) if x and x[0]]

    def default_status(self):
        return self.config.get("default_status") or "準備中"

    def a_version(self, b):
        return {"version": VERSION}

    def a_statuses(self, b):
        main, mh, auto, ah = self.legacy()
        used = {}
        for r in range(ah["row"] + 1, last_data_row(auto, ah) + 1):
            v = disp(auto.cell(r, ah["cols"]["狀態"]))
            if v:
                used[v] = used.get(v, 0) + 1
        return {"options": self.status_options(main, mh), "used": used, "default": self.default_status()}

    def a_set_default_status(self, b):
        if not b.get("status"):
            raise SheetError("missing status")
        self.config["default_status"] = str(b["status"])
        if self.config_path:
            self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"default": self.config["default_status"]}

    def a_list(self, b):
        main, mh, auto, ah = self.legacy()
        return {"urls": sorted(existing_urls(main, mh)) + sorted(existing_urls(auto, ah))}

    def a_fix_dropdown(self, b):
        main, mh, auto, ah = self.legacy()
        n = last_data_row(auto, ah) - ah["row"]
        return {"fixed": self.book.validate(auto, ah["row"] + 1, ah["cols"]["狀態"], status_rule(main, mh), n, only_missing=True)}

    def a_append(self, b):
        """Legacy daily-job-tracker append into 求職追蹤表_AI 投遞 (dedupe against both legacy tabs)."""
        main, mh, auto, ah = self.legacy()
        rows = b.get("rows") or []
        seen = set()
        for t, h in ((main, mh), (auto, ah)):
            seen.update(url_key(u) for u in existing_urls(t, h))
            seen.update(existing_pairs(t, h))
        jobs = []
        for j in rows:
            u = str(j.get("url") or "").strip()
            if not u:
                continue
            keys = [url_key(u), pair_key(j.get("company"), j.get("title"))]
            if any(k in seen for k in keys):
                continue
            seen.update(keys)
            jobs.append(j)
        if not jobs:
            return {"added": 0, "skipped": len(rows)}
        last = last_data_row(auto, ah)
        seq = max_seq(auto, ah, last)
        allowed = set(self.status_options(main, mh))
        c = ah["cols"]
        for i, j in enumerate(jobs):
            r = last + 1 + i
            fresh = {
                "順序": seq + i + 1,
                "狀態": j["status"] if j.get("status") and (not allowed or j["status"] in allowed) else self.default_status(),
                "公司": j.get("company") or "",
                "應徵條件": "\n".join(x for x in [j.get("tech") and "技術:" + j["tech"], j.get("lang") and "語言:" + j["lang"],
                                                 j.get("visa") and "簽證:" + j["visa"], j.get("salary") and "薪資:" + j["salary"],
                                                 j.get("mode") and "工作模式:" + j["mode"]] if x),
                "地點": ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                "求職過程": " · ".join(x for x in [(j.get("added") or "") + " 新增(" + AUTO_TAG + ")", j.get("posted") and "刊登 " + j["posted"],
                                                 j.get("category") and "類別 " + str(j["category"]),
                                                 j.get("priority") and "Priority " + str(j["priority"])] if x),
            }
            for name, v in fresh.items():
                if c.get(name) and not disp(auto.cell(r, c[name])) and not formula(auto.cell(r, c[name])) and v != "":
                    self.book.set(auto, r, c[name], v)
            self.book.set(auto, r, c["Position"], Link(j.get("title") or j["url"], j["url"]))
        self.book.validate(auto, last + 1, c["狀態"], status_rule(main, mh), len(jobs), only_missing=True)
        return {"added": len(jobs), "skipped": len(rows) - len(jobs)}

    def a_set_status(self, b):
        main, mh, auto, ah = self.legacy()
        rows = b.get("rows") or []
        allowed = set(self.status_options(main, mh))
        index = []
        for t, h in ((auto, ah), (main, mh)):
            for r in range(h["row"] + 1, last_data_row(t, h) + 1):
                pos = t.cell(r, h["cols"]["Position"])
                keys = [pair_key(disp(t.cell(r, h["cols"]["公司"])), disp(pos))]
                if link(pos):
                    keys.append(url_key(link(pos)))
                index.append((t, h, r, keys))
        updated, not_found, rejected = [], [], []
        for j in rows:
            status = str(j.get("status") or "")
            if not status or (allowed and status not in allowed):
                rejected.append({"url": j.get("url"), "status": status})
                continue
            want = [k for k in (j.get("url") and url_key(j["url"]), pair_key(j.get("company"), j.get("title"))) if k and k != "ct:|"]
            hit = next((x for x in index if any(k in want for k in x[3])), None)
            if not hit:
                not_found.append(j)
                continue
            t, h, r, _ = hit
            self.book.set(t, r, h["cols"]["狀態"], status)
            pc = h["cols"].get("求職過程")
            if pc:
                prev = disp(t.cell(r, pc))
                line = (j.get("added") or today()) + " " + status + (f"({j['note']})" if j.get("note") else "")
                self.book.set(t, r, pc, prev + "\n" + line if prev else line)
            updated.append({"url": j.get("url"), "sheet": t.title, "row": r})
        sub = self.submission_status(main, mh, rows)
        in_sub = {url_key(x["url"]) for x in sub}
        not_found = [j for j in not_found if not (j.get("url") and url_key(j["url"]) in in_sub)]
        return {"updated": updated, "not_found": not_found, "rejected": rejected, "submissions_updated": sub}

    # -- pipeline tabs (header row 1, columns by title) ------------------------
    def ptab(self, name):
        tab = self.book.tab(name)
        cols = TABS[name]["cols"]
        if tab is None:
            tab = self.book.add_sheet(name, cols=max(26, len(cols)))
            self.book.set_row(tab, 1, 1, [c[1] for c in cols])
            self.format_tab(tab, cols)
            return tab
        head = [disp(tab.cell(1, c)).strip() for c in range(1, len(tab.grid[0]) + 1)] if tab.grid else []
        missing = [c[1] for c in cols if c[1] not in head]
        if missing:
            self.book.set_row(tab, 1, max((i for i, x in enumerate(head, 1) if x), default=0) + 1, missing)
        return tab

    def tab_headers(self, tab):
        cols, last = {}, 0
        row = tab.grid[0] if tab.grid else []
        for i, x in enumerate(row):
            t = disp(x).strip()
            if t:
                cols.setdefault(t, i + 1)
                last = i + 1
        return {"cols": cols, "last": last}

    def format_tab(self, tab, cols, status_rule_=None):
        h = self.tab_headers(tab)
        bk = self.book
        bk.fmt({"repeatCell": {"range": bk.rng(tab, 1, 1, 1, h["last"]), "cell": {"userEnteredFormat": {
            "textFormat": {"bold": True}, "backgroundColor": {"red": 0.91, "green": 0.918, "blue": 0.929}}},
            "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor"}})
        bk.fmt({"updateSheetProperties": {"properties": {"sheetId": tab.id, "gridProperties": {"frozenRowCount": 1}},
                                          "fields": "gridProperties.frozenRowCount"}})
        tab.frozen = 1
        n = max(tab.rows - 1, 1)
        for key, title, width, *opts in cols:
            c = h["cols"].get(title)
            if not c:
                continue
            bk.fmt({"updateDimensionProperties": {"range": {"sheetId": tab.id, "dimension": "COLUMNS", "startIndex": c - 1, "endIndex": c},
                                                  "properties": {"pixelSize": width}, "fields": "pixelSize"}})
            if key == "date":
                bk.fmt({"repeatCell": {"range": bk.rng(tab, 2, c, n), "cell": {"userEnteredFormat": {"numberFormat": {
                    "type": "DATE", "pattern": "yyyy-mm-dd"}}}, "fields": "userEnteredFormat.numberFormat"}})
            if key in ("reasons", "note", "referral_message"):
                bk.fmt({"repeatCell": {"range": bk.rng(tab, 2, c, n), "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                                       "fields": "userEnteredFormat.wrapStrategy"}})
            opt = opts[0] if opts else None
            rule = list_rule(opt) if isinstance(opt, list) else \
                {"condition": {"type": "NUMBER_BETWEEN", "values": [{"userEnteredValue": "1"}, {"userEnteredValue": "10"}]},
                 "strict": True} if opt == "score" else None
            if rule:
                bk.validate(tab, 2, c, rule, n)

    def by_date_tab(self, name, src, width):
        bk = self.book
        tab = bk.tab(name) or bk.add_sheet(name, cols=max(26, width))
        last = col_letter(width)
        q = f"'{src}'!"
        bk.set(tab, 1, 1, "日期")
        if not disp(tab.cell(1, 2)):
            bk.set(tab, 1, 2, Date(today()))
        bk.validate(tab, 1, 2, {"condition": {"type": "DATE_IS_VALID"}, "strict": True})
        bk.set(tab, 1, 3, "← 點兩下選日期")
        bk.set(tab, 3, 1, Formula(f"={{{q}A1:{last}1}}"))
        bk.set(tab, 4, 1, Formula(f'=IFERROR(FILTER({q}A2:{last}, IFERROR(INT({q}A2:A)=$B$1, FALSE)), "這天沒有資料")'))
        bk.fmt({"repeatCell": {"range": bk.rng(tab, 1, 1, 3, width), "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                               "fields": "userEnteredFormat.textFormat.bold"}})
        bk.fmt({"updateSheetProperties": {"properties": {"sheetId": tab.id, "gridProperties": {"frozenRowCount": 3}},
                                          "fields": "gridProperties.frozenRowCount"}})
        tab.frozen = 3

    def read_tab(self, tab, cols):
        h = self.tab_headers(tab)
        out = []
        for r in range(2, len(tab.grid) + 1):
            row = {"row": r}
            any_ = False
            for key, title, *_ in cols:
                c = h["cols"].get(title)
                if not c:
                    continue
                x = tab.cell(r, c)
                v = disp(x)
                if key == "date" and number(x) is not None and not re.match(r"\d{4}-\d\d-\d\d", v):
                    v = (EPOCH + dt.timedelta(days=int(number(x)))).isoformat()
                if key == "score":
                    n = number(x)
                    v = "" if n is None else (int(n) if float(n).is_integer() else n)
                row[key] = v
                if key in LINK_KEYS and link(x):
                    row[key + "_url"] = link(x)
                if v not in ("", None):
                    any_ = True
            if any_ and row.get("url"):
                out.append(row)
        return out

    def last_tab_row(self, tab):
        c = self.tab_headers(tab)["cols"].get("連結")
        for r in range(len(tab.grid), 1, -1):
            if disp(tab.cell(r, c)).strip():
                return r
        return 1

    def value_for(self, key, v, label=None):
        if v is None or v == "":
            return None
        if key == "date":
            return to_date(v) or str(v)
        if key == "score":
            try:
                return min(10, max(1, round(float(v))))
            except (TypeError, ValueError):
                return None
        if key == "mode":
            return norm_mode(v)
        if key == "reasons" and isinstance(v, list):
            return "\n".join("• " + str(s) for s in v)
        if key == "submission_row":
            sub = self.book.tab(SUBMIT_NAME)
            try:
                row = int(v)
            except (TypeError, ValueError):
                return str(v)
            return hyperlink(f"#gid={sub.id}&range=A{row}", f"第 {row} 列") if sub else str(v)
        if key in LINK_KEYS:
            s = str(v)
            if re.match(r"https?://", s, re.I):
                return hyperlink(s, label or {"notion": "Notion", "cv": "CV PDF", "cover_letter": "Cover letter PDF"}.get(key, "連結"))
            return s
        if key == "referral_data":
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        return v

    def append_rows(self, tab, cols, objs):
        h = self.tab_headers(tab)
        start = self.last_tab_row(tab) + 1
        out = []
        for i, o in enumerate(objs):
            r = start + i
            for key, title, *_ in cols:
                c = h["cols"].get(title)
                if not c or key not in o:
                    continue
                v = self.value_for(key, o[key], o.get(key + "_label"))
                if v is not None:
                    self.book.set(tab, r, c, v)
            out.append({"url": o["url"], "row": r})
        return out

    def all_keys(self, legacy, names):
        seen = {}
        add = lambda k, where: seen.setdefault(k, where) if k and k != "ct:|" else None
        if legacy:
            main, mh, auto, ah = legacy
            for t, h in ((main, mh), (auto, ah)):
                for u in existing_urls(t, h):
                    add(url_key(u), t.title)
                for k in existing_pairs(t, h):
                    add(k, t.title)
        for name in names:
            tab = self.book.tab(name)
            if tab is None:
                continue
            rows = self.read_submissions(tab) if name == SUBMIT_NAME else self.read_tab(tab, TABS[name]["cols"])
            for r in rows:
                add(url_key(r["url"]), name)
                if r.get("company") and r.get("title"):
                    add(pair_key(r["company"], r["title"]), name)
        return seen

    def a_setup_pipeline(self, b):
        legacy = self.legacy()
        done = []
        for name, spec in TABS.items():
            tab = self.ptab(name)
            self.format_tab(tab, spec["cols"])
            done.append(name)
            self.by_date_tab(spec["by_date"], name, len(spec["cols"]))
            done.append(spec["by_date"])
        self.submit_tab(legacy)
        done.append(SUBMIT_NAME)
        return {"tabs": done, "version": VERSION}

    def a_pipeline_append(self, b):
        legacy = self.legacy()
        tab = self.ptab(PIPE_NAME)
        seen = self.all_keys(legacy, [PIPE_NAME, INTEREST_NAME, SUBMIT_NAME])
        fresh, skipped = [], []
        for j in b.get("rows") or []:
            u = str(j.get("url") or "").strip()
            if not re.match(r"https?://", u, re.I):
                skipped.append({"url": u, "reason": "no url"})
                continue
            keys = [url_key(u), pair_key(j.get("company"), j.get("title")) if j.get("company") and j.get("title") else ""]
            hit = next((k for k in keys if k and k in seen), None)
            if hit:
                skipped.append({"url": u, "reason": "duplicate", "tab": seen[hit]})
                continue
            for k in keys:
                if k:
                    seen[k] = PIPE_NAME
            fresh.append({**j, "url": u, "date": j.get("date") or today(),
                          "decision": j.get("decision") if j.get("decision") in DECISION_OPTIONS else "未確認",
                          "state": j.get("state") if j.get("state") in PIPE_STATE_OPTIONS else "待決定",
                          "type": j.get("type") if j.get("type") in TYPE_OPTIONS else ""})
        return {"added": self.append_rows(tab, PIPE_COLS, fresh), "skipped": skipped}

    def a_pipeline_list(self, b):
        rows = self.read_tab(self.ptab(PIPE_NAME), PIPE_COLS)
        out = {"queue": [r for r in rows if r.get("decision") == "是" and r.get("state") == "待決定"],
               "undecided": [r for r in rows if not r.get("decision") or r.get("decision") == "未確認"]}
        if b.get("all"):
            out["rows"] = rows
        return out

    def a_interest_list(self, b):
        rows = self.read_tab(self.ptab(INTEREST_NAME), INTEREST_COLS)
        return {"rows": rows if b.get("all") else [r for r in rows if not r.get("state")],
                "pending_cv": [r for r in rows if r.get("state") == "待產 CV"]}

    def a_interest_append(self, b):
        tab = self.ptab(INTEREST_NAME)
        seen = self.all_keys(None, [INTEREST_NAME, SUBMIT_NAME])
        fresh, skipped = [], []
        for j in b.get("rows") or []:
            u = str(j.get("url") or "").strip()
            if not re.match(r"https?://", u, re.I):
                skipped.append({"url": u, "reason": "no url"})
                continue
            k = url_key(u)
            if k in seen:
                skipped.append({"url": u, "reason": "duplicate", "tab": seen[k]})
                continue
            seen[k] = INTEREST_NAME
            fresh.append({"url": u, "note": j.get("note") or "", "date": j.get("date") or today()})
        return {"added": self.append_rows(tab, INTEREST_COLS, fresh), "skipped": skipped}

    def update_by_url(self, name, rows):
        cols = TABS[name]["cols"]
        tab = self.ptab(name)
        h = self.tab_headers(tab)
        by_key = {url_key(r["url"]): r["row"] for r in self.read_tab(tab, cols)}
        updated, not_found, rejected = [], [], []
        for j in rows:
            row = j.get("url") and by_key.get(url_key(j["url"]))
            if not row:
                not_found.append(j.get("url") or "")
                continue
            bad, writes = [], []
            for key, title, *opts in cols:
                if key == "url" or key not in j or title not in h["cols"]:
                    continue
                opt = opts[1] if len(opts) > 1 else None
                if isinstance(opt, list) and j[key] != "" and key != "mode" and j[key] not in opt:
                    bad.append(f"{key}={j[key]}")
                    continue
                writes.append((h["cols"][title], self.value_for(key, j[key], j.get(key + "_label"))))
            if bad:
                rejected.append({"url": j["url"], "bad": bad})
                continue
            for c, v in writes:
                self.book.set(tab, row, c, "" if v is None else v)
            updated.append({"url": j["url"], "row": row})
        return {"updated": updated, "not_found": not_found, "rejected": rejected}

    def a_pipeline_update(self, b):
        return self.update_by_url(PIPE_NAME, b.get("rows") or [])

    def a_interest_update(self, b):
        return self.update_by_url(INTEREST_NAME, b.get("rows") or [])

    def a_pipeline_backfill(self, b):
        status = b.get("status") or "準備中"
        main, mh, auto, ah = self.legacy()
        tab = self.ptab(PIPE_NAME)
        seen = {url_key(r["url"]) for r in self.read_tab(tab, PIPE_COLS)}
        col = lambda name: ah["cols"].get(name)
        fresh, skipped = [], []
        for r in range(ah["row"] + 1, last_data_row(auto, ah) + 1):
            if disp(auto.cell(r, col("狀態"))).strip() != status:
                continue
            pos = auto.cell(r, col("Position"))
            url, title = link(pos), disp(pos)
            if not url:
                skipped.append({"title": title, "reason": "no url"})
                continue
            if url_key(url) in seen:
                skipped.append({"url": url, "reason": "duplicate"})
                continue
            seen.add(url_key(url))
            place = [s.strip() for s in disp(auto.cell(r, col("地點"))).split(",") if s.strip()] if col("地點") else []
            cond = disp(auto.cell(r, col("應徵條件"))) if col("應徵條件") else ""
            mode = re.search(r"工作模式[::]\s*([^\n]+)", cond)
            date = re.search(r"\d{4}-\d{2}-\d{2}", disp(auto.cell(r, col("求職過程")))) if col("求職過程") else None
            fresh.append({"date": date[0] if date else "", "company": disp(auto.cell(r, col("公司"))), "title": title, "url": url,
                          "city": ", ".join(place[:-1]) if len(place) > 1 else "", "country": place[-1] if place else "",
                          "mode": mode[1] if mode else "", "decision": "未確認", "state": "待決定"})
        return {"added": self.append_rows(tab, PIPE_COLS, fresh), "skipped": skipped}

    # -- 投遞表單 (求職追蹤表 layout + pipeline columns) -------------------------
    def submit_tab(self, legacy=None):
        main, mh, auto, ah = legacy or self.legacy()
        tab = self.book.tab(SUBMIT_NAME)
        if tab is None:
            tab = self.book.add_sheet(SUBMIT_NAME, cols=mh["last"] + len(SUBMIT_EXTRA))
            self.copy_header(main, mh, tab)
        h = headers(tab)
        missing = [x for x in SUBMIT_EXTRA if x[1] not in h["cols"]]
        if missing:
            start = h["last"] + 1
            self.book.set_row(tab, h["row"], start, [x[1] for x in missing])
            bk = self.book
            bk.fmt({"repeatCell": {"range": bk.rng(tab, h["row"], start, 1, len(missing)), "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True}, "backgroundColor": {"red": 0.91, "green": 0.918, "blue": 0.929}}},
                "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor"}})
            n = max(tab.rows - h["row"], 1)
            for i, (key, title, width, *opts) in enumerate(missing):
                c = start + i
                bk.fmt({"updateDimensionProperties": {"range": {"sheetId": tab.id, "dimension": "COLUMNS", "startIndex": c - 1,
                                                                "endIndex": c}, "properties": {"pixelSize": width}, "fields": "pixelSize"}})
                if opts and isinstance(opts[0], list):
                    bk.validate(tab, h["row"] + 1, c, list_rule(opts[0]), n)
                if key == "date":
                    bk.fmt({"repeatCell": {"range": bk.rng(tab, h["row"] + 1, c, n), "cell": {"userEnteredFormat": {"numberFormat": {
                        "type": "DATE", "pattern": "yyyy-mm-dd"}}}, "fields": "userEnteredFormat.numberFormat"}})
        return tab

    def read_submissions(self, tab):
        h = headers(tab)
        extra = {x[1]: x[0] for x in SUBMIT_EXTRA}
        out = []
        for r in range(h["row"] + 1, last_data_row(tab, h) + 1):
            row = {"row": r}
            for title, c in h["cols"].items():
                key = "title" if title == "Position" else SUBMIT_MAIN_KEYS.get(title) or extra.get(title)
                if not key:
                    continue
                x = tab.cell(r, c)
                v = disp(x)
                if key == "date" and number(x) is not None and not re.match(r"\d{4}-\d\d-\d\d", v):
                    v = (EPOCH + dt.timedelta(days=int(number(x)))).isoformat()
                row[key] = v
                if key in LINK_KEYS and formula(x) and link(x):
                    row[key + "_url"] = link(x)
            row["url"] = link(tab.cell(r, h["cols"]["Position"])) or row.get("url", "")
            loc = str(row.get("location") or "").split("/")
            place = [s.strip() for s in loc[0].split(",") if s.strip()]
            row.setdefault("city", "")
            row.setdefault("country", "")
            row.setdefault("mode", "")
            row["city"] = row["city"] or (", ".join(place[:-1]) if len(place) > 1 else "")
            row["country"] = row["country"] or (place[-1] if place else "")
            row["mode"] = row["mode"] or (loc[1].strip() if len(loc) > 1 else "")
            try:
                row["referrals"] = json.loads(row.pop("referral_data", "") or "[]")
            except ValueError:
                row["referrals"] = []
            out.append(row)
        return out

    def resume_cell(self, j, ats=None):
        a = j.get("ats", ats)
        label = j.get("resume_label") or " · ".join(x for x in ["v3 cards", f"ATS {a}" if a not in (None, "") else ""] if x) + \
            (" + CL" if j.get("cover_letter") or j.get("has_cover_letter") else "")
        n = j.get("notion")
        return hyperlink(n, label) if n and re.match(r"https?://", str(n)) else label

    def write_extras(self, tab, h, r, j):
        for key, title, *_ in SUBMIT_EXTRA:
            if key not in j or j[key] in ("", None):
                continue
            v = j[key]
            if key == "date":
                v = to_date(v) or str(v)
            elif key == "mode":
                v = norm_mode(v)
            elif key == "referral_data":
                v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            elif key in ("cv", "cover_letter", "notion") and re.match(r"https?://", str(v)):
                v = hyperlink(v, {"cv": "CV PDF", "cover_letter": "Cover letter PDF", "notion": "Notion"}[key])
            self.book.set(tab, r, h["cols"].get(title), v)

    @staticmethod
    def referrers_text(people):
        return "\n".join(p["name"] + (f" ({p['title']})" if p.get("title") else "") + " — " + p["url"] for p in people or [])

    def a_submissions_list(self, b):
        return {"rows": self.read_submissions(self.submit_tab())}

    def a_submissions_append(self, b):
        legacy = self.legacy()
        main, mh = legacy[0], legacy[1]
        tab = self.submit_tab(legacy)
        h = headers(tab)
        seen = {url_key(r["url"]): r["row"] for r in self.read_submissions(tab) if r.get("url")}
        allowed = self.status_options(main, mh)
        added, skipped = [], []
        last = last_data_row(tab, h)
        seq = max_seq(tab, h, last)
        r = last
        c = h["cols"]
        for j in b.get("rows") or []:
            u = str(j.get("url") or "").strip()
            if not re.match(r"https?://", u, re.I):
                skipped.append({"url": u, "reason": "no url"})
                continue
            if url_key(u) in seen:
                skipped.append({"url": u, "reason": "duplicate", "row": seen[url_key(u)]})
                continue
            r += 1
            seq += 1
            seen[url_key(u)] = r
            status = j["status"] if j.get("status") and (not allowed or j["status"] in allowed) else self.default_status()
            place = " / ".join(x for x in (", ".join(x for x in (j.get("city"), j.get("country")) if x),
                                           norm_mode(j["mode"]) if j.get("mode") else "") if x)
            reqs = j.get("requirements") or []
            reqs = reqs if isinstance(reqs, list) else [reqs]
            head = " · ".join(x for x in (j.get("type"), f"評分 {j['score']}/10" if j.get("score") else "") if x)
            cond = "\n".join(x for x in [head] + ["• " + str(q) for q in reqs] if x)
            ats = j.get("ats")
            proc = f"- {mmdd(j.get('date'))} CV" + (" + cover letter" if j.get("cover_letter") or j.get("has_cover_letter") else "") + \
                " 產生" + (f" (ATS {ats})" if ats not in (None, "") else "")
            bk = self.book
            bk.set(tab, r, c.get("順序"), seq)
            bk.set(tab, r, c.get("狀態"), status)
            bk.set(tab, r, c.get("公司"), j.get("company") or "")
            bk.set(tab, r, c["Position"], Link(j.get("title") or u, u))
            if j.get("industry"):
                bk.set(tab, r, c.get("Industry"), j["industry"])
            if cond:
                bk.set(tab, r, c.get("應徵條件"), cond)
            if place:
                bk.set(tab, r, c.get("地點"), place)
            bk.set(tab, r, c.get("求職過程"), proc)
            if j.get("referrers"):
                bk.set(tab, r, c.get("內推人"), self.referrers_text(j["referrers"]))
            bk.set(tab, r, c.get("履歷版本"), self.resume_cell(j))
            self.write_extras(tab, h, r, {**j, "url": u, "date": j.get("date") or today()})
            added.append({"url": u, "row": r})
        if added:
            first, n = added[0]["row"], added[-1]["row"] - added[0]["row"] + 1
            self.book.validate(tab, first, c.get("狀態"), status_rule(main, mh), n, only_missing=True)
            self.book.validate(tab, first, c.get("內推狀態"), column_rule(main, mh, "內推狀態"), n, only_missing=True)
        return {"added": added, "skipped": skipped}

    def find_submission(self, tab, url):
        k = url_key(url or "")
        return [r for r in self.read_submissions(tab) if r.get("url") and url_key(r["url"]) == k]

    def append_process(self, tab, h, r, line):
        c = h["cols"].get("求職過程")
        if c:
            prev = disp(tab.cell(r, c))
            self.book.set(tab, r, c, prev + "\n" + line if prev else line)

    def a_submissions_update(self, b):
        tab = self.submit_tab()
        h = headers(tab)
        updated, not_found = [], []
        for j in b.get("rows") or []:
            hits = self.find_submission(tab, j.get("url"))
            if not hits:
                not_found.append(j.get("url") or "")
                continue
            for r in hits:
                if j.get("referrers"):
                    self.book.set(tab, r["row"], h["cols"].get("內推人"), self.referrers_text(j["referrers"]))
                if j.get("notion") or j.get("resume_label"):
                    self.book.set(tab, r["row"], h["cols"].get("履歷版本"), self.resume_cell(j, r.get("ats")))
                if j.get("process_line"):
                    self.append_process(tab, h, r["row"], f"- {mmdd(j.get('date'))} {j['process_line']}")
                self.write_extras(tab, h, r["row"], j)
                updated.append({"url": j["url"], "row": r["row"]})
        return {"updated": updated, "not_found": not_found}

    def a_referral_sent(self, b):
        tab = self.submit_tab()
        h = headers(tab)
        updated, not_found = [], []
        for j in b.get("rows") or []:
            hits = self.find_submission(tab, j.get("url"))
            if not hits or not j.get("name"):
                not_found.append(j.get("url") or "")
                continue
            for r in hits:
                self.book.set(tab, r["row"], h["cols"].get("內推狀態"), REFERRAL_SENT)
                self.append_process(tab, h, r["row"], f"- {mmdd(j.get('date'))} Sent referral request to {j['name']}")
                updated.append({"url": j["url"], "row": r["row"]})
        return {"updated": updated, "not_found": not_found}

    def submission_status(self, main, mh, rows):
        tab = self.book.tab(SUBMIT_NAME)
        if tab is None:
            return []
        h = headers(tab)
        allowed = self.status_options(main, mh)
        out = []
        for j in rows:
            status = str(j.get("status") or "")
            if not j.get("url") or not status or (allowed and status not in allowed):
                continue
            for r in self.find_submission(tab, j["url"]):
                if r.get("status") == status:
                    out.append({"url": j["url"], "row": r["row"], "changed": False})
                    continue
                self.book.set(tab, r["row"], h["cols"]["狀態"], status)
                label = "Sent" if status == "履歷送出" else status
                self.append_process(tab, h, r["row"], f"- {mmdd(j.get('added'))} {label} ({j.get('note') or 'CV dashboard'})")
                out.append({"url": j["url"], "row": r["row"], "changed": True})
        return out


def col_letter(n):
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s
