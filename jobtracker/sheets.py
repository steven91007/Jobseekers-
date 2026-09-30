"""The job-application tracker sheet: read rows, update cells, append applications.

The sheet keeps whatever headers the user chose. ``ColumnMap`` finds which header
means company / role / status / ... (see config.COLUMN_ALIASES) so code can work
with canonical fields, while writes still go to the user's own columns. Rows are
addressed by their sheet row number, which is stable as long as nobody sorts or
inserts rows between a read and a write; ``update`` checks the company cell first
to catch that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_COLUMNS = 52  # A..AZ


class SheetError(RuntimeError):
    pass


def _norm(header: str) -> str:
    return re.sub(r"[\s_\-:：]+", "", header).casefold()


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        s = chr(65 + rem) + s
    return s


def quote_tab(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


@dataclass
class ColumnMap:
    headers: list[str]
    fields: dict[str, int]  # canonical field -> column index

    @classmethod
    def detect(cls, headers: list[str], aliases: dict[str, list[str]]) -> "ColumnMap":
        index = {}
        for i, h in enumerate(headers):
            index.setdefault(_norm(h), i)
        fields = {}
        for key, names in aliases.items():
            for name in names:
                i = index.get(_norm(name))
                if i is not None and i not in fields.values():
                    fields[key] = i
                    break
        return cls(headers=headers, fields=fields)

    def column(self, name: str) -> int:
        """Column index for a canonical field or an exact (normalized) header."""
        if name in self.fields:
            return self.fields[name]
        target = _norm(name)
        for i, h in enumerate(self.headers):
            if _norm(h) == target:
                return i
        raise SheetError(f"no column {name!r}; headers are {self.headers}, "
                         f"recognized fields {sorted(self.fields)}")


@dataclass
class Application:
    row: int  # 1-based sheet row
    values: dict[str, str]  # header -> cell text
    fields: dict[str, str] = field(default_factory=dict)  # canonical field -> cell text

    def to_dict(self) -> dict:
        return {"row": self.row, **self.fields, "cells": self.values}


class ApplicationSheet:
    def __init__(self, service, spreadsheet_id: str, tab: str = "", header_row: int = 1,
                 aliases: dict[str, list[str]] | None = None):
        if not spreadsheet_id:
            raise SheetError("no spreadsheet configured; set JOBTRACKER_SHEET_ID in .env "
                             "(the id or the full docs.google.com URL)")
        self._svc = service.spreadsheets()
        self.spreadsheet_id = spreadsheet_id
        self._tab = tab
        self.header_row = header_row
        self._aliases = aliases or {}
        self._columns: ColumnMap | None = None

    @classmethod
    def from_settings(cls, settings) -> "ApplicationSheet":
        from .google_auth import build_service

        return cls(build_service(settings, "sheets", "v4"), settings.sheet_id, settings.sheet_tab,
                   settings.header_row, settings.aliases())

    # --- metadata ------------------------------------------------------------------

    def info(self) -> dict:
        meta = self._svc.get(spreadsheetId=self.spreadsheet_id,
                             fields="properties.title,sheets.properties(title,gridProperties)").execute()
        return {
            "title": meta["properties"]["title"],
            "tabs": [s["properties"]["title"] for s in meta.get("sheets", [])],
        }

    @property
    def tab(self) -> str:
        if not self._tab:
            self._tab = self.info()["tabs"][0]
        return self._tab

    def _range(self, a1: str) -> str:
        return f"{quote_tab(self.tab)}!{a1}"

    def columns(self, refresh: bool = False) -> ColumnMap:
        if self._columns is None or refresh:
            last = column_letter(MAX_COLUMNS - 1)
            r = self.header_row
            resp = self._svc.values().get(spreadsheetId=self.spreadsheet_id,
                                          range=self._range(f"A{r}:{last}{r}")).execute()
            headers = [str(h).strip() for h in (resp.get("values") or [[]])[0]]
            while headers and not headers[-1]:
                headers.pop()
            if not headers:
                raise SheetError(f"row {r} of tab {self.tab!r} is empty; put the column headers there "
                                 "or set JOBTRACKER_HEADER_ROW")
            self._columns = ColumnMap.detect(headers, self._aliases)
        return self._columns

    # --- read ----------------------------------------------------------------------

    def applications(self) -> list[Application]:
        cols = self.columns(refresh=True)
        last = column_letter(len(cols.headers) - 1)
        first = self.header_row + 1
        resp = self._svc.values().get(spreadsheetId=self.spreadsheet_id,
                                      range=self._range(f"A{first}:{last}"),
                                      valueRenderOption="FORMATTED_VALUE").execute()
        out = []
        for offset, raw in enumerate(resp.get("values", [])):
            cells = [str(c).strip() for c in raw] + [""] * (len(cols.headers) - len(raw))
            if not any(cells):
                continue
            values = {h or column_letter(i): cells[i] for i, h in enumerate(cols.headers)}
            fields = {k: cells[i] for k, i in cols.fields.items()}
            out.append(Application(row=first + offset, values=values, fields=fields))
        return out

    def find(self, text: str) -> list[Application]:
        """Applications whose company or role contains ``text`` (case-insensitive)."""
        needle = text.casefold().strip()
        return [a for a in self.applications()
                if needle in a.fields.get("company", "").casefold()
                or needle in a.fields.get("role", "").casefold()]

    # --- write ---------------------------------------------------------------------

    def update(self, row: int, changes: dict[str, str], *, expect_company: str | None = None) -> dict:
        """Set cells of one row. Keys are canonical fields (status, notes, ...) or headers.

        ``expect_company`` guards against the sheet having been re-sorted since it was read:
        the write is refused when that row's company cell no longer contains it.
        """
        if row <= self.header_row:
            raise SheetError(f"row {row} is the header or above it; data starts at row {self.header_row + 1}")
        if not changes:
            raise SheetError("no changes given")
        cols = self.columns()
        if expect_company is not None and "company" in cols.fields:
            cell = self._cell(row, cols.fields["company"])
            if expect_company.casefold().strip() not in cell.casefold():
                raise SheetError(f"row {row} is now {cell!r}, not {expect_company!r}; "
                                 "the sheet changed, read it again")
        data = []
        written = {}
        for key, value in changes.items():
            i = cols.column(key)
            data.append({"range": self._range(f"{column_letter(i)}{row}"), "values": [[value]]})
            written[cols.headers[i]] = value
        self._svc.values().batchUpdate(spreadsheetId=self.spreadsheet_id, body={
            "valueInputOption": "USER_ENTERED", "data": data}).execute()
        return {"row": row, "written": written}

    def append(self, values: dict[str, str]) -> dict:
        """Add an application as a new row. Keys are canonical fields or headers."""
        if not values:
            raise SheetError("no values given")
        cols = self.columns()
        row = [""] * len(cols.headers)
        for key, value in values.items():
            row[cols.column(key)] = value
        last = column_letter(len(cols.headers) - 1)
        resp = self._svc.values().append(
            spreadsheetId=self.spreadsheet_id, range=self._range(f"A{self.header_row}:{last}"),
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
            body={"values": [row]}).execute()
        updated = (resp.get("updates") or {}).get("updatedRange", "")
        m = re.search(r"![A-Z]+(\d+)", updated)
        return {"row": int(m.group(1)) if m else None, "range": updated,
                "written": {cols.headers[i]: v for i, v in enumerate(row) if v}}

    def _cell(self, row: int, col: int) -> str:
        a1 = f"{column_letter(col)}{row}"
        resp = self._svc.values().get(spreadsheetId=self.spreadsheet_id, range=self._range(a1)).execute()
        values = resp.get("values") or [[""]]
        return str(values[0][0]) if values and values[0] else ""
