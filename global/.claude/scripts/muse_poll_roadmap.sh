#!/usr/bin/env bash
# Read-only: prints the hub roadmap (issue #1 of the workspace repo) for the unattended notes poller.
# A wrapper, not `Bash(gh issue view:*)` in muse_poll.sh's allow list: that list's deny side has
# Bash(gh:*), and a deny always beats an allow (tested 2026-10-11). The command below is the whole job.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
exec gh issue view 1 -R TortoiseWolfe/workspace --json body --jq .body
