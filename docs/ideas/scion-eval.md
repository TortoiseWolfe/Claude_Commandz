# Idea: evaluate Scion as the runner under lane.sh

Raised by Jonathan, 25 Oct 2026 about 12:00 AM, while the hub was planning a skills map:
"graphify, obsidian, scion and playwright should scaffold a lot of the tooling?"

## What Scion is
- GoogleCloudPlatform/scion. Apache 2.0, experimental, and explicitly not an officially supported Google product.
- It runs several coding agents at once: Claude Code, Gemini CLI, Codex and OpenCode, with Codex and OpenCode support partial.
- Each agent gets its own container, git worktree and credentials.
- A "hub" control plane coordinates them, and a "grove" is a project.
- Runtimes: Docker, Podman, Apple containers, Kubernetes and remote VMs.
- Install with `go install github.com/GoogleCloudPlatform/scion/cmd/scion@latest`. Container images are built by the user.
- Sources: devops.com (Google's Scion gives developers a smarter way to run AI agents in parallel), InfoQ (2026/04 google agent testbed scion).

## Why it matters here
lane.sh already does this job with tmux windows and git worktrees in ~/repos/.worktrees. Scion could replace that plumbing.

## What the evaluation has to compare (lane.sh features to keep)
- Recycle at each lane's own context line, with handoff.md, /clear and a re-prime. Lanes never auto-compact.
- lane_watch.sh waking the hub on report.md, idle turns and stuck dialogs.
- Per-engine routing and billing (agy, codex, copilot, opencode, Claude on the subscription or Console), and the refusals that keep free engines off client repos and ~/repos.
- A tmux window Jonathan can type into, which stays open until he closes it.
- The folder-trust prompt and the safety-check behaviour of Claude Code lanes.

## Constraints
- Every Scion container lands on the one shared Docker engine, so it must follow the docker-heavy.sh rules: one heavy job at a time, capped, with a timeout.
- Credentials stay out of agent context (redact_view.py rule). Check how Scion mounts them.

## Output wanted
A written fit report (keep, adopt part, or replace) before any switch. Trial it in a scratch grove, never on a client repo.
