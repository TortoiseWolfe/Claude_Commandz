---
description: Triage the current repo's un-deferred open issues, present a ranked plan, then work the one you pick to a PR
---

# /goal — work through the backlog

Drive the current repository's backlog of open GitHub issues. Default mode: **triage → confirm → work one** (do NOT auto-start coding; bring the ranked plan and let the user pick, then take that one issue all the way to a PR).

Optional argument `$ARGUMENTS`:
- empty → full triage of un-deferred issues, then ask which to start.
- an issue number (e.g. `109`) → skip triage, go straight to working that issue.
- `triage` → triage-only; produce the ranked report and stop.

## Step 0 — Orient (always)

1. Confirm the repo: `git rev-parse --show-toplevel` and `gh repo view --json nameWithOwner --jq .nameWithOwner`. The shell may reset CWD between commands — always `cd` into the repo root explicitly in each command.
2. **Branch hygiene gate (binding):** `gh pr list --state open` and `git status -s`. If there is an open PR or a dirty/un-merged feature branch, STOP and surface it — never start a new issue while another PR is in flight (see CLAUDE.md "Branch hygiene — NON-NEGOTIABLE"). Resolve/merge that first.
3. Read the repo's roadmap issue if one exists (look for an issue labeled `next-session` / titled `SESSION-PRIME`). Its body is the priority signal; its latest comment is the audit trail.
4. Read binding user prefs: `~/.claude/projects/.../memory/MEMORY.md` — cleaner-long-term-solution-over-hack and root-cause rules are NON-NEGOTIABLE.

## Step 1 — Triage (unless an issue number was given)

1. List open issues with labels:
   `gh issue list --state open --limit 60 --json number,title,labels --jq '.[] | "#\(.number)\t[\([.labels[].name]|join(","))]\t\(.title)"'`
2. **Scope to un-deferred work**: exclude issues tagged `template-v1-out-of-scope` (deferred backlog) and meta/noise issues (the roadmap issue itself, "FYI"-type stubs). If the user asked for a wider scope, honor that.
3. For each candidate, **reconcile against actual repo state** — do not trust the title. Read the issue body + comments (`gh issue view N --json title,body,comments`), then Grep/Glob/Read/`git log` to check: is it already shipped? partially done? blocked on a decision or external dep? For a substantial set (5+), fan this out with the Workflow tool (one Explore agent per issue, structured output) — this is the pattern that works well here.
4. Classify each: **readiness** (ready-now / needs-decision / blocked / likely-done), **effort**, **dependencies**, **blockers**, **alreadyDone**, **recommendation** (do-next / do-soon / defer / close-as-done / needs-user-input), with **evidence** citing real paths/commits.

## Step 2 — Present & confirm (default mode)

- Show a compact ranked table (do-next first), with one-line rationale + readiness + effort each.
- Flag any **close-as-done** issues (already shipped) — recommend closing them with a comment citing the commit/PR.
- Then **stop and ask which to start** (use AskUserQuestion). Do not begin coding until the user picks.
- If invoked as `triage`, stop here.

## Step 3 — Work the chosen issue

Once the user picks (or an issue number was passed):

1. **Branch off `main`** (never commit to main): `git checkout main && git pull && git checkout -b <type>/<short-name>-<issue#>`.
2. Implement the **root-cause** fix — no shortcuts/hacks/bypasses (binding user pref). Follow project conventions (5-file component pattern, Docker-first commands, monolithic Supabase migration, etc. per CLAUDE.md).
3. **Run the gates** before claiming done (Docker-first): `type-check`, `lint`, `test`, and any issue-specific verification (e.g. the actual E2E spec for a test fix). Show real output — evidence before assertions.
4. **Commit from inside the container** with the GIT_* identity if the project requires it (check CLAUDE.md); **never** `--no-verify`. Commit-only if operating as a terminal; otherwise open a PR with `gh pr create` linking the issue (`Closes #N`).
5. Report what landed, with the gate output. Then offer the next item from the triage.

## Notes

- One issue → one PR. Don't batch unrelated issues.
- If the issue turns out already-done during Step 3, stop and recommend closing it instead.
- Keep the roadmap issue honest: when a substantive issue lands, suggest `/session-prime` to update it.
