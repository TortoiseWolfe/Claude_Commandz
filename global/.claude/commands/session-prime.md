---
description: Create or update a rolling "session-prime" issue — the issue body IS the roadmap (active arc → next 3 sessions → backlog), comments are the audit trail of changes
scope: personal
---

Idempotently maintain a single rolling GitHub issue (label `next-session`) that acts as the **durable roadmap** for a repo. The body carries the always-current plan (active arc, next 3 sessions queued, backlog). Comments are the audit trail of what shipped + what changed in the roadmap.

## Mental model

- **Body** = always-current truth. Changes on every run. You write three fields of it: the active arc, the next 3 sessions, and the "next session should" line.
- **Comments** = audit trail. Never rewritten; they accumulate. A script writes the facts part of each one.
- **The facts and the posting are a script's job** (`~/.claude/scripts/session_prime_facts.py`): commits, PRs and issues opened or closed, lanes closed with each report's first line, inbox items ticked, the drift check, the stamp, the updatedAt guard, the body write, the comment and the drift-cache seed. Don't gather those by hand and don't rebuild the stamp or the comment by hand.
- **In the hub, /prep no longer reads this issue's body**: it reads the status page `~/.claude/state/hub-digest.md`, which a script builds from this roadmap and other sources. So the fields you write must be short and current; the page quotes the active arc's head and open items, the Next 3 summary and session labels, and the first paragraph of "Next session should".

## What to do

### 1. Identify the repo and the machine

The script reads the repo from `gh repo view` and the machine from `~/.config/afa/machine` (the fleet name, not the hostname: hostnames get renamed). Missing file and the motherboard is `PRO Z690-A` → NX-01, `PRIME Z790M-PLUS` → NCC-74656: write the name into the file. Unknown → STOP and ask. The script refuses to post with no machine.

### 2. Run the facts (in the repo's folder)

```bash
python3 ~/.claude/scripts/session_prime_facts.py
```

It finds the one open `next-session` issue (none → SEED, step 5; several → STOP and ask which is canonical), saves the issue's `updatedAt` as READ_AT, and prints the facts since the last comment, the drift check, the stamp and, for the hub, the tidy counts. Read that; it replaces the git log, `gh` listing and lane-report reading.

### 3. Write the three fields (and a note)

Read only what you need of the current body (`gh issue view <n> --repo <owner>/<repo> --json body --jq .body`; in the hub, only the blocks you are changing). Write each field to a file in the scratchpad; each file is the whole replacement block, wrapper included:

- `arc.md`: the active arc. A `<details>` block whose `<summary>` starts `<b>Arc N`, or a `### Active arc` section. If the user said the arc is done or changed direction, swap it; else drop what was completed and add what was discovered. The script replaces the block with the same arc number.
- `next3.md`: a `<details>` block whose `<summary>` starts `<b>Next 3 sessions`, or a `### Next 3 sessions queued` section. Re-prioritize; promote from the backlog, demote what the user de-prioritized.
- `next-should.md`: just the paragraph(s) under "### Next session should" (the script keeps the heading). One sentence that tells a cold start where to begin.
- `note.md` (optional): extra comment sections that are not facts: **Roadmap changes** (what is different in the body), **Watch** (time-sensitive items with dates).

**Workspace hub only:** the facts output ends with the tidy report for THIS machine's `~/repos`. If it prints anything, add or replace one backlog line, "**Tidy ~/repos (<MACHINE>)**", summarising each section with its count. Drop only your own machine's line when the report comes back empty. A tidy line without a machine name was written by NX-01.

**Phase 3 of the token week (Tuesday 6:00 AM Eastern to the Wednesday 12:00 PM reset): nothing waits for the reset.** The hub holds no reserve for after it. In phase 3, never write a "Next session should" line or a "Next 3 sessions" step as "after the reset"; write it as "now", and start it. A lane that hits the limit writes handoff.md and resumes after noon (Jonathan, 28 Oct about 9:20 AM: "the closer we get the the reset, the less we need in reserve until we make sure to use what's left Tuesday night Wensday morning"). The digest's "Budget this week" section says "Phase 3: no reserve; start anything held for after the reset now" when it is phase 3.

A field that cannot be found in the body is an error, not a guess; fix the file. Backlog items stay as they are; edit the body by hand (`gh issue edit`) only for something outside these three fields, and then run step 2 again afterwards so READ_AT is current.

**Workspace hub on NCC-74656 (the tower):** NX-01 owns the hub roadmap's priorities. The script refuses `--arc`, `--next3` and `--next-should` there. Post a note only (its own tower follow-ups, items it finished) and edit just its own lines by hand.

**Workspace hub on NX-01 only: it is the director of directors.** The hub did lane work in its own context on 2026-10-11 (production logs, client drafts) instead of dispatching it. Three rules, so the roadmap keeps the hub in its lane:
- **The prime-prompt block carries this paragraph**, word for word. Add it if missing, never paraphrase it:

  > This terminal (~/repos on NX-01) is the director of directors. It answers Hatch and the tower, keeps this roadmap and the board, takes Jonathan's decisions, and dispatches. It does no lane work: anything that reads a repo's code, production, logs or a client thread, or writes a draft, goes to a lane, a tmux window in that repo's folder started with ~/.claude/scripts/lane.sh <name> <dir> <model> <prompt-file>, with a self-contained hand-off prompt and a report file. Pick each lane's model by the work (feedback_model_tiers). Read only lane reports. Send searches to subagents. Past 330K tokens of context (the last tenth before auto-compact at 367K; the status line shows it as about 10% left), hand off what's open and end, never before: open work comes first (Jonathan, 30 Oct: never auto-compact; 31 Oct at 157K: "still too soon, fifty seven percent left, wtf are you doing?").

- **"Next 3 sessions" items are written as lanes**: tmux window name, folder, model, the hand-off's first action, and the report path. Never as work for the hub itself. Lane prompts and reports live in `~/.claude/state/lanes/<date>-<name>/`.
- **The hub's first action in a new session is the lane check.** Closing the hub terminal does not stop the lanes (they are windows in the detached tmux session `lanes`); only WSL shutting down does. So the prime-prompt block says, before anything else: *run `~/.claude/scripts/lane.sh --status`, read every report.md it lists, and resume lanes before anything else.* A lane stays open until its context is full; a report, a merge or "done" does not end it (Jonathan, 19 Oct: "quite closing the fucking lane unless it's context is full, you're obviously no where near being done with it"). `--status` prints each window's name, whether its report is written, working / idle / error (a "Credit balance too low" or "API Error" in the pane is error), its last pane line, and its folder, model and Claude session id from `~/.claude/state/lanes/index.tsv`; lanes from the last 3 days with no window and no report show as dead. A dead or crashed lane is restarted with `lane.sh --resume <name>` (`claude --resume <session-id> --model <m>` in a fresh window), and a message goes in with `lane.sh --send <name> <text>`, which retries Enter once and says whether it took.

### 4. Post

```bash
python3 ~/.claude/scripts/session_prime_facts.py --post --dry-run --arc arc.md --next3 next3.md --next-should next-should.md --note note.md
python3 ~/.claude/scripts/session_prime_facts.py --post --arc arc.md --next3 next3.md --next-should next-should.md --note note.md
```

`--dry-run` prints the comment and what would change in the body, and never posts. The real run:

1. checks the issue's `updatedAt` still equals READ_AT. If it moved, another machine or lane edited the issue in between: it stops with exit 6 and posts nothing. Re-read the body, redo the fields, run step 2 again. Never write over a newer body;
2. replaces the fields, refreshes the single `roadmap-stamp` line (tip rule: `origin/<default>`, or `HEAD` when that is an ancestor), and writes the body;
3. appends the comment (heading with the time, machine, folder and session; the facts; your note; the "Next session should" line);
4. seeds the drift-hook cache and runs `roadmap-drift.sh check`. Expect `drift 0 commits (exact, ...)`.

The comment says what a lane did only in the private hub: for any other repo, lanes, inbox and client lines are left out, because a public repo's issue must never carry them. Add `--session <id>` if you know the session id.

### 5. SEED (first run in a repo with no rolling issue)

Two paths: **seed-from-work** (you finished real work here this session; capture what shipped and what's next) or **seed-baseline** (no work here this session; the first comment says so, see B-2). Either way:

1. `gh label create next-session --color 0E8A16 --description "Rolling roadmap + priming prompt for new sessions" --repo <owner>/<repo> 2>/dev/null || true`
2. Gather state: `git log --oneline -20`, `git branch --show-current`, open issues and PRs, recently closed, the memory index if present, and any time-sensitive items the user mentioned.
3. Write the body from **Appendix A**; fill the stamp with the same rule as the script (`BRANCH` from `origin/HEAD` else `main`; `TIP=origin/$BRANCH`, or `HEAD` if that is an ancestor; `TIP_SHA`; `GENERATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)`).
4. `gh issue create --repo <owner>/<repo> --title "session-prime: roadmap + orient-fast prompt for new sessions" --label next-session --label documentation --body-file <file>`; replace `#NNN` with the number and `gh issue edit` the body.
5. Post the first comment: for seed-from-work, run step 2 and `--post` (it writes the facts); for seed-baseline use **B-2** below.
6. Seed the drift cache and confirm: `bash ~/.claude/hooks/roadmap-drift.sh seed-cache "<owner>/<repo>" "<number>" "$TIP_SHA" "$BRANCH" "$GENERATED"` then `bash ~/.claude/hooks/roadmap-drift.sh check` (expect drift 0).

### 6. Surface the URL

```
✓ session-prime issue: https://github.com/<owner>/<repo>/issues/<number>
✓ body updated, comment appended (or: seeded)
```

---

## Appendix A: Body template

Substitute as noted. Inner backticks are literal — they form fenced code blocks in the rendered issue.

```markdown
**Purpose:** this issue is the durable roadmap for <owner>/<repo>. The body below is always-current — active arc, next 3 sessions queued, backlog. Comments are the audit trail (what shipped + what changed in the roadmap, per session). The prime prompt at the top tells a fresh Claude session to read this body first.

---

## Prime prompt (copy from here into a fresh Claude session) In the hub (~/repos), typing /prep loads this block, so nothing needs pasting.

\`\`\`
/prep

In the hub, /prep also absorbs client mail (step 5 of its hub section): it checks for new client messages and starts an absorb lane per client. Keep that step in the orientation.

Then check this issue (<owner>/<repo>#NNN) — its BODY is the current roadmap. Read everything below the prime prompt: active arc, next 3 sessions queued, backlog. The most recent COMMENT on this issue is the audit trail of what changed since the previous session.

Supplementary context (read if the body's roadmap references them or if the user's first message takes us off-roadmap):
1. <MEMORY_PATH if exists> — top of the index has the most-recently-written memories.
2. `git log --oneline -20` — what shipped in the last couple of sessions.
3. Open issues: `gh issue list --repo <owner>/<repo> --state open`
4. Open PRs: `gh pr list --repo <owner>/<repo> --state open`

Operational reminders from CLAUDE.md / MEMORY.md you'll trip over otherwise:
- <REMINDER 1 — repo-specific from CLAUDE.md or memory>
- <REMINDER 2>
- <REMINDER 3>

Then either start on the roadmap's "next session should" item, or ask the user if they want to deviate.
\`\`\`

## Stop reading here when priming — everything below is the roadmap

---

## Roadmap

### Active arc

**<arc name>** — <one-sentence why this arc exists / what it ships>

Status: <one line — % done, blocker, etc.>

Remaining tasks (in suggested order):
- [ ] <task>
- [ ] <task>
- [ ] <task>

Blockers / decisions needed:
- <blocker or "(none)">

---

### Next 3 sessions queued

After the active arc lands (or in parallel where the user prefers), the next three sessions should pick from these, in priority order:

**Session +1:** <one-line label>
- Why: <one line>
- First action: <one line — concrete enough to start on without re-planning>

**Session +2:** <one-line label>
- Why:
- First action:

**Session +3:** <one-line label>
- Why:
- First action:

---

### Backlog (no scheduled position)

Named items that exist but aren't queued yet. Promote into "next 3 sessions" when priority emerges.

- **<label>** — <one line description / link to issue if applicable>
- **<label>** — <description>
- **<label>** — <description>

---

### Next session should

<One sentence — the single most important thing to do next. If the user starts a session and reads only this line, they should know where to start.>

---

## Why this exists

Sessions end. The next session starts cold. Without a single canonical "what was the world like when we last stopped + here's what to do next" pointer, the new model wastes context wandering. This issue is the one URL to paste into a fresh chat. The BODY carries the plan; comments form the audit trail.

## How to maintain

Run `/session-prime` at session-end. The skill rewrites the body to reflect what changed in the roadmap and appends a comment with the audit trail.

If the prime prompt itself becomes stale (memory file renamed, project changed shape), edit the body's prime-prompt block directly.

<!-- roadmap-stamp v1 sha=<TIP_SHA> branch=<BRANCH> generated=<GENERATED> issue=#NNN -->
```

**The stamp is the last line, and it is load-bearing.** It records the commit this roadmap was written against, so staleness becomes measurable instead of asserted — `git rev-list --count --first-parent <sha>..origin/<branch>`. It renders invisibly on GitHub. `~/.claude/hooks/roadmap-drift.sh` reads it; without it that hook falls back to the issue's last body edit, which is approximate because any edit resets the baseline.

If the body already contains a `roadmap-stamp` line, **replace it** — never append a second.

---

### B-2. Seeded baseline template (no work this session — just establishing the issue)

```markdown
## Issue seeded YYYY-MM-DD HH:MM <TZ> (<MACHINE>) — baseline snapshot, not a session log

The rolling-prime issue was created via `/session-prime` from a different repo's session (or just to bootstrap this repo's roadmap). **No work happened on this repo this session.** Use this comment as the starting snapshot; future `/session-prime` runs will add real session-end audit-trail entries on top.

**Current branch:** `<branch>`

**Last <N> commits:**
- [`<sha>`](https://github.com/<owner>/<repo>/commit/<sha>) — <message>
- ...

**Open PRs:** #N (description)

**Recently closed (last 7d):** #N, #N

**Open issues snapshot:** <one line — total count + label breakdown if many>

**Active work** (inferred from branch + recent commits, not user-confirmed):
- <inferred arc>

**Watch:**
- <known time-sensitive item, if any>

**Next session should:** Confirm the roadmap in the body matches reality, then start on whatever the body's "Next session should" line says.
```

---

## Edge cases

- **Not in a git repo / no GitHub remote / `gh` not authenticated** — the script stops with the error; fix it (`gh auth status`) and rerun. If `gh` is not installed (a fresh machine), install it into `~/.local/bin` from the cli/cli release, check it against the release's checksums, then have the user run `! gh auth login`.
- **Machine unknown** — ask the user. Never infer it from the hostname.
- **The other machine's comment is the newest one** — fine. Your comment adds this machine's side; correct a stale item only by editing that phrase in the body.
- **No commits since the last comment, but the roadmap should change anyway** — fine; the comment says "Nothing shipped" and carries your note.
- **The script's field locator misses** (an old body shape) — fix the field file's wrapper to match the body, or edit that one block by hand and rerun step 2.
- **Repo has no memory dir** — drop that bullet from a seeded prime prompt.
