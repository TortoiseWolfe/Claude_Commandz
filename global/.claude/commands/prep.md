---
description: Prepare to discuss this repository (non-verbose)
scope: personal
---

Read and internalize CLAUDE.md without summarizing.

## In the hub (~/repos)

If the working directory is ~/repos (the private hub repo TortoiseWolfe/workspace), this is the director session. Don't print "Read project context. Ready." Instead:

1. Read the machine name from `~/.config/afa/machine`.
2. Find the rolling roadmap issue: `gh issue list --repo TortoiseWolfe/workspace --label next-session --state open --json number -q '.[0].number'`
3. Read its body (`gh issue view <n> --repo TortoiseWolfe/workspace --json body -q .body`). Take the text between the heading that starts "## Prime prompt" and the heading that starts "## Stop reading here". It is the orientation Jonathan used to paste.
4. Follow it exactly as if he had pasted it, skipping its own leading "/prep" line. Its first action is `~/.claude/scripts/lane.sh --status`.

Lanes never run /prep, so this never turns a lane into the director.

## Everywhere else

## Instructions

1. Read `CLAUDE.md`
2. Do NOT summarize or explain

## Output

> "Read project context. Ready."

## Notes

- For role-specific priming, use `/prime [role]` instead
- This command is for general repo context only
