---
description: Hand the hub session off in one go (hand-off file, open question to Hatch, roadmap, commit and push, end marker)
---
# /handoff

Ends a hub session (~/repos) cleanly at about 150K of context, at the first natural break and never mid-task, as one motion with no stops for permission
(Jonathan approved the routine on 5 Nov: "we need a shortened command for that whole process like /handoff").
Outside the hub, write the repo's own hand-off note and stop. Lanes never run this; they write handoff.md and
get recycled (hub/rules/lanes.md).

Every fact comes from a live check, never from memory or a summary (feedback_verify_loose_ends_live.md).
Never trim his words; never put a password, token or member data in any file or note this writes.

## 1. Live state
- `~/.claude/scripts/lane.sh --status` and line 1 of `report.md` for every lane this session started, typed into or
  acted on.
- `~/.claude/scripts/muse_poll_record.sh open "Question"` and `open "Hub"`: lines this session added, ticked or left.
- The questions still waiting on Jonathan, in the order he should get them (from this session's own record).
- `git -C ~/repos status --short` (other sessions' files are listed, never committed).
- The newest earlier `~/.claude/state/hub-handoff-*.md`: its open items still stand unless this session closed them.

## 2. Write the hand-off file
`~/.claude/state/hub-handoff-<YYYY-MM-DD>-<HHMM>.md` (no clock times inside unless an item depends on one, such as a
due-by; then read `date`). Line 1 names the machine and which file it supersedes. Sections, in this order:
- **Jonathan's words tonight** (verbatim quotes; act on them, don't re-ask)
- **Waiting on Jonathan, in order**: each item a full question (what it touches, what yes and no do, the pick, the
  link). Mark the one sent to Hatch.
- **Done this session** (with PR numbers, merge commits, ticket links, draft ids)
- **Lanes** (name, repo, engine, line 1 of its report; WORKING means mid-job; EMPTY lanes and their replacements)
- **Open waits** (Hatch, the tower, CI) and **Not committed** (other sessions' files; leave)
- **Roadmap** (what changed on workspace#1)

**Keep it under 6 KB, with no Lessons section** (workspace ticket 96: the 111 November hub hand-offs averaged 10 KB, and a
lesson written in one reaches a single later session). Run `python3 ~/.claude/scripts/handoff_check.py --hub FILE`; it
prints "ok" or what is wrong (too big, a heading missing, a Lessons heading). A lesson becomes a hub-list job the same day:
`~/.claude/scripts/muse_poll_record.sh inbox "Job: <the check or rule it should become>"`; a leftover gets a job line too
(feedback_leftovers_get_a_job_line_not_a_handoff_note.md). Cut finished work to one line each; the pull request or ticket
link carries the rest. Fix the file until the check prints "ok" before step 3.
/prep also starts this file at the beginning of a session and updates it after each finished item; /handoff then
finishes it rather than starting from nothing.

## 3. The first waiting question goes to Hatch (ONLY when he has gone)
**Skip when Jonathan has typed in this session in the last two hours** (he is at the screen; the next hub asks him there).
Only if he has been silent that long and a question is still unanswered, send the first one to Hatch as ONE `[CC>MUSE]` draft through the agent-notes
skill: self-contained, two layers, repo and number in plain text, no URL (Gmail rewrites them), and say his quoted
answer back counts as his word (feedback_unanswered_questions_go_to_hatch.md). Read the latest `[CC>MUSE]` drafts on
the topic first. The rest stay in the file's order for the next hub.

## 4. Roadmap
Run /session-prime's step 2 (facts, READ_AT) and step 4 with `--next-should` at least: one sentence naming the new
hand-off file and the first thing to do. Also `--next3` or `--arc` when they changed. Exit 6 means the issue moved:
re-read and redo, never force.

## 5. Commit and push the hub
Only files this session changed, from the allowlist (CLAUDE.md, ACCOUNTS.md, README.md, hub/...). Branch off main,
commit with a message that says why (and the attribution line), run the README secrets scan on the index (gitleaks
in Docker with `timeout`, then known_secrets_scan.py), fast-forward main, push. A non-fast-forward from a remote
commit that touches other files: `git pull --rebase --autostash`, then push. A conflict: stop and say so.

## 6. End
- `touch ~/.claude/state/hub-handed-off.<session id>` (the stop hook lets the session end once a fresh hand-off file
  and this marker both exist); then answer no more lane wakes.
  Never take the id from the newest transcript in the project folder: another session may have written one more
  recently (6 Nov 2026: the marker went to the wrong id and the guard kept blocking). Take it from the
  lane-watch-guard block message, which says "this session's id is <id>, so run touch <path>": run that exact
  `touch`. If the guard has not blocked yet, end the turn once; its block message then names the path.
- Last message, outcome first, two layers: what was handed off, the hand-off file path on its own line, the question
  that went to Hatch, and: "Open a fresh terminal in ~/repos and run /prep."
