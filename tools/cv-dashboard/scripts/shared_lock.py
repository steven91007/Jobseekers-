#!/usr/bin/env python3
"""Cross-process lock for state that every dashboard Claude run shares.

Shared state: cv/profile/library_seed.json, cv/profile/candidate_confirmations.md, the SQLite DB
(library_db.py init / record / set-*) and the Notion rows. The lock is an fcntl.flock on
tools/cv-dashboard/data/shared.lock (CV_DASHBOARD_DATA overrides the folder); who holds it is in
shared.lock.holder.json next to it.

  shared_lock.py run [--run ID] -- python3 .claude/skills/tailor-cv/scripts/library_db.py init
      wait for the lock, run the command, release. Only the tailor-cv scripts may be run this way
      (same scope as the run's own Bash allowlist). If this run already holds the lock (hold),
      the command runs straight away.
  shared_lock.py hold --run ID [--seconds N]
      take the lock for a multi-step edit; a background holder keeps it until `release` or N s.
  shared_lock.py release --run ID
  shared_lock.py status

The run id defaults to $CV_DASHBOARD_RUN_ID. Waiting gives up after 10 minutes (exit 3).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("CV_DASHBOARD_DATA") or Path(__file__).resolve().parent.parent / "data")
LOCK_FILE = DATA_DIR / "shared.lock"
HOLDER_FILE = DATA_DIR / "shared.lock.holder.json"
WAIT_TIMEOUT_S = 600
POLL_S = 0.5
REPORT_EVERY_S = 15
MAX_HOLD_S = 1800
ALLOWED = {"python3": ".claude/skills/tailor-cv/scripts/", "bash": ".claude/skills/tailor-cv/scripts/"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_holder() -> dict | None:
    try:
        h = json.loads(HOLDER_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return h if isinstance(h, dict) and alive(int(h.get("pid", 0))) else None


def write_holder(info: dict) -> None:
    tmp = HOLDER_FILE.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(info), "utf-8")
    os.replace(tmp, HOLDER_FILE)


def clear_holder(pid: int) -> None:
    try:
        h = json.loads(HOLDER_FILE.read_text("utf-8"))
        if h.get("pid") == pid:
            HOLDER_FILE.unlink()
    except (OSError, ValueError):
        pass


def describe(h: dict | None) -> str:
    if not h:
        return "unknown holder"
    what = h.get("command") or f"hold until {h.get('until', '?')}"
    return f"run {h.get('run') or '?'} (pid {h.get('pid')}, since {h.get('since')}): {what}"


def acquire(run: str, out=sys.stderr) -> int:
    """Block until the flock is ours; returns the fd. Exits 3 on timeout."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    start = last = time.monotonic()
    reported = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if reported:
                print(f"shared lock acquired by run {run or '?'} after {time.monotonic() - start:.0f}s", file=out, flush=True)
            return fd
        except BlockingIOError:
            pass
        t = time.monotonic()
        if not reported or t - last >= REPORT_EVERY_S:
            print(f"waiting for shared lock, held by {describe(read_holder())}", file=out, flush=True)
            reported, last = True, t
        if t - start >= WAIT_TIMEOUT_S:
            print(f"gave up after {WAIT_TIMEOUT_S}s; still held by {describe(read_holder())}", file=out, flush=True)
            os.close(fd)
            sys.exit(3)
        time.sleep(POLL_S)


def check_command(cmd: list[str]) -> None:
    if len(cmd) < 2 or cmd[0] not in ALLOWED:
        sys.exit("shared_lock.py run: command must be `python3|bash .claude/skills/tailor-cv/scripts/<script> …`")
    if not cmd[1].startswith(ALLOWED[cmd[0]]) or ".." in Path(cmd[1]).parts:
        sys.exit(f"shared_lock.py run: only {ALLOWED[cmd[0]]}* may run under the lock, not {cmd[1]!r}")


def cmd_run(args: argparse.Namespace) -> int:
    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    check_command(cmd)
    h = read_holder()
    if args.run and h and h.get("run") == args.run:
        return subprocess.call(cmd)  # this run already holds the lock (hold … release)
    fd = acquire(args.run)
    try:
        write_holder({"run": args.run, "pid": os.getpid(), "since": now(), "command": " ".join(cmd)})
        return subprocess.call(cmd)
    finally:
        clear_holder(os.getpid())
        os.close(fd)


def cmd_holder(args: argparse.Namespace) -> int:
    """Background process started by `hold`: owns the flock until SIGTERM or the deadline.

    It talks to `hold` only through its stdout pipe (waiting messages, then "acquired") and closes it
    afterwards, so the caller's own pipes never stay open behind it."""
    fd = acquire(args.run, out=sys.stdout)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    until = time.time() + args.seconds
    write_holder({"run": args.run, "pid": os.getpid(), "since": now(), "until": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(until))})
    print("acquired", flush=True)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    while not stop["now"] and time.time() < until:
        time.sleep(0.2)
    clear_holder(os.getpid())
    os.close(fd)
    return 0


def cmd_hold(args: argparse.Namespace) -> int:
    if not args.run:
        sys.exit("shared_lock.py hold: --run ID is required")
    h = read_holder()
    if h and h.get("run") == args.run:
        print(f"run {args.run} already holds the shared lock (until {h.get('until', '?')})")
        return 0
    seconds = max(1, min(args.seconds, MAX_HOLD_S))
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "_holder", "--run", args.run, "--seconds", str(seconds)],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, start_new_session=True,
    )
    assert child.stdout
    for line in child.stdout:
        if line.strip() == "acquired":
            break
        print(line.rstrip(), file=sys.stderr, flush=True)
    else:
        child.wait()
        return child.returncode or 3
    child.stdout.close()
    print(f"shared lock held by run {args.run} for up to {seconds}s (pid {child.pid}); "
          f"re-read the shared files now, then `shared_lock.py release --run {args.run}`")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    h = read_holder()
    if not h or h.get("run") != args.run or "until" not in h:
        if not args.quiet:
            print(f"run {args.run} does not hold the shared lock ({'held by ' + describe(h) if h else 'free'})", file=sys.stderr)
        return 0 if args.quiet else 1
    pid = int(h["pid"])
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if not alive(pid):
            break
        time.sleep(0.05)
    if not args.quiet:
        print(f"shared lock released by run {args.run}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    h = read_holder()
    print(f"held by {describe(h)}" if h else "free")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_run = os.environ.get("CV_DASHBOARD_RUN_ID", "")
    p = sub.add_parser("run", help="wait for the lock, run a tailor-cv script, release")
    p.add_argument("--run", default=default_run)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("hold", help="take the lock for a multi-step edit")
    p.add_argument("--run", default=default_run)
    p.add_argument("--seconds", type=int, default=600)
    p.set_defaults(fn=cmd_hold)
    p = sub.add_parser("_holder")
    p.add_argument("--run", required=True)
    p.add_argument("--seconds", type=int, required=True)
    p.set_defaults(fn=cmd_holder)
    p = sub.add_parser("release", help="give the lock back")
    p.add_argument("--run", default=default_run)
    p.add_argument("--quiet", action="store_true", help="no output, exit 0 even if not held (used by the server)")
    p.set_defaults(fn=cmd_release)
    p = sub.add_parser("status", help="who holds the lock")
    p.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
