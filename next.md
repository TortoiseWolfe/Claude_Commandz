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

**Note**: Generators are numbered (generator-1, generator-2, generator-3). Detect from primer "You are the Generator-N terminal".

If you cannot determine your role from context, ask via `AskUserQuestion`.

## Instructions

### 1. Determine Role

Check `$ARGUMENTS` first. If empty, infer from your session context.

### 2. Read Terminal Status

```bash
cat docs/design/wireframes/.terminal-status.json
```

Extract:
- Your terminal's status: `.terminals.[role]`
- Queue items assigned to you: `.queue[] | select(.assignedTo == "[role]")`
- Queue length for your role

### 3. Role-Specific Checks

Run additional checks based on role:

| Role | Additional Check |
|------|------------------|
| validator | `python3 docs/design/wireframes/validate-wireframe.py --check-escalation` |
| inspector | `python3 docs/design/wireframes/inspect-wireframes.py --all` |
| generator-N | First queue item assigned to THIS generator number |
| reviewer | `ls -t docs/design/wireframes/*/*.issues.md 2>/dev/null \| head -3` |
| planner | Check for assignments in queue |
| manager | Full queue summary + check for idle generators (1, 2, 3) |
| assistant-manager | Check idle roles + skill file ages |
| viewer | Check if viewer running (port 3000) |

### 4. Determine Next Action

| Role | When Idle | When Queue Has Items |
|------|-----------|---------------------|
| generator-N | "Self-assign unassigned work (see below)" | "Auto-execute `/wireframe-prep [feature]`" |
| reviewer | "Run `/wireframe-screenshots --all` to find work" | "Auto-execute `/wireframe-screenshots --feature [NNN]`" |
| planner | "Self-assign next unplanned feature (see below)" | "Auto-execute `/wireframe-plan [feature]`" |
| validator | "Run `--check-escalation`. [N] candidates." | "Escalate [issue] to GENERAL_ISSUES.md, add _check_*() method" |
| inspector | "Run `inspect-wireframes.py --all` for pattern violations" | "Check cross-SVG consistency, log PATTERN_VIOLATION issues" |
| viewer | "Auto-execute `/hot-reload-viewer`" | "Viewer should be running at localhost:3000" |
| manager | "All roles have work. Standing by." | "Auto-assign work to idle downstream roles" |
| assistant-manager | "Check skill files need updates" | "Assist Manager with assignments or check skills" |
| author | "Auto-execute `/session-summary`" | "Write [topic]" |
| tester | "No tests to run (no src/ yet)" | "Auto-execute `/test`" |
| implementer | "No implementation tasks" | "Auto-execute `/speckit.implement [feature]`" |
| auditor | "Auto-execute `/speckit.analyze`" | "Audit [feature]" |

### 5. Output Format

```
[ROLE] Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: [idle | working on feature]
Queue:  [N] items assigned to you

NEXT ACTION
→ [Specific instruction]
→ Auto-executing: [skill]

Files: [relevant files]
```

### 6. Auto-Execute (if work available)

**Skip auto-execute if:**
- Terminal status is NOT "idle"
- Already working on a task

After displaying status, automatically invoke the appropriate action:

| Role | Condition | Auto-Execute |
|------|-----------|--------------|
| manager | Idle generators (1, 2, or 3) | Auto-assign work (see Manager Logic below) |
| assistant-manager | Idle roles OR skill updates needed | Help assign work or check `~/.claude/commands/` for outdated skills |
| planner | Queue has items for planner | `Skill: wireframe-plan` with feature arg |
| generator-N | Queue has items for THIS generator | `Skill: wireframe-prep` then `Skill: wireframe` with feature |
| reviewer | Queue has items | `Skill: wireframe-screenshots` with `--feature [NNN]` |
| validator | Escalation candidates > 0 | Run `python3 validate-wireframe.py --check-escalation`, update GENERAL_ISSUES.md |
| inspector | Reviewed SVGs pending inspection | Run `python3 inspect-wireframes.py --feature [NNN]`, log PATTERN_VIOLATION to *.issues.md |
| viewer | Not running | `Skill: hot-reload-viewer` |
| author | Idle | `Skill: session-summary` |
| tester | Tests exist | `Skill: test` |
| implementer | Tasks queued | `Skill: speckit.implement` |
| auditor | Features to audit | `Skill: speckit.analyze` |

### Manager Auto-Assign Logic

When Manager runs `/next` and downstream roles are idle:

1. **Read state**: `.terminal-status.json` (queue + terminal statuses)
2. **Check idle generators needing work** (generator-1, generator-2, generator-3):
   - Generator-N idle + no items assigned to generator-N → Assign by feature group
   - **Feature-grouped assignment**: All SVGs for one feature go to same generator
3. **Check other idle roles**:
   - Planner idle + no planner assignments → Assign next feature from `features/IMPLEMENTATION_ORDER.md`
   - Reviewer idle + completed SVGs not reviewed → Assign to reviewer
4. **Update `.terminal-status.json`** with new assignment:
   ```json
   {"feature": "003-user-authentication", "assignedTo": "generator-2", "action": "REGEN"}
   ```
5. **Output**: "Assigned [feature] to [generator-N]. Run `/next` in that terminal."

### Assistant Manager Logic

When Assistant Manager runs `/next`:
- If idle downstream roles exist → Help Manager by assigning work
- If no assignments needed → Check if skills in `~/.claude/commands/` are outdated
- Can pick up Manager duties when Manager is busy

**Generator workflow:** After `/wireframe-prep` loads context, auto-execute `/wireframe` to generate SVGs.

**Generator self-assignment:** If no items assigned to this generator but unassigned queue items exist:

**GUARD CHECK (prevent race conditions):**
First, check if other generators are currently working:
```bash
cat docs/design/wireframes/.terminal-status.json | jq '.terminals | to_entries[] | select(.key | startswith("generator")) | select(.value.status == "working")'
```

If **any** generator has `status: "working"`:
- **DO NOT self-assign** - wait for Manager to distribute work
- Output: "Unassigned work exists but generator-N is actively working. Waiting for Manager to assign. Run `/next` in Manager terminal."

If **all** generators are idle and unassigned work exists:
1. Self-assign the **first unassigned feature** (all its SVGs) to this generator
2. Update `.terminal-status.json`:
   - Set `assignedTo: "generator-N"` for all queue items of that feature
   - Set `terminals.generator-N.status: "working"`
   - Set `terminals.generator-N.feature: "[feature]"`
3. **Validate JSON before writing:**
   ```bash
   python3 -c "import json; json.load(open('docs/design/wireframes/.terminal-status.json'))"
   ```
   If validation fails, fix the JSON structure before proceeding.
4. Auto-execute `/wireframe-prep` then `/wireframe`

**Priority rule:** Manager-assigned work ALWAYS takes precedence over self-assignment. If Manager has already assigned a feature to a different generator, respect that assignment.

If no unassigned work exists either, output: "No work available. Standing by."

### Planner Self-Assignment Logic

When Planner runs `/next` and no items assigned to planner in queue:

**GUARD CHECK:**
```bash
cat docs/design/wireframes/.terminal-status.json | jq '.queue[] | select(.assignedTo == "planner")'
```

If items exist → Follow queue, don't self-assign.

If NO planner items:

1. **Read IMPLEMENTATION_ORDER.md** - Parse Tier 1-9 ordered feature list
2. **Check each feature in order** for existing `assignments.json`:
   ```bash
   ls docs/design/wireframes/NNN-feature/assignments.json 2>/dev/null
   ```
3. **Find first unplanned** - Feature without assignments.json
4. **Self-assign**:
   - Add to queue: `{"feature": "NNN-feature", "action": "PLAN", "assignedTo": "planner", "reason": "Self-assigned from IMPLEMENTATION_ORDER.md"}`
   - Update: `terminals.planner.status: "active"`, `terminals.planner.feature: "NNN-feature"`
   - Log: `"Planner: Self-assigned NNN-feature from IMPLEMENTATION_ORDER.md"`
5. **Validate JSON** then **auto-execute** `/wireframe-plan NNN`

If all features planned → "All features have wireframe plans. Standing by for implementation phase."

**Output format:**
```
PLANNER Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: idle → active
Queue:  0 items → 1 item (self-assigned)

SELF-ASSIGNING:
→ Next unplanned: 002-cookie-consent
→ Based on: IMPLEMENTATION_ORDER.md (Tier 1, position 5)
→ Auto-executing: /wireframe-plan 002
```

## Examples

### Validator (idle, no escalations)

```
VALIDATOR Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: idle
Queue:  0 items

NEXT ACTION
→ No escalation candidates found
→ Standing by for Generator output

Files: validate-wireframe.py, GENERAL_ISSUES.md
```

### Generator-1 (has queue items)

```
GENERATOR-1 Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: idle
Queue:  2 items assigned to generator-1

NEXT ACTION
→ Regenerate 000-landing-page/01-landing-page.svg
→ Auto-executing: /wireframe-prep 000 → /wireframe 000

Reason: v3 wireframe - wrong viewports, no annotation panel, 26 errors
```

[/wireframe-prep executes, then /wireframe auto-executes]

### Generator-2 (different feature)

```
GENERATOR-2 Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: idle
Queue:  3 items assigned to generator-2

NEXT ACTION
→ Generate 003-user-authentication/01-registration-sign-in.svg
→ Auto-executing: /wireframe-prep 003 → /wireframe 003

Reason: SVG missing. Sign Up/Sign In forms + OAuth buttons
```

[/wireframe-prep executes, then /wireframe auto-executes]

### Generator-3 (self-assigns unassigned work)

```
GENERATOR-3 Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: idle
Queue:  0 items assigned to generator-3

SELF-ASSIGNING:
→ Found unassigned: 006-template-fork-experience (2 SVGs)
→ Assigning to generator-3
→ Auto-executing: /wireframe-prep 006 → /wireframe 006
```

[Updates .terminal-status.json, then auto-executes wireframe workflow]

### Manager (distributing work to generators)

```
MANAGER Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: idle
Queue:  8 items total

GENERATOR STATUS:
→ generator-1: idle, 2 items (000, 001)
→ generator-2: idle, 3 items (003 x3)
→ generator-3: idle, 3 items (005 x2, 006)

All generators have work assigned.
Run /next in each Generator terminal to begin.

Files: .terminal-status.json, CLAUDE.md
```

### Manager (idle generator needs work)

```
MANAGER Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: idle
Queue:  5 items (3 unassigned)

GENERATOR STATUS:
→ generator-1: working on 000-landing-page
→ generator-2: idle, 0 items ← needs work
→ generator-3: working on 005-security-hardening

AUTO-ASSIGNING:
→ Feature: 003-user-authentication (3 SVGs)
→ Assigning to: generator-2 (feature-grouped)

Files: .terminal-status.json, IMPLEMENTATION_ORDER.md
```

[Updates .terminal-status.json with assignments]

```
Assigned 003-user-authentication (3 SVGs) to generator-2.
Run /next in Generator-2 terminal to begin.
```

### Reviewer (has queue items)

```
REVIEWER Terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Status: idle
Queue:  1 item

NEXT ACTION
→ Review 002-cookie-consent wireframes
→ Auto-executing: /wireframe-screenshots --feature 002

Files: 002-cookie-consent/*.issues.md
```

[/wireframe-screenshots executes, generates PNGs for review]

## DO NOT

- Create verbose output - keep it scannable
- Show queue items not assigned to your role
- Suggest tasks outside your role's focus
