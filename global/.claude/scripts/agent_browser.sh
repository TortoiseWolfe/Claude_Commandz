#!/usr/bin/env bash
# Fleet browser helper: one Chromium profile that a human signs in to once and headless
# lanes then reuse through Playwright.
#
#   agent_browser.sh login <url>...   visible Chromium on the fleet profile, for a human to sign in
#   agent_browser.sh path             two lines: the chromium executable, then the profile dir
#                                     (for Playwright's launchPersistentContext)
#   agent_browser.sh busy             exit 0 if a Chromium already holds the profile, else 1
#
# Profile: ~/.config/afa/browser (mode 700, created if missing).
# Chromium: the newest ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome, launched plain
# (no automation flags) with --no-first-run.
#
# Rules:
#   - Lanes drive the profile headless only after the login window is closed.
#   - Never print cookies or tokens.
#   - Never copy content off a site unless asked.
set -euo pipefail
PROFILE="$HOME/.config/afa/browser"
# Tower (NCC-74656): Chromium libraries unpacked from Debian packages into the home folder (no root there)
CL="$HOME/band-wagon-tower/chromium-libs/root"
if [ -d "$CL" ]; then export LD_LIBRARY_PATH="$CL/usr/lib/x86_64-linux-gnu:$CL/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; fi

chrome() {
  local c
  c="$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1 || true)"
  [ -n "$c" ] && [ -x "$c" ] || { echo "no Playwright chromium under ~/.cache/ms-playwright" >&2; exit 1; }
  echo "$c"
}

profile() {
  mkdir -p "$PROFILE"
  chmod 700 "$HOME/.config/afa/browser"
}

# Chromium keeps SingletonLock (a symlink to host-pid) in the profile while it runs.
busy() {
  local lock="$PROFILE/SingletonLock" target pid
  [ -L "$lock" ] || return 1
  target="$(readlink "$lock")"; pid="${target##*-}"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

case "${1:-}" in
  login)
    shift
    [ $# -ge 1 ] || { echo "usage: agent_browser.sh login <url>..." >&2; exit 1; }
    bin="$(chrome)"; profile
    if busy; then echo "a Chromium already holds $PROFILE; close it first" >&2; exit 1; fi
    exec "$bin" --user-data-dir="$PROFILE" --no-first-run "$@"
    ;;
  path)
    chrome; echo "$PROFILE"
    ;;
  busy)
    busy
    ;;
  *)
    echo "usage: agent_browser.sh login <url>... | path | busy" >&2; exit 1
    ;;
esac
