"""CLI: python -m jobtracker <command>."""

import argparse
import sys

from rich.console import Console
from rich.table import Table

from . import config
from .google_auth import AuthError, authorize, credentials
from .gmail import GmailClient, GmailError
from .sheets import ApplicationSheet, SheetError

console = Console()


def cmd_auth(args, settings) -> int:
    creds = authorize(settings, open_browser=not args.no_browser)
    console.print(f"Signed in. Token saved to {settings.token_file} (scopes: {', '.join(creds.scopes or [])})")
    return 0


def cmd_doctor(args, settings) -> int:
    ok = True
    console.print(f"OAuth client: {settings.client_secret_file} "
                  + ("[green]found[/green]" if settings.client_secret_file.exists() else "[red]missing[/red]"))
    try:
        credentials(settings)
        console.print(f"Google token: {settings.token_file} [green]OK[/green]")
    except AuthError as e:
        console.print(f"[red]Google token: {e}[/red]")
        return 1
    try:
        profile = GmailClient.from_settings(settings).profile()
        console.print(f"Gmail: [green]OK[/green] {profile.get('emailAddress')} "
                      f"({profile.get('messagesTotal')} messages)")
    except Exception as e:
        ok = False
        console.print(f"[red]Gmail: {type(e).__name__}: {e}[/red]")
    try:
        sheet = ApplicationSheet.from_settings(settings)
        info = sheet.info()
        cols = sheet.columns()
        console.print(f"Sheet: [green]OK[/green] {info['title']!r}, tab {sheet.tab!r} of {info['tabs']}")
        console.print(f"  headers: {cols.headers}")
        console.print(f"  recognized: { {k: cols.headers[i] for k, i in cols.fields.items()} }")
        for need in ("company", "role", "status"):
            if need not in cols.fields:
                ok = False
                console.print(f"[yellow]  no {need!r} column recognized; name it in JOBTRACKER_COLUMNS, "
                              f"e.g. {need}=<your header>[/yellow]")
    except Exception as e:
        ok = False
        console.print(f"[red]Sheet: {type(e).__name__}: {e}[/red]")
    return 0 if ok else 1


def cmd_sheet(args, settings) -> int:
    sheet = ApplicationSheet.from_settings(settings)
    if args.action == "show":
        apps = sheet.find(args.find) if args.find else sheet.applications()
        cols = sheet.columns()
        shown = [k for k in ("company", "role", "status", "applied_at", "last_update") if k in cols.fields]
        t = Table("Row", *(cols.headers[cols.fields[k]] for k in shown), title=f"{sheet.tab}: {len(apps)} rows")
        for a in apps[-args.limit:]:
            t.add_row(str(a.row), *(a.fields.get(k, "") for k in shown))
        console.print(t)
        return 0
    if args.action == "set":
        changes = dict(kv.split("=", 1) for kv in args.values)
        res = sheet.update(args.row, changes, expect_company=args.expect_company)
        console.print(f"Row {res['row']}: {res['written']}")
        return 0
    if args.action == "add":
        res = sheet.append(dict(kv.split("=", 1) for kv in args.values))
        console.print(f"Added row {res['row']}: {res['written']}")
        return 0
    return 1


def cmd_gmail(args, settings) -> int:
    gmail = GmailClient.from_settings(settings)
    if args.action == "search":
        mails = gmail.search(args.query or settings.gmail_query, args.limit)
        t = Table("Received", "From", "Subject", "Id", title=f"{len(mails)} messages")
        for m in mails:
            t.add_row(m.received_at[:16].replace("T", " "), f"{m.sender} <{m.sender_email}>", m.subject, m.id)
        console.print(t)
        return 0
    if args.action == "show":
        m = gmail.get(args.id)
        console.print(f"[bold]{m.subject}[/bold]\nFrom: {m.sender} <{m.sender_email}>\n"
                      f"Received: {m.received_at}\nLabels: {', '.join(m.labels)}\n\n{m.body}")
        return 0
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m jobtracker",
                                description="Gmail + Google Sheets job-application tracker")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth", help="sign in to Google once (opens a browser)")
    a.add_argument("--no-browser", action="store_true", help="print the consent URL instead of opening it")
    sub.add_parser("doctor", help="check the OAuth token, Gmail and the sheet's columns")

    s = sub.add_parser("sheet", help="read or edit the application sheet")
    s_sub = s.add_subparsers(dest="action", required=True)
    show = s_sub.add_parser("show", help="list applications")
    show.add_argument("--find", help="only rows whose company or role contains this")
    show.add_argument("--limit", type=int, default=50)
    st = s_sub.add_parser("set", help="update cells: set 12 status=Interview notes='call on Fri'")
    st.add_argument("row", type=int)
    st.add_argument("values", nargs="+", metavar="field=value")
    st.add_argument("--expect-company", help="refuse if that row's company no longer matches")
    ad = s_sub.add_parser("add", help="append an application: add company=Acme role='AI Engineer'")
    ad.add_argument("values", nargs="+", metavar="field=value")

    g = sub.add_parser("gmail", help="search or read Gmail (read-only)")
    g_sub = g.add_subparsers(dest="action", required=True)
    gs = g_sub.add_parser("search", help="list messages (default: JOBTRACKER_GMAIL_QUERY)")
    gs.add_argument("query", nargs="?", default="")
    gs.add_argument("--limit", type=int, default=20)
    gshow = g_sub.add_parser("show", help="read one message as text")
    gshow.add_argument("id")

    args = p.parse_args(argv)
    try:
        settings = config.load()
        handler = {"auth": cmd_auth, "doctor": cmd_doctor, "sheet": cmd_sheet, "gmail": cmd_gmail}[args.cmd]
        return handler(args, settings)
    except (AuthError, SheetError, GmailError, config.ConfigError) as e:
        console.print(f"[red]{e}[/red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
