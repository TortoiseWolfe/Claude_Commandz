---
description: Commit changes, merge to main, and clean up feature branches
---

Ship the current feature branch: commit, merge to main, and cleanup.

## Docker-First Requirement

**CRITICAL**: If the project has a `docker-compose.yml` or `CLAUDE.md` mentioning Docker-first development:
- ALL commands (git, pnpm, npm) MUST run through Docker
- Use `docker compose exec <service> <command>` for everything
- Check if container is running first; if not, run `docker compose up -d`

## Pre-flight Checks

1. **Verify branch state**:
   - Run `git status` and `git branch` (through Docker if Docker-first project)
   - Note the current branch name and whether there are uncommitted changes

2. **Run quality checks** (if project has them):
   - Look for lint/type-check commands in package.json
   - Run through Docker: `docker compose exec <service> pnpm run lint && docker compose exec <service> pnpm run type-check`
   - Abort if they fail

## Commit Phase

3. **Stage changes**:
   - Run `git status` to see all uncommitted changes (staged, unstaged, untracked)
   - If there are changes, show the user the full list and ask which to include
   - Default assumption: everything gets committed unless the user says otherwise
   - Stage confirmed files with `git add` (through Docker if Docker-first)
   - Run `git diff --cached --stat` to summarize staged changes
   - If no changes to commit, skip to Merge Phase (or Final Report if already on main)

4. **Create commit**:
   - Analyze staged changes to generate descriptive commit message
   - Use conventional commits format (feat/fix/docs/refactor/test/chore)
   - Run commit through Docker: `docker compose exec <service> git commit -m "..."`
   - Include standard footer:
     ```
     🤖 Generated with [Claude Code](https://claude.com/claude-code)

     Co-Authored-By: Claude <noreply@anthropic.com>
     ```

## Merge Phase (skip if already on main)

5. **Pick the right merge path — check, don't assume.** A repo with PRs in its
   history should not get a local merge; that bypasses CI and review.
   ```bash
   git log --oneline -10 main | grep -c "Merge pull request"   # >0 => PR workflow
   gh api repos/{owner}/{repo} --jq '{delete_branch_on_merge, allow_squash_merge}'
   ```

   **PR workflow** (preferred where the history shows one):
   ```bash
   git push -u origin <feature-branch>
   gh pr create --fill
   gh pr checks --watch          # do not merge on red
   gh pr merge <n> --merge       # or --squash, matching the repo's convention
   ```

   **Local merge** (only when the repo has no PR history):
   ```bash
   git checkout main
   git merge <feature-branch> --no-ff -m "Merge branch '<feature-branch>' - <description>"
   ```

6. **Delete the shipped branch — and expect `-d` to refuse.**
   ```bash
   git checkout main && git pull --ff-only
   git branch -d <feature-branch> || git branch -D <feature-branch>
   ```

   `git branch -d` only succeeds when the branch is an **ancestor** of main. After
   a **squash merge** it never is: squashing writes a new commit with a new SHA, so
   git cannot tell the work landed and refuses the delete. That refusal is not a
   warning that work would be lost — it is a false negative, and `-D` is correct.

   Before forcing, confirm the work really landed rather than trusting the PR state:
   ```bash
   gh pr list --head <feature-branch> --state all --json number,state
   git diff --name-only main...<feature-branch> | head   # spot-check those paths exist on main
   ```

## Cleanup Phase

7. **Prune stale remote-tracking refs**:
   ```bash
   git fetch --prune
   ```
   `fetch.prune = true` is set globally, so this happens on every fetch. It removes
   `origin/*` refs for branches GitHub deleted on merge — but it **never touches
   local branches**. Nothing in git does. That asymmetry is why they pile up.

8. **Audit stray local branches — classify before asking.** Sort them into three
   buckets rather than listing 20 branches and asking one by one:
   ```bash
   for b in $(git branch --format='%(refname:short)' | grep -v '^main$'); do
     if git merge-base --is-ancestor "$b" main; then
       echo "MERGED   $b"
     else
       pr=$(gh pr list --head "$b" --state all --json number,state \
              --jq 'if length>0 then "#\(.[0].number) \(.[0].state)" else empty end' 2>/dev/null)
       if [ -n "$pr" ]; then echo "SQUASHED $b ($pr)"; else echo "LIVE     $b"; fi
     fi
   done
   ```

   The `if length>0 … else empty end` guard is load-bearing. The obvious form,
   `--jq '.[0]|"#\(.number) \(.state)"'`, emits the literal string `#null null`
   when no PR exists — which is non-empty, so a `[ -n "$pr" ]` test passes and
   **live branches get classified as SQUASHED, i.e. safe to force-delete.**
   Verified both directions: a branch with no PR must report LIVE, and a real
   squash-merged branch must still report its PR number.
   - **MERGED** — safe, `git branch -d`
   - **SQUASHED** — PR merged but not an ancestor; safe, needs `git branch -D`
   - **LIVE** — no PR and not on main. **Keep these.** Report them; do not offer
     to delete without the user raising it.

   Record the SHAs before deleting so recovery doesn't depend on reflog:
   ```bash
   git branch --format='%(refname:short) %(objectname:short)' > /tmp/deleted-branches.txt
   ```

   Observed in practice: a repo with 25 local branches had 16 MERGED, 6 SQUASHED,
   and only 2 LIVE. Treating all 22 as "stray branches to ask about one at a time"
   would be 22 questions to reach two real decisions.

## Final Report

9. **Summary**:
   - Commit hash and message; PR number and merge commit if a PR was used
   - Which branches were deleted, split by `-d` vs `-D`, and which were kept as LIVE
   - Sync state per repo: `git rev-list --left-right --count HEAD...@{u}` should be `0 0`
   - **Say plainly whether anything was pushed.** A local merge leaves the remote
     untouched; a PR merge already published. Do not report "shipped" ambiguously.

## Before staging: two traps worth checking

- **`git add -A` in a repo with pre-existing untracked files** sweeps them into
  your commit. Run `git status` first and read the untracked list — if files were
  there before this task, they are not yours to commit. Stage explicit paths instead.
- **Generated artifacts.** Anything a hook or build regenerates (graphs, caches,
  reports) should be gitignored, not committed. Committing a file that is rewritten
  on every commit grows the repo permanently, and once pushed it cannot be removed
  without rewriting public history.
