#!/usr/bin/env bash
# PreCompact hook: whatever this prints (exit 0) is appended to the compaction summarizer's
# instructions, for auto and manual /compact alike. It steers what the summary keeps, so a
# compacted session doesn't lose the facts it acts on. Exit 0 always: a failing hook must never
# block compaction.
cat >/dev/null   # the event JSON on stdin is not needed
cat <<'EOF'
Additional instructions for this summary (Jonathan's standing rules):
- Keep word for word, as quotes, every correction, decision and preference Jonathan stated in this session, with what it applied to.
- Keep every identifier exactly: Gmail draft and message IDs, commit SHAs, issue and PR numbers with their repo, branch and worktree names, file paths with line numbers, URLs, Console workspace names.
- Keep every open loop: steps promised to Jonathan, steps waiting on him, notes waiting on Hatch or the second tower (with the note ID), and anything queued in muse-inbox.md this session.
- Mark each status fact as verified (say how: command, API, file) or as assumed.
- Drop raw tool output (file contents, command output, search results, diffs). Name the file or command instead, so it can be re-read.
- Never carry passwords, tokens, keys or webhook URLs into the summary, even redacted ones.
EOF
exit 0
