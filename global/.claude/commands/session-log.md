---
description: Export the latest Claude Code session in this folder as a readable, scrubbed Markdown log
scope: personal
---

!python3 ~/.claude/scripts/session_to_md.py --latest "$PWD"

Use `--with-subagents` to include subagent prompts and activity, `--all` to keep system reminders, hook output, and attachment markers, or `--out <file>` to save the Markdown to a file.
