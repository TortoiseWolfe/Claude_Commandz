---
description: Create or update a rolling "session-prime" issue — the issue body IS the roadmap (active arc → next 3 sessions → backlog), comments are the audit trail of changes
scope: personal
---

Idempotently maintain a single rolling GitHub issue (label `next-session`) that acts as the **durable roadmap** for a repo. The body carries the always-current plan (active arc, next 3 sessions queued, backlog). Comments are the audit trail of what shipped + what changed in the roadmap.

The prime prompt that users paste into a fresh Claude session tells the new model: **read this issue's body** — the roadmap is right there.

## Mental model

- **Body** = always-current truth. Active arc with remaining tasks. Next 3 sessions queued in priority order. Named backlog items. The body changes on every run.
- **Comments** = audit trail. What shipped, what got pushed back, what got pulled forward, what changed in the body since last comment. Comments never get rewritten; they accumulate.
- **Prime prompt** (the fenced block inside the body) = the orientation text users paste into a fresh session. It tells the new model to read the rest of the body first.

## What to do

### 0. Identify the machine (before anything else)

Two PCs run this command against the same issues: **NX-01** (the main PC) and **NCC-74656** (the second tower). Since 2026-10-10 the tower's Windows hostname has read `NX01`, a near-match for NX-01, and a session there once believed it was NX-01. **Never use the hostname.**

```bash
MACHINE="$(cat ~/.config/afa/machine 2>/dev/null)"
if [ -z "$MACHINE" ]; then
  case "$(powershell.exe -NoProfile -Command '(Get-CimInstance Win32_BaseBoard).Product' 2>/dev/null | tr -d '\r')" in
    *"PRO Z690-A"*)      MACHINE=NX-01 ;;
    *"PRIME Z790M-PLUS"*) MACHINE=NCC-74656 ;;
  esac
fi
echo "machine: ${MACHINE:-UNKNOWN}"
```

- `~/.config/afa/machine` holds one line, the fleet name. If it's missing and the motherboard is one of the two above, write the name into it.
- **UNKNOWN** → STOP and ask the user which machine this is. Don't guess.
- Carry `MACHINE` through every step: every comment's heading names it (Appendix B), and the hub has extra rules in 3b.

### 1. Detect the current repo

```bash
gh repo view --json nameWithOwner --jq .nameWithOwner
```

Fail loudly if not in a git repo or the remote isn't GitHub. Also confirm `gh auth status` before going further.

### 2. Check whether the rolling issue already exists

```bash
gh issue list --repo <owner>/<repo> --state open --label next-session --json number,title
```

- **Zero matches** → SEED: create the issue with the full roadmap structure (step 3a)
- **One match** → UPDATE: rewrite the body to reflect the new roadmap state + append a session-end comment (step 3b)
- **Multiple matches** → STOP. Ask the user which is canonical. Never auto-pick.

### 3a. SEED (first run in this repo)

This is **first run** in a repo, so there's no prior roadmap. You're building the initial structure. Two paths:

**3a-i. You have just finished real work in this repo this session.** Capture what shipped and what's next as the seed roadmap.

**3a-ii. You're seeding from a different session.** No work was done on this repo this session — you're just establishing the structure with current repo state as the baseline. The first comment should say so explicitly. (This is the ScriptHammer case from 2026-05-28.)

Either way:

1. Make sure the label exists:
   ```bash
   gh label create next-session --color 0E8A16 --description "Rolling roadmap + priming prompt for new sessions" --repo <owner>/<repo> 2>/dev/null || true
   ```

2. Gather state to populate the initial roadmap:
   - `git log --oneline -20` — recent commits
   - `git branch --show-current` — current branch
   - `gh issue list --repo <owner>/<repo> --state open --json number,title,labels` — open issues
   - `gh pr list --repo <owner>/<repo> --state open --json number,title` — open PRs
   - `gh issue list --repo <owner>/<repo> --state closed --search "closed:>=<today-7d>"` — recently closed
   - Look at `~/.claude/projects/-home-...-<repo>/memory/MEMORY.md` if present — surface the "READ FIRST" pointer
   - Note any time-sensitive items the user mentioned (meetings, deploys, scheduled cron checkpoints)

3. Write the body using the template in **Appendix A** below. Substitute `<owner>/<repo>`, `<ISSUE_NUMBER>` (use `#NNN` placeholder; fix after create), memory path, current branch.

3.5. **Capture the stamp values** and substitute them into the template's trailing `roadmap-stamp` line. This must match the tip the drift hook measures against, so drift reads 0 immediately after this runs — even with unpushed commits:

   ```bash
   BRANCH="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD | sed 's|^origin/||')"
   BRANCH="${BRANCH:-main}"
   TIP="origin/$BRANCH"
   git merge-base --is-ancestor "$TIP" HEAD 2>/dev/null && TIP=HEAD
   TIP_SHA="$(git rev-parse "$TIP")"
   GENERATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```

4. Create:
   ```bash
   gh issue create \
     --repo <owner>/<repo> \
     --title "session-prime: roadmap + orient-fast prompt for new sessions" \
     --label next-session \
     --label documentation \
     --body-file /tmp/session-prime-body.md
   ```

5. Replace the `#NNN` placeholder in the body with the actual issue number returned:
   ```bash
   sed -i "s/#NNN/#<actual>/g" /tmp/session-prime-body.md
   gh issue edit <actual> --repo <owner>/<repo> --body-file /tmp/session-prime-body.md
   ```

6. Append the first comment using the appropriate template from **Appendix B**:
   - **3a-i case** → use the "session work" template; describe what shipped this session
   - **3a-ii case** → use the "seeded baseline" template; explicit that no work happened, this is the starting snapshot

7. **Seed the drift-hook cache** (same as 3b step 6) so the next session start is exact and the new issue is not hidden by the hook's negative cache:

   ```bash
   [ -f ~/.claude/hooks/roadmap-drift.sh ] && bash ~/.claude/hooks/roadmap-drift.sh seed-cache \
     "<owner>/<repo>" "<actual>" "$TIP_SHA" "$BRANCH" "$GENERATED"
   bash ~/.claude/hooks/roadmap-drift.sh check    # expect: drift 0 commits (exact, ...)
   ```

### 3b. UPDATE (subsequent runs in this repo)

The issue already exists. You're doing two things: (1) rewriting the body to reflect what's changed in the roadmap, and (2) appending a comment that audit-trails what changed.

1. Fetch the current body, and note when it was last updated:
   ```bash
   gh issue view <issue-number> --repo <owner>/<repo> --json body --jq .body > /tmp/old-body.md
   gh issue view <issue-number> --repo <owner>/<repo> --json updatedAt --jq .updatedAt   # keep this as READ_AT
   ```
   The other machine may edit the same issue between your read and your write. Step 4 checks for that.

2. Gather state since the last comment was posted:
   - `gh issue view <issue-number> --repo <owner>/<repo> --json comments --jq '.comments[-1].createdAt'` — timestamp of last comment
   - `git log --oneline --since="<that-timestamp>"` — commits since then
   - Recently closed/opened issues in the same window
   - Anything new on the user-stated agenda
   - **Workspace hub only** (`TortoiseWolfe/workspace`): run `~/repos/hub/scripts/tidy-report.sh`. Its findings are about THIS machine's `~/repos` only. If it prints anything, add or replace one backlog line, "**Tidy ~/repos (<MACHINE>)**", summarising each section with its count. Drop only your own machine's line when the report comes back empty. A tidy line without a machine name was written by NX-01.

3. Update the roadmap in the body.

   **Workspace hub on any machine other than NX-01:** NX-01 owns the hub roadmap's priorities. From the tower:
   - Edit only lines about this machine's work (its tower follow-ups, items it finished, its own tidy line), plus new backlog items it found. Write the machine name into anything you add, e.g. "(tower)".
   - Don't re-prioritize the active arc, reorder "Next 3 sessions", or rewrite "Next session should" unless the user explicitly asks. If an item there is now done or stale because of this machine's work, edit just that phrase.
   - Everything else in the body stays exactly as NX-01 wrote it.

   Otherwise (any other repo, or the hub on NX-01):
   - **Active arc** → if the user signaled the arc is done or changed direction, swap it. Else, update its "remaining tasks" list to remove what was completed and add what was discovered.
   - **Next 3 sessions** → re-prioritize. Promote items from backlog if relevant. Demote items the user de-prioritized.
   - **Backlog** → add anything new captured this session. Remove anything that got built or de-scoped.
   - **What's the "next session should"** sentence at the bottom of the roadmap section → rewrite to reflect the new top priority.

3.5. **Refresh the stamp** on the trailing `roadmap-stamp` line — replace the existing one, never append a second. Same values as 3a:

   ```bash
   BRANCH="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD | sed 's|^origin/||')"
   BRANCH="${BRANCH:-main}"
   TIP="origin/$BRANCH"
   git merge-base --is-ancestor "$TIP" HEAD 2>/dev/null && TIP=HEAD
   TIP_SHA="$(git rev-parse "$TIP")"
   GENERATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```

   If the old body had no stamp (pre-existing issue), add one — that is the upgrade from approximate drift to exact, and it needs no migration.

4. Confirm nobody edited the issue since step 1, then write the updated body back:
   ```bash
   gh issue view <issue-number> --repo <owner>/<repo> --json updatedAt --jq .updatedAt   # must equal READ_AT
   gh issue edit <issue-number> --repo <owner>/<repo> --body-file /tmp/new-body.md
   ```
   If `updatedAt` moved, someone (usually the other machine) changed the issue in between. Re-fetch, redo your edits on the new body, and check again. Never write over a newer body.

5. Append a comment using the "session work" template from **Appendix B**. The comment is the audit trail — it explains what changed in the body and why.

6. **Seed the drift-hook cache** so the next session start is exact and needs no network — and so a newly seeded roadmap issue is never hidden behind the hook's 72-hour negative cache. Skip silently if the script is absent; the stamp in the body is the durable artifact, the cache is only an optimisation:

   ```bash
   [ -f ~/.claude/hooks/roadmap-drift.sh ] && bash ~/.claude/hooks/roadmap-drift.sh seed-cache \
     "<owner>/<repo>" "<issue-number>" "$TIP_SHA" "$BRANCH" "$GENERATED"
   ```

   Then confirm it agrees — this single assertion proves the stamp, the cache and the tip-selection rule are consistent:

   ```bash
   bash ~/.claude/hooks/roadmap-drift.sh check     # expect: drift 0 commits (exact, ...)
   ```

### 4. Surface the URL

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

## Prime prompt (copy from here into a fresh Claude session)

\`\`\`
/prep

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

## Appendix B: Comment templates

Two templates depending on the case. **Drop sections that have nothing real to report** — don't include empty `Closed: (none)` lines.

### B-1. Session work template (real work happened)

```markdown
## Session ended YYYY-MM-DD HH:MM <TZ> (<MACHINE>, <repo dir> session <id>)

**Shipped (<N> commits, all on `origin/<branch>`):**
- [`<sha>`](https://github.com/<owner>/<repo>/commit/<sha>) — <one-line summary>
- ...

**Closed:** #N (description), #N (description)

**Opened:** #N (description), #N (description)

**Roadmap changes (what's different in the body vs the previous version):**
- Active arc: <what moved / what's now active>
- Promoted from backlog: <item> → Session +<N>
- Demoted to backlog: <item>
- Pulled forward: <item>
- New backlog items: <item>

**Watch:**
- <time-sensitive item with date — meeting, deploy window, cron checkpoint>

**Next session should:** <one sentence — mirrors the body's "Next session should" so the comment is self-contained>
```

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

- **Not in a git repo / no GitHub remote** — fail loudly, abort.
- **`gh` not authenticated** — print `gh auth status`, abort.
- **`gh` not installed** (a fresh machine) — the GitHub MCP connector can't see the private hub repo, so it's no substitute. Install `gh` into `~/.local/bin` from the cli/cli release, check it against the release's checksums file, then have the user run `! gh auth login`.
- **Machine unknown** (step 0) — ask the user. Never infer it from the hostname.
- **The other machine's comment is the newest one** — fine. Your comment adds this machine's side; don't restate or contradict theirs. Correct a stale item only by editing that phrase in the body.
- **Multiple open `next-session` issues** — ask the user which is canonical. Never auto-pick.
- **Repo has no `~/.claude/projects/.../memory/` dir** — drop that bullet from the prime-prompt block; rely on `git log` + open issues only.
- **No commits since last comment, but the user wants to update the roadmap anyway** — that's fine. The "Shipped" section in the comment becomes brief or omitted; the "Roadmap changes" section captures the actual update.
- **Roadmap re-shuffle without a real session ending** — also fine. Run `/session-prime` and the comment explains what re-prioritized + why.

## Why this isn't a Bash script

This command does enough conditional logic (existence check, multi-branch behavior, body diff vs rewrite, comment template selection) that a markdown-instruction file invoking the right `gh` commands per-step is clearer than a 300-line shell script. The model running this skill handles the branching naturally and can adapt the roadmap shape to the specific repo's needs.
