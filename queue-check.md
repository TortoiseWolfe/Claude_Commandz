---
description: Show pending tasks and terminal status from the queue
---

# Queue Check

Display current queue status, pending tasks, and terminal availability.

## Usage

```
/queue-check                    # Show full queue and all terminal statuses
/queue-check [terminal]         # Show tasks assigned to specific terminal
/queue-check --pending          # Show only pending queue items
/queue-check --active           # Show only active (non-idle) terminals
```

**Examples**:
```
/queue-check                    # Full status dashboard
/queue-check reviewer           # Tasks assigned to reviewer
/queue-check generator-1        # Tasks for generator-1
/queue-check --pending          # Just the queue
```

## Arguments

ARGUMENTS: $ARGUMENTS

- No args: Full dashboard (terminals + queue)
- `terminal`: Filter to specific terminal's tasks
- `--pending`: Show only queue items
- `--active`: Show only non-idle terminals

---

## Instructions

### 1. Read Status File

```bash
cat docs/design/wireframes/.terminal-status.json
```

Parse the JSON to extract:
- `terminals`: Object with status/feature/task per terminal
- `queue`: Array of pending tasks
- `lastUpdated`: Timestamp

### 2. Process Arguments

**If no arguments**: Show full dashboard (go to Step 3)
**If `--pending`**: Show only queue (go to Step 4)
**If `--active`**: Show only active terminals (go to Step 5)
**If terminal name**: Filter queue by assignedTo (go to Step 6)

---

### 3. Full Dashboard (No Args)

Display terminal status grid and queue:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Terminal Status                              Updated: 2026-01-14T12:00:00Z  │
├─────────────────────────────────────────────────────────────────────────────┤
│ COUNCIL                                                                     │
│   cto: idle          architect: idle       security-lead: idle              │
│   toolsmith: idle    devops: idle          product-owner: idle              │
│                                                                             │
│ GENERATORS                                                                  │
│   generator-1: idle  generator-2: idle     generator-3: idle                │
│                                                                             │
│ PIPELINE                                                                    │
│   planner: idle      viewer: idle          reviewer: idle                   │
│   validator: idle    inspector: idle                                        │
│                                                                             │
│ SUPPORT                                                                     │
│   author: idle       tester: idle          implementer: idle                │
│   auditor: idle      coordinator: idle                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ Queue (4 items)                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. 001-wcag-aa-compliance → reviewer (REVIEW)                               │
│    Generator-1 completed 3 SVGs - ready for review                          │
│                                                                             │
│ 2. 002-cookie-consent → reviewer (REVIEW)                                   │
│    Generator-3 completed 3 SVGs - ready for review                          │
│                                                                             │
│ 3. 005-security-hardening → reviewer (REVIEW)                               │
│    Generator-1 completed 3 SVGs - ready for reviewer                        │
│                                                                             │
│ 4. 007-e2e-testing-framework → reviewer (REVIEW)                            │
│    Generator-3 completed 2 SVGs - ready for review                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

If queue is empty:
```
│ Queue: Empty - no pending tasks                                             │
```

---

### 4. Pending Only (--pending)

Show only the queue items:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Pending Queue (4 items)                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. 001-wcag-aa-compliance → reviewer (REVIEW)                               │
│ 2. 002-cookie-consent → reviewer (REVIEW)                                   │
│ 3. 005-security-hardening → reviewer (REVIEW)                               │
│ 4. 007-e2e-testing-framework → reviewer (REVIEW)                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 5. Active Only (--active)

Show only terminals with non-idle status:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Active Terminals                                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ generator-1: generating  │ Feature: 003-user-auth  │ Task: SVG 2/4          │
│ reviewer: review         │ Feature: 001-wcag       │ Task: Screenshot       │
└─────────────────────────────────────────────────────────────────────────────┘
```

If all idle:
```
│ All terminals are idle                                                      │
```

---

### 6. Filter by Terminal

Show only queue items assigned to the specified terminal:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Tasks for: reviewer                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ Terminal Status: idle                                                       │
│                                                                             │
│ Assigned Tasks (4):                                                         │
│                                                                             │
│ 1. 001-wcag-aa-compliance (REVIEW)                                          │
│    Generator-1 completed 3 SVGs - ready for review                          │
│                                                                             │
│ 2. 002-cookie-consent (REVIEW)                                              │
│    Generator-3 completed 3 SVGs - ready for review                          │
│                                                                             │
│ 3. 005-security-hardening (REVIEW)                                          │
│    Generator-1 completed 3 SVGs - ready for reviewer                        │
│                                                                             │
│ 4. 007-e2e-testing-framework (REVIEW)                                       │
│    Generator-3 completed 2 SVGs - ready for review                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ To start: Pick a feature and run /wireframe-screenshots                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

If no tasks for terminal:
```
│ No tasks assigned to [terminal]                                             │
```

---

## Error Handling

| Scenario | Message |
|----------|---------|
| Status file not found | "Status file not found at docs/design/wireframes/.terminal-status.json" |
| Invalid terminal name | "Unknown terminal: [name]. Valid: cto, architect, generator-1, etc." |
| JSON parse error | "Error parsing status file. Check JSON syntax." |

---

## Valid Terminal Names

**Council**: cto, architect, security-lead, toolsmith, devops, product-owner

**Generators**: generator-1, generator-2, generator-3

**Pipeline**: planner, viewer, reviewer, validator, inspector

**Support**: author, tester, implementer, auditor, coordinator

---

## Related Skills

- `/dispatch [terminal] [task]` - Assign a task to a terminal
- `/wireframe-status` - Update queue item status
- `/status` - Project-wide health check
