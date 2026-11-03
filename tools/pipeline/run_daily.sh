#!/usr/bin/env bash
# Scheduled entry point (Windows run_pipeline.ps1 → wsl.exe --cd <repo> -- bash -l tools/pipeline/run_daily.sh).
set -u
cd "$(dirname "$0")/../.."
mkdir -p data/pipeline/logs
{
  echo "=== $(date '+%F %T') scheduler start ==="
  python3 .claude/skills/daily-job-tracker/scripts/daily_pipeline.py --live "$@"
  pipeline_status=$?
  echo "=== $(date '+%F %T') scheduler end (exit $pipeline_status) ==="
} >> data/pipeline/logs/scheduler.log 2>&1
exit "$pipeline_status"
