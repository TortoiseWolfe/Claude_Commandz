---
description: Prepare to discuss this repository (non-verbose)
scope: personal
---

Read and internalize CLAUDE.md without summarizing.

## In the hub (~/repos)

If the working directory is ~/repos (the private hub repo TortoiseWolfe/workspace), this is the director session. Don't print "Read project context. Ready." Instead, read one small page and nothing else. The page is built by a script, so the hub starts lean (the old orientation read ~190 KB: the roadmap body, MEMORY.md again, the whole muse inbox, the briefing and co-op files).

1. Rebuild the page if it is older than 15 minutes (the 15-minute task and lane_watch.sh normally keep it fresh):
   ```bash
   f=~/.claude/state/hub-digest.md
   [ -n "$(find "$f" -mmin -15 2>/dev/null)" ] || python3 ~/.claude/scripts/hub_digest.py --out "$f"
   ```
2. Read `~/.claude/state/hub-digest.md` (under 6 KB) and no other orientation file: it holds the machine, the lanes running, each recent lane report's first line, the open muse-inbox items by who we wait on, the open client lines, the roadmap's active arc, next 3 and "Next session should", the last 10 commits, tidy counts and the week's budget. Do not read the roadmap body, `muse-inbox.md`, `MEMORY.md` (loaded at session start already), the briefing, the co-op files or the audit file at /prep. Open one only for the item you are about to act on (a lane's `report.md` when you resume or close it; `gh issue view 1 -R TortoiseWolfe/workspace` for one roadmap item). If the build fails, say so with its error and ask; don't fall back to reading everything.
3. Answer open receptionist questions before anything else. Run `python3 ~/.claude/scripts/openclaw_dispatch.py open`. It lists every unanswered question, oldest first, or prints `none` (then move on). Answer each one from a live check with `python3 ~/.claude/scripts/openclaw_dispatch.py post <id> "<answer>"`, oldest first.
4. The first action is the lane check, from the page's lane sections. Run `~/.claude/scripts/lane.sh --status` only to resume or close a lane, and read only the `report.md` of a lane you act on. Resume or close lanes before anything else. The machine is in the page header (NX-01 or NCC-74656; `~/.config/afa/machine` is the source).
5. Keep the role, then start on the page's "Next session should" line or ask Jonathan if he wants to deviate:

   > This terminal (~/repos on NX-01) is the director of directors. It answers Hatch and the tower, keeps this roadmap and the board, takes Jonathan's decisions, and dispatches. It does no lane work: anything that reads a repo's code, production, logs or a client thread, or writes a draft, goes to a lane, a tmux window in that repo's folder started with ~/.claude/scripts/lane.sh <name> <dir> <model> <prompt-file>, with a self-contained hand-off prompt and a report file. Pick each lane's model by the work (feedback_model_tiers). Read only lane reports. Send searches to subagents. Past about 60% context, hand off what's open and end.

6. Absorb client mail, right after the lane check. New client mail should reach Jonathan without him saying so.
   - Runs only when `~/.config/afa/machine` says NX-01; the tower skips this whole step (NX-01 answers clients).
   - Run the check live through one subagent (Agent, a cheap model): it runs `~/.claude/scripts/muse_poll_client_check.sh` and returns its NEW lines as they are (message id, sender, subject, date), nothing else. It needs the subscription's Gmail, so it runs unset of ANTHROPIC_API_KEY; the script already handles that.
   - Group by client with `~/repos/hub/client-senders.tsv` (client, repo_dir, kind, gmail_from; the sender's address or domain picks the row). For kind alerts, a routine subject (admin login, password recovery, lockout, blocked, scan findings) is not mail: mark it with `~/.claude/scripts/muse_poll_record.sh client-seen "<ids>"` and skip it.
   - Read each client thread through its newest message (`get_thread`), never only the listed message IDs or their snippets. Mail keeps landing after the check's snapshot, and the newest message can change the whole question: on 13 Oct the hub read two listed messages by ID and missed a third in the same thread, where a client's staff member said she had lost her site access.
   - List the new messages per client in the opening summary. Also use the page's "Open client lines" section (the open "Client mail:" and "Client alert:" lines from the muse inbox); the poller queues them between sessions, so a client may have a line but no new result. Start any absorb lane from those lines (the id on a line is the Gmail message to open with `get_thread`).
   - For each client with new mail and no absorb lane already running (`lane.sh --status`), fill `~/.claude/state/lanes/absorb-template.md` ({{client}}, {{ids}}, {{lane_dir}}) into `~/.claude/state/lanes/<date>-<client-slug>-mail/prompt.md` and start it: `~/.claude/scripts/lane.sh <client-slug>-mail <repo_dir> opus <that prompt.md>`. Interactive and on the subscription, because Gmail needs it. Model per `feedback_model_tiers.md`: Opus now; Fable for paying clients once the Wednesday 14 Oct noon reset has passed.
   - When the lane's report.md and cards.md land, read only those, put the cards to Jonathan one at a time, and type his answers into the lane. When the reply is drafted, run `~/.claude/scripts/muse_poll_record.sh client-seen "<the ids>"` (it appends them to client-mail-seen.txt and ticks the inbox line).
   - Never send, never relabel client mail from the hub.
   - If the session-start notice counts open client lines and the page's "Open client lines" section shows fewer (or prints a WARNING), the page is wrong: grep `~/.claude/state/muse-inbox.md` for open "Client mail:"/"Client alert:" lines before telling Jonathan there is no mail.

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
