#!/usr/bin/env bash
# WSL wrapper: runs build_cv.py with the project's Windows venv, converting every
# argument that is an existing POSIX path (or whose parent dir exists) to a
# Windows path so python.exe / Word can open it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

exec "$PY" "$(wslpath -w "$HERE/build_cv.py")" "${args[@]}"
