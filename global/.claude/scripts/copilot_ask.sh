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
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: copilot_ask.sh PROMPT" >&2; exit 2; }
GH_TOKEN="$(gh auth token 2>/dev/null)" || { echo "copilot_ask: gh is not signed in" >&2; exit 3; }
export GH_TOKEN
exec copilot -s --disable-builtin-mcps --deny-tool shell --deny-tool write -p "$1"
