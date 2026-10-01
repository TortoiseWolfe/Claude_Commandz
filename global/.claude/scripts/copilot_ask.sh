#!/usr/bin/env bash
# Free-panel wrapper for the GitHub Copilot CLI: one question in, one answer out.
#
# Auth: borrows the GitHub CLI's own login at call time (`gh auth token`, a gho_ OAuth token, which
# copilot accepts), so no Copilot token is ever written to disk. This machine has no keychain, and
# `copilot login` would otherwise want to store one in plain text.
#
# Read-only on purpose. That token can write to repos, so nothing in the prompt (a diff under review
# is untrusted text) may reach a tool that could use it: the built-in GitHub MCP servers are off, and
# the shell and write tools are denied. panel_review runs this in an empty temp dir with stdin closed.
#
# Hardening (2026-10-01 audit): an empty token is refused; a prompt starting with "-" is refused (it would
# be read as an option); copilot runs under `env -i` with only PATH, HOME, LANG, TERM and the token; and
# COPILOT_HOME is a fresh private temp dir, removed on exit, so no user MCP config, hooks, instructions or
# session store are ever read or written.
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: copilot_ask.sh PROMPT" >&2; exit 2; }
case "$1" in -*) echo "copilot_ask: prompt must not start with '-'" >&2; exit 2;; esac
GH_TOKEN="$(gh auth token 2>/dev/null)" || { echo "copilot_ask: gh is not signed in" >&2; exit 3; }
[ -n "$GH_TOKEN" ] || { echo "copilot_ask: gh returned an empty token" >&2; exit 3; }
umask 077
COPILOT_HOME="$(mktemp -d "${TMPDIR:-/tmp}/copilot-home.XXXXXX")"
trap 'rm -rf "$COPILOT_HOME"' EXIT
env -i PATH="$PATH" HOME="$HOME" LANG="${LANG:-C.UTF-8}" TERM=dumb GH_TOKEN="$GH_TOKEN" COPILOT_HOME="$COPILOT_HOME" \
  copilot -s --disable-builtin-mcps --deny-tool shell --deny-tool write -p "$1"
