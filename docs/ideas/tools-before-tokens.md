# Idea: tools carry the work, models make the calls

Jonathan's strategy for the whole workflow, 25 Oct 2026:

- About 12:00 AM: "graphify, obsidian, scion and playwright should scaffold a lot of the tooling?"
- 9:38 AM: "don't forget we can explore python helpers to offload token burns"
- 9:40 AM: "it came from me, my suggestion of how this AI workflow in the HUB and you and the AFA and Hatch doesn't have to burn all it's tokens to make progress"

## The idea

The hub, the lanes, the AFA and Hatch spend most of their tokens re-reading and re-checking things: files, mail, web pages, their own history.
A plain tool can do that part for free and hand the model a short answer.
The model then spends tokens only where judgment is needed.

## Which tool takes which job

| Tool | The work it takes off the model | State on 25 Oct |
|---|---|---|
| graphify | The map of code and docs. A lane asks the graph instead of reading files. Code extraction is local and free. | Code graphs in about 20 repos. A skills-map graph (226 nodes) built 25 Oct. |
| Obsidian | A view of the same graph that Jonathan can browse. Costs no tokens at all. | First vault (246 notes) built 25 Oct, kept private. |
| Playwright | Browser steps. One script per site or form, not a model driving every click. | Fleet browser plus per-job submit scripts. A shared ats-submit.js is already a hub job (25 Oct, 9:35 AM). |
| Scion | Running many agents at once: containers, worktrees, credentials. | Not evaluated yet. See scion-eval.md. |
| Python helpers | Repeated checks (mail, notes, status, budget) reduced to a few lines of output. | hub_digest.py, skills_check.py, lane_spend.py, usage_ledger.py, openclaw_dispatch.py, gmail_api.py. |

## Proof that helpers pay off

- hub_digest.py: the hub's start-up used to read about 190 KB of orientation files. It now reads one page under 6 KB.
- The client-mail check lists messages from Gmail metadata through gmail_api.py and never reads a body. On 25 Oct it answered "none" in one tool call.

## First candidates (measured 25 Oct)

1. **notes_check.py**, the session-start check for Hatch's and the tower's notes.
   On 25 Oct it took 8 Gmail connector calls. They returned 21 full note bodies, and every one was already in agent-notes-processed.txt.
   A script on gmail_api.py (drafts-list and messages-list) can drop the handled IDs first.
   The session-start hook then prints "no new notes", or only the new subjects and IDs.
2. **ats-submit.js**, one shared form-submit script per applicant-tracking system. Already on the hub list.
3. **A token-burn audit.** Rank what sessions re-read most (usage_ledger.py and lane_spend.py hold the numbers), and turn each repeat into a helper.
   Start from the 11 Oct finding: 87% of Opus use was sessions re-reading their own history.

## Rules for every helper

- Python standard library only, no pip installs on the host (like gmail_api.py).
- Never prints a secret. Config is read under the redact_view.py rules.
- Ships with a --self-test or a test file.
- Prints only what the model needs. The detail goes to a log file.
