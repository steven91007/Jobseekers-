#!/usr/bin/env bash
# Scheduled entry point of the morning run (Windows run_morning.ps1 → wsl.exe --cd <repo> -- bash -l tools/pipeline/run_morning.sh).
# Read-only except cv/digest/YYYY-MM-DD.md and the Google token refresh; classifier = Jev (TypeSafe), regex fallback.
set -u
cd "$(dirname "$0")/../.."
mkdir -p data/pipeline/logs
{
  echo "=== $(date '+%F %T') morning start ==="
  CLAUDE_SCHEDULED=1 python3 .claude/skills/morning-run/scripts/morning.py run --classifier jev "$@"
  echo "=== $(date '+%F %T') morning end (exit $?) ==="
} >> data/pipeline/logs/morning.log 2>&1
