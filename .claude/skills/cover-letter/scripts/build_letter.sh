#!/usr/bin/env bash
# WSL wrapper: `plan` / `check-ats` → letter_plan.py / letter_check.py (WSL python3);
# everything else runs build_letter.py with the project's Windows venv, converting every
# argument that is an existing POSIX path (or whose parent dir exists) to a
# Windows path so python.exe / Word can open it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# keyword plan / check run in WSL python3 (stdlib; read the experience DB), no path conversion
case "${1:-}" in
  plan)      shift; exec python3 "$HERE/letter_plan.py" "$@" ;;
  check-ats) shift; exec python3 "$HERE/letter_check.py" "$@" ;;
esac
PROJECT_ROOT="$(cd "$HERE/../../../.." && pwd)"
PY="$PROJECT_ROOT/.venv/Scripts/python.exe"

if [[ ! -x "$PY" ]]; then
  echo "build_cv.sh: Windows venv python not found at $PY" >&2
  exit 1
fi

args=()
for a in "$@"; do
  if [[ "$a" == /* ]] && { [[ -e "$a" ]] || [[ -d "$(dirname "$a")" ]]; }; then
    args+=("$(wslpath -w "$a")")
  else
    args+=("$a")
  fi
done

exec "$PY" "$(wslpath -w "$HERE/build_letter.py")" "${args[@]}"
