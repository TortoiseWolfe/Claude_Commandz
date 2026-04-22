---
description: Project health dashboard showing terminal states, queue depth, RFCs, and recent completions
---

# Project Status Dashboard

Comprehensive health check showing project status at a glance.

## Usage

```
/status                    # Full dashboard
/status --terminals        # Terminal status only
/status --queue            # Queue status only
/status --rfcs             # RFC status only
/status --today            # Today's activity only
```

## Arguments

ARGUMENTS: $ARGUMENTS

- No args: Full dashboard
- `--terminals`: Show terminal status grid
- `--queue`: Show queue and pending items
- `--rfcs`: Show RFC voting status
- `--today`: Show today's completions

---

## Instructions

### 1. Get Health Summary (Quick)

Run the terminal health script first for instant status:

```bash
# Quick one-line health summary
python3 scripts/terminal-health.py --summary

# Detailed health in JSON format
python3 scripts/terminal-health.py --json
```

This provides:
- Overall status (HEALTHY/WARNING/UNHEALTHY)
- Active, blocked, and queued counts
- Issues and warnings arrays

### 2. Read Data Sources

```bash
# Terminal and queue status
cat docs/design/wireframes/.terminal-status.json

# RFC files
ls docs/interoffice/rfcs/RFC-*.md 2>/dev/null

# Decision files
ls docs/interoffice/decisions/DEC-*.md 2>/dev/null

# Wireframe counts
find docs/design/wireframes -name "*.svg" -type f 2>/dev/null | wc -l
```

### 2. Build Dashboard

Display in this format:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PROJECT STATUS                                        2026-01-14 15:30:00   │
├─────────────────────────────────────────────────────────────────────────────┤
│ HEALTH: HEALTHY | 2 active | 0 blocked | 4 queued                    [OK]  │
├─────────────────────────────────────────────────────────────────────────────┤
│ WIREFRAMES                                                                  │
│   Features with SVGs: 8/46                                                  │
│   Total SVGs: 24                                                            │
│   Pending review: 4                                                         │
│   Approved: 0                                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ TERMINALS                                                                   │
│                                                                             │
│   COUNCIL (6)                                                               │
│   ┌─────────────┬─────────────┬─────────────┐                               │
│   │ cto: idle   │ architect:  │ security:   │                               │
│   │             │   idle      │   idle      │                               │
│   ├─────────────┼─────────────┼─────────────┤                               │
│   │ toolsmith:  │ devops:     │ product-    │                               │
│   │   idle      │   idle      │   owner:idle│                               │
│   └─────────────┴─────────────┴─────────────┘                               │
│                                                                             │
│   GENERATORS (3)                                                            │
│   ┌─────────────┬─────────────┬─────────────┐                               │
│   │ gen-1: idle │ gen-2: idle │ gen-3: idle │                               │
│   └─────────────┴─────────────┴─────────────┘                               │
│                                                                             │
│   PIPELINE (5)                                                              │
│   planner→viewer→reviewer→validator→inspector                               │
│   [idle]  [idle] [idle]   [idle]    [idle]                                  │
│                                                                             │
│   Active: 0  |  Idle: 17  |  Blocked: 0                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│ QUEUE (4 items)                                                             │
│                                                                             │
│   1. 001-wcag-aa-compliance → reviewer (REVIEW)                             │
│   2. 002-cookie-consent → reviewer (REVIEW)                                 │
│   3. 005-security-hardening → reviewer (REVIEW)                             │
│   4. 007-e2e-testing-framework → reviewer (REVIEW)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ RFCs                                                                        │
│                                                                             │
│   Pending: 0                                                                │
│   Decided today: 2 (DEC-001: QA Lead, DEC-002: Technical Writer)            │
├─────────────────────────────────────────────────────────────────────────────┤
│ TODAY'S ACTIVITY                                                            │
│                                                                             │
│   Completions: 15                                                           │
│   Latest: Generator-3 completed 002-cookie-consent (3 SVGs)                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3. Compute Metrics

**Terminal Status**:
- Count terminals by status (idle, active, blocked)
- Active = any non-idle status (generating, reviewing, etc.)

**Queue Metrics**:
- Total items in queue array
- Group by action type (PLAN, GENERATE, REVIEW, INSPECT)
- Group by assignee

**RFC Metrics**:
- Count files in `docs/interoffice/rfcs/`
- For each, check Status field (proposed, voting, decided, rejected)
- Count pending votes per RFC

**Wireframe Metrics**:
- Count `.svg` files per feature folder
- Count features with at least one SVG
- Check `wireframe-status.json` for approval status

### 4. Health Indicators

Use terminal-health.py for automated health detection:

```bash
# Check for blocked terminals
python3 scripts/terminal-health.py blocked

# Check for idle terminals
python3 scripts/terminal-health.py idle

# Check for stale assignments
python3 scripts/terminal-health.py stale
```

Add status indicators:

| Indicator | Meaning |
|-----------|---------|
| `[OK]` | Normal operation |
| `[WARN]` | Attention needed |
| `[BLOCKED]` | Something is stuck |

Conditions (auto-detected by terminal-health.py):
- `[WARN]` if queue > 10 items
- `[WARN]` if any RFC pending > 3 days
- `[WARN]` if stale assignments detected
- `[BLOCKED]` if all generators idle but queue not empty
- `[BLOCKED]` if pending RFC has reject vote

### 5. Low-Context Terminal Alerts

Monitor for terminals that may need context refresh:

```bash
# Check for stale terminals (assigned but inactive too long)
python3 scripts/terminal-health.py stale
```

Alert conditions for low-context:
- Terminal has assignment but no activity > 30 minutes
- Terminal status hasn't changed in > 1 hour with work pending
- Terminal is idle while queue has items for its role

When detected, include in dashboard:

```
│ ⚠️ LOW-CONTEXT ALERTS                                                        │
│   Inspector: stale assignment (45m), may need /prime                          │
│   Generator-2: idle with 3 GENERATE items in queue                            │
```

---

## Filtered Views

### --terminals

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TERMINAL STATUS                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ Terminal        │ Status      │ Feature              │ Task                 │
├─────────────────┼─────────────┼──────────────────────┼──────────────────────┤
│ cto             │ idle        │ -                    │ -                    │
│ architect       │ idle        │ -                    │ -                    │
│ generator-1     │ generating  │ 003-user-auth        │ SVG 2/4              │
│ reviewer        │ review      │ 001-wcag             │ Screenshots          │
│ ...             │ ...         │ ...                  │ ...                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### --queue

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ QUEUE STATUS                                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│ # │ Feature                    │ Action   │ Assigned To │ Reason            │
├───┼────────────────────────────┼──────────┼─────────────┼───────────────────┤
│ 1 │ 001-wcag-aa-compliance     │ REVIEW   │ reviewer    │ 3 SVGs ready      │
│ 2 │ 002-cookie-consent         │ REVIEW   │ reviewer    │ 3 SVGs ready      │
│ 3 │ 005-security-hardening     │ REVIEW   │ reviewer    │ 3 SVGs ready      │
│ 4 │ 007-e2e-testing-framework  │ REVIEW   │ reviewer    │ 2 SVGs ready      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### --rfcs

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ RFC STATUS                                                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│ RFC-001: Add QA Lead Role                                                   │
│   Status: decided → DEC-001                                                 │
│   Votes: 6 approve, 0 reject, 0 pending                                     │
│                                                                             │
│ RFC-002: Add Technical Writer Role                                          │
│   Status: decided → DEC-002                                                 │
│   Votes: 6 approve, 0 reject, 0 pending                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### --today

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TODAY'S ACTIVITY                                          2026-01-14        │
├─────────────────────────────────────────────────────────────────────────────┤
│ Completions (15):                                                           │
│                                                                             │
│ • Manager: Clean slate - deleted all SVGs except 000-landing-page           │
│ • Manager: Assigned 000-landing-page to generator-1                         │
│ • Planner: Completed wireframe plan for 003-user-authentication (4 SVGs)    │
│ • Generator-2: Completed 003-user-authentication (3 SVGs)                   │
│ • ...                                                                       │
│                                                                             │
│ RFCs Decided (2):                                                           │
│ • DEC-001: Add QA Lead Role (6-0-0)                                         │
│ • DEC-002: Add Technical Writer Role (6-0-0)                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Related Skills

- `/queue-check` - Detailed queue view with filtering
- `/vote-now` - Check and cast RFC votes
- `/wireframe-status` - Wireframe-specific status updates
- `/session-stats` - Token usage statistics

## Automation Scripts

This skill uses `scripts/terminal-health.py` for automated health detection:

| Command | Purpose |
|---------|---------|
| `--summary` | Quick one-line health status |
| `--json` | Full health data in JSON format |
| `check` | Full health check (default) |
| `blocked` | List blocked terminals |
| `idle` | List idle terminals |
| `stale` | List terminals with stale assignments |

The script reduces token usage by computing health metrics deterministically instead of parsing status files with AI.
