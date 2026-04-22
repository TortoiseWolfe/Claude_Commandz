---
description: Show next task for current terminal role
---

Show the next actionable task for your terminal role.

## Arguments

```text
$ARGUMENTS
```

- `/next` - Auto-detect role from your terminal primer
- `/next [role]` - Override with specific role

## Role Detection

You were primed at session start (e.g., "You are the Validator terminal").
Use that context to determine your role automatically.

**Valid roles**: manager, assistant-manager, planner, generator-1, generator-2, generator-3, viewer, reviewer, validator, inspector, author, tester, implementer, auditor

If you cannot determine your role from context, ask via `AskUserQuestion`.

## Instructions

### 1. Determine Role

Check `$ARGUMENTS` first. If empty, infer from your session context.

### 2. Run Terminal Router

```bash
python3 docs/design/wireframes/terminal-router.py [role]
```

This Python script:
- Reads `.terminal-status.json` queue
- Runs role-specific checks (escalation, inspection, etc.)
- Returns formatted status + next action
- Suggests auto-execute commands

### 3. Display Output

Show the terminal-router.py output directly. It includes:
- Terminal name and status
- Queue count
- Next action recommendation
- Auto-execute suggestion
- Relevant files

### 4. Auto-Execute (if suggested)

If terminal-router.py suggests an auto-execute command AND terminal status is "idle":

| Auto-Execute Type | Action |
|-------------------|--------|
| `/wireframe-prep NNN` | Invoke Skill: wireframe-prep with args |
| `/wireframe-plan NNN` | Invoke Skill: wireframe-plan with args |
| `/wireframe-screenshots --feature NNN` | Invoke Skill: wireframe-screenshots with args |
| `/hot-reload-viewer` | Invoke Skill: hot-reload-viewer |
| `/session-summary` | Invoke Skill: session-summary |
| `/test` | Invoke Skill: test |
| `/speckit.implement` | Invoke Skill: speckit.implement |
| `/speckit.analyze` | Invoke Skill: speckit.analyze |
| `queue_manager.py ...` | Run Python command directly |
| `validate-wireframe.py ...` | Run Python command directly |
| `inspect-wireframes.py ...` | Run Python command directly |

**Skip auto-execute if:**
- Terminal status is NOT "idle"
- Already working on a task
- Output says "Standing by"

## Example

```bash
$ python3 docs/design/wireframes/terminal-router.py validator

VALIDATOR Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: has_work
Queue:  2 items assigned to you

NEXT ACTION
→ 2 escalation candidates found
→ Auto-execute: validate-wireframe.py --check-escalation

Files: validate-wireframe.py, GENERAL_ISSUES.md
```

Then run the suggested command and handle the output.

## DO NOT

- Implement role dispatch logic manually (use terminal-router.py)
- Create verbose output - the Python script handles formatting
- Suggest tasks outside your role's focus
