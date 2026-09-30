"""Environment-backed settings for the Gmail + Google Sheets application tracker."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Read-only Gmail and read/write Sheets. Changing this list invalidates the saved token
# (google_auth notices and asks for `python -m jobtracker auth` again).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Mail from employers and ATSs about applications. Gmail search syntax; override with
# JOBTRACKER_GMAIL_QUERY.
DEFAULT_GMAIL_QUERY = (
    "newer_than:30d -category:promotions -category:social "
    "(application OR applying OR applied OR interview OR candidate OR position OR role "
    "OR offer OR assessment OR unfortunately OR Bewerbung OR sollicitatie)"
)

# Canonical tracker fields -> header names that mean them (compared case-insensitively,
# ignoring spaces, "_" and "-"). The sheet keeps its own headers; these only let code find
# the company / role / status columns. Extend with JOBTRACKER_COLUMNS.
COLUMN_ALIASES: dict[str, list[str]] = {
    "company": ["company", "company name", "employer", "公司", "公司名稱", "企業"],
    "role": ["role", "position", "title", "job title", "job", "職位", "職稱", "職缺", "應徵職位"],
    "status": ["status", "application status", "stage", "狀態", "進度", "目前狀態"],
    "applied_at": ["applied", "applied at", "applied on", "date applied", "application date",
                   "date", "投遞日期", "申請日期", "投遞時間", "日期"],
    "location": ["location", "city", "country", "地點", "城市", "國家"],
    "link": ["link", "url", "job link", "job url", "posting", "連結", "職缺連結", "網址"],
    "source": ["source", "platform", "channel", "來源", "管道", "平台"],
    "contact": ["contact", "recruiter", "email", "聯絡人", "招募者", "信箱"],
    "notes": ["notes", "note", "comments", "remark", "備註", "筆記"],
    "last_update": ["last update", "updated", "last contact", "最後更新", "更新日期", "最後聯絡"],
}


class ConfigError(RuntimeError):
    pass


def spreadsheet_id(raw: str) -> str:
    """Accept a bare spreadsheet id or any docs.google.com/spreadsheets URL."""
    raw = raw.strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", raw)
    return m.group(1) if m else raw


def parse_columns(raw: str) -> dict[str, list[str]]:
    """``company=Firma|Arbeitgeber,status=Stand`` -> extra aliases per canonical field."""
    out: dict[str, list[str]] = {}
    for part in filter(None, (p.strip() for p in raw.split(","))):
        key, sep, names = part.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"JOBTRACKER_COLUMNS: expected field=Header, got {part!r}")
        out.setdefault(key.strip(), []).extend(n.strip() for n in names.split("|") if n.strip())
    return out


def _path(raw: str, default: str) -> Path:
    p = Path(raw.strip() or default).expanduser()
    return p if p.is_absolute() else ROOT / p


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() else default


@dataclass(frozen=True)
class Settings:
    client_secret_file: Path
    token_file: Path
    sheet_id: str
    sheet_tab: str
    header_row: int
    gmail_query: str
    columns: dict[str, list[str]] = field(default_factory=dict)

    def aliases(self) -> dict[str, list[str]]:
        merged = {k: list(v) for k, v in COLUMN_ALIASES.items()}
        for k, extra in self.columns.items():
            merged[k] = extra + merged.get(k, [])
        return merged


def load(env_file: str | Path | None = None) -> Settings:
    load_dotenv(env_file or os.getenv("JOBSEEKERS_ENV_FILE", "").strip() or ROOT / ".env")
    return Settings(
        client_secret_file=_path(os.getenv("GOOGLE_OAUTH_CLIENT_FILE", ""), "data/google_oauth_client.json"),
        token_file=_path(os.getenv("GOOGLE_TOKEN_FILE", ""), "data/google_token.json"),
        sheet_id=spreadsheet_id(os.getenv("JOBTRACKER_SHEET_ID", "")),
        sheet_tab=os.getenv("JOBTRACKER_SHEET_TAB", "").strip(),
        header_row=max(1, _int("JOBTRACKER_HEADER_ROW", 1)),
        gmail_query=os.getenv("JOBTRACKER_GMAIL_QUERY", "").strip() or DEFAULT_GMAIL_QUERY,
        columns=parse_columns(os.getenv("JOBTRACKER_COLUMNS", "")),
    )
