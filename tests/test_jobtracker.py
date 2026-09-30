"""jobtracker: Gmail parsing and the application sheet, against fake Google services."""

import base64
import json
import re

import pytest

from jobtracker import config
from jobtracker.gmail import GmailClient, body_text, parse_message
from jobtracker.sheets import ApplicationSheet, ColumnMap, SheetError, column_letter


def b64(s: str, enc="utf-8") -> str:
    return base64.urlsafe_b64encode(s.encode(enc)).decode().rstrip("=")


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


# --- fake Gmail ----------------------------------------------------------------------


def message(mid, subject, sender, payload_parts=None, date_ms=1759200000000):
    payload = {"mimeType": "multipart/alternative",
               "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject},
                           {"name": "To", "value": "me@example.com"}],
               "parts": payload_parts or []}
    return {"id": mid, "threadId": "t" + mid, "internalDate": str(date_ms), "snippet": subject[:20],
            "labelIds": ["INBOX", "CATEGORY_UPDATES"], "payload": payload}


class FakeGmail:
    def __init__(self, messages):
        self.messages_by_id = {m["id"]: m for m in messages}
        self.order = [m["id"] for m in messages]
        self.list_calls = []

    def users(self):
        return self

    def messages(self):
        return self

    def getProfile(self, userId):
        return Call(lambda: {"emailAddress": "me@example.com", "messagesTotal": len(self.order)})

    def list(self, userId, q, maxResults, pageToken=None):
        self.list_calls.append((q, maxResults, pageToken))
        start = int(pageToken or 0)
        ids = self.order[start:start + maxResults]
        nxt = start + maxResults
        resp = {"messages": [{"id": i} for i in ids]}
        if nxt < len(self.order):
            resp["nextPageToken"] = str(nxt)
        return Call(lambda: resp)

    def get(self, userId, id, format, metadataHeaders=None):
        return Call(lambda: self.messages_by_id[id])


def test_body_prefers_plain_text_and_decodes_charset():
    parts = [
        {"mimeType": "text/html", "body": {"data": b64("<p>HTML version</p>")}},
        {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}],
         "body": {"data": b64("Vielen Dank für Ihre Bewerbung", "iso-8859-1")}},
    ]
    assert body_text({"parts": parts}) == "Vielen Dank für Ihre Bewerbung"


def test_body_falls_back_to_html_without_scripts_and_skips_attachments():
    html = "<html><head><style>p{}</style></head><body><p>Hi   Chiyi,</p><script>x()</script>" \
           "<p>We'd like to invite you</p></body></html>"
    payload = {"parts": [
        {"mimeType": "multipart/related", "parts": [{"mimeType": "text/html", "body": {"data": b64(html)}}]},
        {"mimeType": "text/plain", "filename": "cv.txt", "body": {"data": b64("attachment")}},
    ]}
    assert body_text(payload) == "Hi Chiyi,\nWe'd like to invite you"


def test_parse_message_headers_and_truncation():
    m = message("1", "Your application to Acme", '"Acme Talent" <Jobs@Acme.io>',
                [{"mimeType": "text/plain", "body": {"data": b64("x" * 50)}}])
    e = parse_message(m, with_body=True, max_chars=10)
    assert (e.sender, e.sender_email, e.sender_domain) == ("Acme Talent", "jobs@acme.io", "acme.io")
    assert e.received_at == "2025-09-30T02:40:00+00:00"
    assert e.body == "x" * 10 + "\n[... truncated]"
    assert parse_message(m).body == ""
    assert e.to_dict()["sender_domain"] == "acme.io"


def test_search_pages_and_caps_results():
    fake = FakeGmail([message(str(i), f"s{i}", "a@b.c") for i in range(150)])
    mails = GmailClient(fake).search("interview", max_results=120)
    assert [m.id for m in mails] == [str(i) for i in range(120)]
    assert fake.list_calls == [("interview", 100, None), ("interview", 20, "100")]
    assert GmailClient(fake).profile()["emailAddress"] == "me@example.com"


# --- fake Sheets -----------------------------------------------------------------------


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


class FakeSheets:
    """A grid behind the subset of the Sheets v4 API that ApplicationSheet uses."""

    def __init__(self, tabs):
        self.tabs = tabs  # title -> list of rows
        self.batch_bodies = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def _parse(self, rng):
        m = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)(\d+)(?::([A-Z]+)(\d*))?", rng)
        assert m, rng
        tab = m.group(1).replace("''", "'")
        c1, r1 = col_index(m.group(2)), int(m.group(3))
        c2 = col_index(m.group(4)) if m.group(4) else c1
        r2 = int(m.group(5)) if m.group(5) else (r1 if not m.group(4) else None)
        return self.tabs[tab], c1, r1, c2, r2

    def get(self, spreadsheetId, range=None, fields=None, valueRenderOption=None):
        if range is None:
            return Call(lambda: {"properties": {"title": "Job hunt"},
                                 "sheets": [{"properties": {"title": t}} for t in self.tabs]})
        grid, c1, r1, c2, r2 = self._parse(range)
        rows = grid[r1 - 1:(r2 if r2 else len(grid))]
        values = [row[c1:c2 + 1] for row in rows]
        while values and not any(values[-1]):
            values.pop()
        return Call(lambda: {"values": values} if values else {})

    def _set(self, grid, r, c, v):
        while len(grid) < r:
            grid.append([])
        row = grid[r - 1]
        row.extend([""] * (c + 1 - len(row)))
        row[c] = v

    def batchUpdate(self, spreadsheetId, body):
        self.batch_bodies.append(body)
        for d in body["data"]:
            grid, c, r, _, _ = self._parse(d["range"])
            self._set(grid, r, c, d["values"][0][0])
        return Call(lambda: {})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        grid, *_ = self._parse(range)
        tab = next(t for t, g in self.tabs.items() if g is grid)
        r = len(grid) + 1
        for c, v in enumerate(body["values"][0]):
            self._set(grid, r, c, v)
        return Call(lambda: {"updates": {"updatedRange": f"'{tab}'!A{r}:{column_letter(len(body['values'][0]) - 1)}{r}"}})


def tracker():
    grid = [
        ["公司", "職位", "投遞日期", "狀態", "備註"],
        ["Acme AI", "AI Engineer", "2026-09-20", "Applied", ""],
        [],
        ["Zalando", "ML Engineer", "2026-09-22", "Interview", "HR call"],
    ]
    fake = FakeSheets({"Other": [["x"]], "求職紀錄 'main'": grid})
    sheet = ApplicationSheet(fake, "sid", tab="求職紀錄 'main'", aliases=config.COLUMN_ALIASES)
    return fake, grid, sheet


def test_column_map_detects_chinese_and_english_headers():
    cols = ColumnMap.detect(["Company Name", "Job_Title", "Stage", "Date applied"], config.COLUMN_ALIASES)
    assert cols.fields == {"company": 0, "role": 1, "status": 2, "applied_at": 3}
    assert cols.column("date applied") == 3
    with pytest.raises(SheetError):
        cols.column("salary")


def test_applications_skip_blank_rows_and_keep_row_numbers():
    _, _, sheet = tracker()
    apps = sheet.applications()
    assert [(a.row, a.fields["company"], a.fields["status"]) for a in apps] == [
        (2, "Acme AI", "Applied"), (4, "Zalando", "Interview")]
    assert apps[0].values["備註"] == ""
    assert [a.row for a in sheet.find("ml eng")] == [4]


def test_update_writes_cells_by_field_or_header():
    fake, grid, sheet = tracker()
    res = sheet.update(4, {"status": "Offer", "備註": "verbal"}, expect_company="zalando")
    assert res == {"row": 4, "written": {"狀態": "Offer", "備註": "verbal"}}
    assert grid[3] == ["Zalando", "ML Engineer", "2026-09-22", "Offer", "verbal"]
    assert fake.batch_bodies[0]["valueInputOption"] == "USER_ENTERED"


def test_update_refuses_when_row_moved_or_header():
    _, grid, sheet = tracker()
    with pytest.raises(SheetError, match="the sheet changed"):
        sheet.update(2, {"status": "Rejected"}, expect_company="Zalando")
    with pytest.raises(SheetError):
        sheet.update(1, {"status": "x"})
    assert grid[1][3] == "Applied"


def test_append_maps_fields_to_columns():
    _, grid, sheet = tracker()
    res = sheet.append({"company": "Mistral", "role": "Applied AI Engineer", "status": "Applied"})
    assert res["row"] == 5
    assert grid[4] == ["Mistral", "Applied AI Engineer", "", "Applied", ""]


def test_default_tab_is_first_and_missing_sheet_id_errors():
    fake, _, _ = tracker()
    assert ApplicationSheet(fake, "sid").tab == "Other"
    with pytest.raises(SheetError, match="JOBTRACKER_SHEET_ID"):
        ApplicationSheet(fake, "")


# --- config / auth ----------------------------------------------------------------------


def test_config_parses_sheet_url_and_columns(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("JOBTRACKER_SHEET_ID=https://docs.google.com/spreadsheets/d/1AbC-d_E/edit#gid=0\n"
                   "JOBTRACKER_COLUMNS=company=Firma|Arbeitgeber,status=Stand\n"
                   "GOOGLE_TOKEN_FILE=/abs/token.json\n")
    for k in ("JOBTRACKER_SHEET_ID", "JOBTRACKER_COLUMNS", "GOOGLE_TOKEN_FILE"):
        monkeypatch.delenv(k, raising=False)
    s = config.load(env)
    assert s.sheet_id == "1AbC-d_E"
    assert s.aliases()["company"][:2] == ["Firma", "Arbeitgeber"]
    assert str(s.token_file) == "/abs/token.json"
    assert s.client_secret_file == config.ROOT / "data/google_oauth_client.json"
    with pytest.raises(config.ConfigError):
        config.parse_columns("company")


def test_credentials_errors_are_actionable(tmp_path):
    from jobtracker.google_auth import AuthError, credentials

    s = config.Settings(client_secret_file=tmp_path / "c.json", token_file=tmp_path / "t.json",
                        sheet_id="x", sheet_tab="", header_row=1, gmail_query="q")
    with pytest.raises(AuthError, match="python -m jobtracker auth"):
        credentials(s)
    s.token_file.write_text(json.dumps({"scopes": [config.SCOPES[0]], "refresh_token": "r"}))
    with pytest.raises(AuthError, match="lacks"):
        credentials(s)
