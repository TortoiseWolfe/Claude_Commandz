---
description: Send tasks to terminals by updating the queue
---

# Dispatch

Assign tasks to terminals by adding items to the queue in `.terminal-status.json`.

**Access**: Coordinator, CTO, Architect (task assignment authority)

## Usage

```
/dispatch [terminal] [feature] [action] "[reason]"
```

**Examples**:
```
/dispatch generator-1 003-user-auth GENERATE "Plan complete - 4 SVGs assigned"
/dispatch reviewer 001-wcag REVIEW "Generator-1 completed 3 SVGs"
/dispatch inspector 005-security INSPECT "Review complete - check consistency"
/dispatch planner 008-blog PLAN "Next feature in IMPLEMENTATION_ORDER"
```

## Arguments

ARGUMENTS: $ARGUMENTS

- `terminal`: Target terminal (generator-1, reviewer, inspector, etc.)
- `feature`: Feature folder name (e.g., 001-wcag-aa-compliance)
- `action`: Task type (PLAN, GENERATE, REVIEW, INSPECT, IMPLEMENT)
- `reason`: Description of why this task is dispatched (in quotes)

---

## Instructions

### 1. Verify Authority

Only these terminals can dispatch tasks:
- Coordinator (primary queue manager)
- CTO (strategic direction)
- Architect (technical assignments)

If not authorized:
```
┌─────────────────────────────────────────────────┐
│ ERROR: Not Authorized                           │
├─────────────────────────────────────────────────┤
│ /dispatch requires Coordinator, CTO, or         │
│ Architect authority.                            │
│                                                 │
│ Use /memo to request task assignment.           │
└─────────────────────────────────────────────────┘
```

### 2. Parse Arguments

Extract from ARGUMENTS:
- `terminal`: First arg
- `feature`: Second arg
- `action`: Third arg (uppercase: PLAN, GENERATE, REVIEW, INSPECT, IMPLEMENT)
- `reason`: Fourth arg (quoted string)

Validate:
- All 4 arguments required
- Terminal must be valid name
- Action must be valid type

### 3. Validate Terminal

Valid terminal names:

| Category | Terminals |
|----------|-----------|
| Council | cto, architect, security-lead, toolsmith, devops, product-owner |
| Generators | generator-1, generator-2, generator-3 |
| Pipeline | planner, viewer, reviewer, validator, inspector |
| Support | author, tester, implementer, auditor, coordinator |

If invalid:
```
┌─────────────────────────────────────────────────┐
│ ERROR: Unknown Terminal                         │
├─────────────────────────────────────────────────┤
│ "[name]" is not a valid terminal.               │
│                                                 │
│ Valid: generator-1, reviewer, inspector, etc.   │
└─────────────────────────────────────────────────┘
```

### 4. Validate Action

Valid actions:

| Action | Description | Typical Assignees |
|--------|-------------|-------------------|
| PLAN | Analyze spec, create wireframe assignments | planner |
| GENERATE | Create SVG wireframes | generator-1/2/3 |
| REVIEW | Screenshot and document issues | reviewer |
| INSPECT | Cross-SVG consistency check | inspector |
| IMPLEMENT | Convert spec/wireframe to code | implementer |
| AUDIT | Cross-artifact consistency check | auditor |
| TEST | Run test suites | tester |
| DOCUMENT | Write documentation | author, tech-writer |

If invalid:
```
┌─────────────────────────────────────────────────┐
│ ERROR: Invalid Action                           │
├─────────────────────────────────────────────────┤
│ "[action]" is not a valid task action.          │
│                                                 │
│ Valid: PLAN, GENERATE, REVIEW, INSPECT,         │
│        IMPLEMENT, AUDIT, TEST, DOCUMENT         │
└─────────────────────────────────────────────────┘
```

### 5. Read Status File

```bash
cat docs/design/wireframes/.terminal-status.json
```

Parse the JSON structure.

### 6. Add Queue Item

Create new queue entry:

```json
{
  "feature": "[feature]",
  "svg": null,
  "action": "[ACTION]",
  "reason": "[reason]",
  "assignedTo": "[terminal]"
}
```

Add to the `queue` array.

### 7. Update Timestamp

Set `lastUpdated` to current ISO timestamp:
```json
"lastUpdated": "2026-01-14T15:30:00Z"
```

### 8. Write Status File

Save the updated JSON back to the file.

### 9. Output Confirmation

```
┌─────────────────────────────────────────────────┐
│ Task Dispatched                                 │
├─────────────────────────────────────────────────┤
│ To: [terminal]                                  │
│ Feature: [feature]                              │
│ Action: [ACTION]                                │
│ Reason: [reason]                                │
│                                                 │
│ Queue position: #[N]                            │
│                                                 │
│ Terminal can run: /queue-check [terminal]       │
└─────────────────────────────────────────────────┘
```

---

## Batch Dispatch

For multiple assignments, run `/dispatch` multiple times:

```
/dispatch generator-1 003-user-auth GENERATE "Batch 1"
/dispatch generator-2 004-dashboard GENERATE "Batch 2"
/dispatch generator-3 005-security GENERATE "Batch 3"
```

---

## Queue Management

### Remove from Queue

To remove a completed or cancelled task, edit `.terminal-status.json` directly and remove the queue item.

### Reorder Queue

Edit `.terminal-status.json` to reorder the `queue` array.

### Clear Queue

To clear all items:
```json
"queue": []
```

---

## Error Handling

| Scenario | Message |
|----------|---------|
| Missing arguments | "Usage: /dispatch [terminal] [feature] [action] \"[reason]\"" |
| Status file not found | "Status file not found" |
| JSON parse error | "Error parsing status file" |
| Write failed | "Failed to update status file" |

---

## Related Skills

- `/queue-check` - View current queue and terminal status
- `/wireframe-status` - Update item status (complete/failed)
- `/status` - Project-wide health check
