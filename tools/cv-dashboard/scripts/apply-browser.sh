#!/usr/bin/env bash
# Start (or reuse) the visible Chromium that Apply runs drive through the Playwright MCP over CDP.
# WSL2: the window shows on the Windows desktop through WSLg; it outlives each headless run so the
# filled form stays open for the candidate to review and submit by hand.
#   APPLY_CDP_PORT      default 9223
#   APPLY_BROWSER_DIR   persistent profile, default tools/cv-dashboard/data/apply/profile (logins survive)
#   APPLY_CHROME        chrome binary, default: newest Playwright Chromium in ~/.cache/ms-playwright
#   APPLY_LIBS          extra shared libs (libnss3/libnspr4/libasound2 unpacked without sudo),
#                       default ~/.job-apply-browser-libs/root/usr/lib/x86_64-linux-gnu
set -euo pipefail
PORT="${APPLY_CDP_PORT:-9223}"
if curl -sf "http://127.0.0.1:${PORT}/json/version" >/dev/null; then
  echo "apply browser already up on ${PORT}"
  exit 0
fi
CHROME="${APPLY_CHROME:-$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1)}"
[ -x "$CHROME" ] || { echo "no Chromium: run 'npx playwright install chromium'" >&2; exit 1; }
LIBS="${APPLY_LIBS:-$HOME/.job-apply-browser-libs/root/usr/lib/x86_64-linux-gnu}"
[ -d "$LIBS" ] && export LD_LIBRARY_PATH="$LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
PROFILE="${APPLY_BROWSER_DIR:-$(cd "$(dirname "$0")/.." && pwd)/data/apply/profile}"
mkdir -p "$PROFILE"
# APPLY_HEADLESS=1 (tests): no window on the Windows desktop; CDP and screencast work the same
HEADLESS=()
[ "${APPLY_HEADLESS:-0}" = "1" ] && HEADLESS=(--headless=new --window-size=1280,900)
setsid nohup "$CHROME" "${HEADLESS[@]}" --remote-debugging-port="$PORT" --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check about:blank \
  >"$PROFILE/chrome.log" 2>&1 < /dev/null &
for _ in $(seq 1 20); do
  curl -sf "http://127.0.0.1:${PORT}/json/version" >/dev/null && { echo "apply browser started on ${PORT}"; exit 0; }
  sleep 0.5
done
echo "apply browser did not open port ${PORT}; see $PROFILE/chrome.log" >&2
exit 1
