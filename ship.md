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

5. **Switch to main and merge**:
   - `docker compose exec <service> git checkout main`
   - `docker compose exec <service> git merge <feature-branch> --no-ff -m "Merge branch '<feature-branch>' - <brief description>"`

6. **Delete merged branch**:
   - `docker compose exec <service> git branch -d <feature-branch>`

## Cleanup Phase

7. **Check for stray branches**:
   - Run `git branch` to list remaining local branches
   - For each non-main branch, ask user if it should be deleted
   - Delete confirmed branches through Docker: `docker compose exec <service> git branch -d <branch>` (or -D if not fully merged)

## Final Report

8. **Summary**:
   - Show commit hash and message
   - Confirm merge to main
   - List any remaining branches
   - Note: Changes are local only - run `git push` to push to remote
