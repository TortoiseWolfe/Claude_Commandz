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

5. Absorb client mail, right after the lane check. New client mail should reach Jonathan without him saying so.
   - Runs only when `~/.config/afa/machine` says NX-01; the tower skips this whole step (NX-01 answers clients).
   - Run the check live through one subagent (Agent, a cheap model): it runs `~/.claude/scripts/muse_poll_client_check.sh` and returns its NEW lines as they are (message id, sender, subject, date), nothing else. It needs the subscription's Gmail, so it runs unset of ANTHROPIC_API_KEY; the script already handles that.
   - Group by client with `~/repos/hub/client-senders.tsv` (client, repo_dir, kind, gmail_from; the sender's address or domain picks the row). For kind alerts, a routine subject (admin login, password recovery, lockout, blocked, scan findings) is not mail: mark it with `~/.claude/scripts/muse_poll_record.sh client-seen "<ids>"` and skip it.
   - Read each client thread through its newest message (`get_thread`), never only the listed message IDs or their snippets. Mail keeps landing after the check's snapshot, and the newest message can change the whole question: on 13 Oct the hub read two listed messages by ID and missed a third in the same thread, where a client's staff member said she had lost her site access.
   - List the new messages per client in the opening summary. Also read the open "Client mail:" and "Client alert:" lines in `~/.claude/state/muse-inbox.md`; the poller queues them between sessions, so a client may have a line but no new result.
   - For each client with new mail and no absorb lane already running (`lane.sh --status`), fill `~/.claude/state/lanes/absorb-template.md` ({{client}}, {{ids}}, {{lane_dir}}) into `~/.claude/state/lanes/<date>-<client-slug>-mail/prompt.md` and start it: `~/.claude/scripts/lane.sh <client-slug>-mail <repo_dir> opus <that prompt.md>`. Interactive and on the subscription, because Gmail needs it. Model per `feedback_model_tiers.md`: Opus now; Fable for paying clients once the Wednesday 14 Oct noon reset has passed.
   - When the lane's report.md and cards.md land, read only those, put the cards to Jonathan one at a time, and type his answers into the lane. When the reply is drafted, run `~/.claude/scripts/muse_poll_record.sh client-seen "<the ids>"` (it appends them to client-mail-seen.txt and ticks the inbox line).
   - Never send, never relabel client mail from the hub.

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
