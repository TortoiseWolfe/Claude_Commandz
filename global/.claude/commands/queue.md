---
description: Task queue management - add, remove, list items in .terminal-status.json
---

# Queue Management

Manage the task queue in `.terminal-status.json` with add, remove, and list operations.

**Access**: Coordinator, CTO, Architect (queue management authority)

## Usage

```
/queue                              # List all queue items
/queue list                         # Same as above
/queue list [terminal]              # Filter by assigned terminal
/queue add [terminal] [feature] [action] "[reason]"
/queue remove [index]               # Remove by position (1-based)
/queue clear                        # Clear entire queue (with confirmation)
```

**Examples**:
```
/queue                              # Show current queue
/queue list WireframeQA             # Show items assigned to WireframeQA
/queue add WireframeGenerator1 008-blog GENERATE "Planner completed assignment"
/queue remove 3                     # Remove third item
/queue clear                        # Clear all (asks for confirmation)
```

## Arguments

ARGUMENTS: $ARGUMENTS

- No args or `list`: Show all queue items
- `list [terminal]`: Filter by assignee
- `add [terminal] [feature] [action] "[reason]"`: Add new item
- `remove [index]`: Remove item by 1-based index
- `clear`: Remove all items (with confirmation)

---

## Instructions

### 1. Verify Authority

Only these terminals can modify the queue:
- Coordinator (primary queue manager)
- CTO (strategic direction)
- Architect (technical assignments)

Read operations (list) are allowed for all terminals.

If not authorized for write operations:
```
┌─────────────────────────────────────────────────┐
│ ERROR: Not Authorized                           │
├─────────────────────────────────────────────────┤
│ Queue modifications require Coordinator, CTO,   │
│ or Architect authority.                         │
│                                                 │
│ Use /queue list to view the queue.              │
└─────────────────────────────────────────────────┘
```

### 2. Read Status File

```bash
cat docs/design/wireframes/.terminal-status.json
```

Parse JSON to get:
- `queue`: Array of task items
- `lastUpdated`: Timestamp
- `completedToday`: Array of completion logs

---

### 3. List Operations

#### /queue or /queue list

Display all queue items:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TASK QUEUE                                            4 items               │
├─────────────────────────────────────────────────────────────────────────────┤
│ # │ Feature                    │ Action   │ Assigned To │ Reason            │
├───┼────────────────────────────┼──────────┼─────────────┼───────────────────┤
│ 1 │ 001-wcag-aa-compliance     │ REVIEW   │ reviewer    │ 3 SVGs ready      │
│ 2 │ 002-cookie-consent         │ REVIEW   │ reviewer    │ 3 SVGs ready      │
│ 3 │ 005-security-hardening     │ REVIEW   │ reviewer    │ 3 SVGs ready      │
│ 4 │ 007-e2e-testing-framework  │ REVIEW   │ reviewer    │ 2 SVGs ready      │
├─────────────────────────────────────────────────────────────────────────────┤
│ Commands: /queue add, /queue remove [#], /queue clear                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

If queue is empty:
```
┌─────────────────────────────────────────────────┐
│ TASK QUEUE                        0 items       │
├─────────────────────────────────────────────────┤
│ Queue is empty.                                 │
│                                                 │
│ Add items: /queue add [terminal] [feature]      │
│            [action] "[reason]"                  │
└─────────────────────────────────────────────────┘
```

#### /queue list [terminal]

Filter by assigned terminal:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TASKS FOR: reviewer                               4 items                   │
├─────────────────────────────────────────────────────────────────────────────┤
│ # │ Feature                    │ Action   │ Reason                          │
├───┼────────────────────────────┼──────────┼─────────────────────────────────┤
│ 1 │ 001-wcag-aa-compliance     │ REVIEW   │ 3 SVGs ready                    │
│ 2 │ 002-cookie-consent         │ REVIEW   │ 3 SVGs ready                    │
│ 3 │ 005-security-hardening     │ REVIEW   │ 3 SVGs ready                    │
│ 4 │ 007-e2e-testing-framework  │ REVIEW   │ 2 SVGs ready                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 4. Add Operation

#### /queue add [terminal] [feature] [action] "[reason]"

**Validate inputs**:
- Terminal must be valid (see valid terminals list)
- Action must be valid: PLAN, GENERATE, REVIEW, INSPECT, IMPLEMENT, AUDIT, TEST, DOCUMENT

**Create queue item**:
```json
{
  "feature": "[feature]",
  "svg": null,
  "action": "[ACTION]",
  "reason": "[reason]",
  "assignedTo": "[terminal]"
}
```

**Append to queue array**.

**Update timestamp**:
```json
"lastUpdated": "2026-01-14T15:30:00Z"
```

**Write back to file**.

**Output**:
```
┌─────────────────────────────────────────────────┐
│ Task Added                                      │
├─────────────────────────────────────────────────┤
│ Feature: [feature]                              │
│ Action: [ACTION]                                │
│ Assigned to: [terminal]                         │
│ Reason: [reason]                                │
│                                                 │
│ Queue position: #[N]                            │
└─────────────────────────────────────────────────┘
```

---

### 5. Remove Operation

#### /queue remove [index]

**Validate index**:
- Must be 1-based integer
- Must be within queue bounds

**Remove item at index** (convert to 0-based for array).

**Update timestamp**.

**Write back to file**.

**Output**:
```
┌─────────────────────────────────────────────────┐
│ Task Removed                                    │
├─────────────────────────────────────────────────┤
│ Removed: [feature] → [terminal] ([ACTION])      │
│                                                 │
│ Queue now has [N] items                         │
└─────────────────────────────────────────────────┘
```

---

### 6. Clear Operation

#### /queue clear

**Ask for confirmation** using AskUserQuestion:

Question: "Clear all [N] items from the queue?"
Options:
- Yes, clear the queue
- No, cancel

**If confirmed**:
- Set `queue` to empty array `[]`
- Update timestamp
- Write back to file

**Output**:
```
┌─────────────────────────────────────────────────┐
│ Queue Cleared                                   │
├─────────────────────────────────────────────────┤
│ Removed [N] items from queue.                   │
│ Queue is now empty.                             │
└─────────────────────────────────────────────────┘
```

---

## Valid Terminals

| Category | Terminals |
|----------|-----------|
| Council | CTO, Architect, Security, Toolsmith, DevOps, ProductOwner |
| Generators | WireframeGenerator1, WireframeGenerator2, WireframeGenerator3 |
| Pipeline | Planner, PreviewHost, WireframeQA, Validator, Inspector |
| Support | Author, Developer, TestEngineer, Auditor, Coordinator, QALead, TechWriter |

## Valid Actions

| Action | Description |
|--------|-------------|
| PLAN | Analyze spec, create wireframe assignments |
| GENERATE | Create SVG wireframes |
| REVIEW | Screenshot and document issues |
| INSPECT | Cross-SVG consistency check |
| IMPLEMENT | Convert spec/wireframe to code |
| AUDIT | Cross-artifact consistency check |
| TEST | Run test suites |
| DOCUMENT | Write documentation |

---

## Error Handling

| Scenario | Message |
|----------|---------|
| Not authorized (write) | "Queue modifications require Coordinator, CTO, or Architect authority" |
| Invalid terminal | "Unknown terminal: [name]" |
| Invalid action | "Invalid action: [action]. Valid: PLAN, GENERATE, REVIEW, etc." |
| Index out of bounds | "Invalid index: [N]. Queue has [M] items." |
| Missing arguments | "Usage: /queue add [terminal] [feature] [action] \"[reason]\"" |

---

## Related Skills

- `/queue-check` - View queue with additional filtering
- `/dispatch` - Alternative way to add tasks
- `/status` - Project overview including queue summary
- `/wireframe-status` - Update wireframe-specific status
