---
description: Update wireframe status with interactive menu (modifies wireframe-status.json)
---

# Wireframe Status Manager

Update the status of wireframes in the central tracker with an interactive menu.

**File:** `docs/design/wireframes/wireframe-status.json`

## Usage

```
/wireframe-status                    # Interactive dashboard
/wireframe-status [feature]          # Status + actions for specific feature
/wireframe-status [feature]:[svg]    # Status for specific SVG
/wireframe-status --queue            # Show items from .terminal-status.json queue
```

**Examples**:
```
/wireframe-status                    # Show dashboard with all features
/wireframe-status 001                # Show 001-wcag-aa-compliance status
/wireframe-status 003:02             # Show 003 SVG #2 status
/wireframe-status --queue            # Show pending review items
```

## Arguments

```text
$ARGUMENTS
```

- No args: Interactive dashboard with feature selection
- `FEATURE`: Feature number (e.g., `000`, `001`)
- `FEATURE:SVG`: Specific SVG (e.g., `003:02` for third feature, second SVG)
- `--queue`: Show items from `.terminal-status.json` queue

---

## Instructions

### 1. No Arguments → Interactive Dashboard

Display status summary:

```bash
python3 docs/design/wireframes/wireframe-status-manager.py summary
```

Then present interactive menu using `AskUserQuestion`:

**Question**: "Select an action:"
**Options**:
1. View feature status (enter number)
2. List by status (draft/review/approved)
3. Show queue items
4. Validate JSON structure

### 2. Feature Argument → Feature Detail + Actions

```bash
python3 docs/design/wireframes/wireframe-status-manager.py get [feature]
```

Display current status, then present action menu:

**Question**: "Update status for [feature]:"
**Options**:

| Option | Action |
|--------|--------|
| Set to REVIEW | Ready for reviewer |
| Set to APPROVED | Passed all checks |
| Set to ISSUES | Problems found |
| Set to REGENERATING | Being regenerated |
| Back to dashboard | Return to main menu |

### 3. Update Status

After user selection:

```bash
# Update entire feature
python3 docs/design/wireframes/wireframe-status-manager.py set [feature] [status]

# Update single SVG
python3 docs/design/wireframes/wireframe-status-manager.py set-svg [feature] [svg] [status]
```

### 4. Queue Integration (--queue)

Read `.terminal-status.json` and show pending items:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Wireframe Queue                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ PENDING REVIEW (4)                                                          │
│                                                                             │
│ 1. 001-wcag-aa-compliance (3 SVGs) → reviewer                               │
│ 2. 002-cookie-consent (3 SVGs) → reviewer                                   │
│ 3. 005-security-hardening (3 SVGs) → reviewer                               │
│ 4. 007-e2e-testing-framework (2 SVGs) → reviewer                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ Quick action: /wireframe-status 001 to update                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5. Sync Queue and Status

When updating status to APPROVED or ISSUES:
1. Update `wireframe-status.json` via Python script
2. Optionally remove from `.terminal-status.json` queue if complete

---

## Status Values

| Key | Emoji | Meaning | Valid Transitions |
|-----|-------|---------|-------------------|
| draft | 📝 | Initial generation | → regenerating, review |
| regenerating | 🔄 | Being regenerated | → draft, review |
| review | 👁️ | Under review | → issues, patchable, approved |
| issues | 🔴 | Has problems | → regenerating |
| patchable | 🟡 | Minor fixes needed | → regenerating, approved |
| approved | ✅ | Passed review | → planning |
| planning | 📋 | In planning phase | → tasked |
| tasked | 📝 | Has tasks.md | → inprogress |
| inprogress | 🚧 | Implementation started | → complete, blocked |
| complete | ✅ | Implementation done | (terminal) |
| blocked | ⛔ | Waiting on dependency | → inprogress |

---

## Batch Operations

```bash
# Set all features in a category to same status
python3 docs/design/wireframes/wireframe-status-manager.py batch-set foundation approved

# List features needing attention
python3 docs/design/wireframes/wireframe-status-manager.py list issues
python3 docs/design/wireframes/wireframe-status-manager.py list patchable
```

---

## Other Commands

```bash
# List features with specific status
python3 docs/design/wireframes/wireframe-status-manager.py list draft

# List all features
python3 docs/design/wireframes/wireframe-status-manager.py features

# Validate JSON structure
python3 docs/design/wireframes/wireframe-status-manager.py validate

# Show menu options for feature
python3 docs/design/wireframes/wireframe-status-manager.py menu [feature]
```

---

## Related Skills

- `/queue-check` - View terminal queue and status
- `/dispatch` - Send tasks to terminals
- `/wireframe-review` - Review workflow for screenshots

---

## DO NOT

- Read/write JSON manually (use Python script)
- Create verbose tables (Python output is sufficient)
- Skip the AskUserQuestion for status updates
- Update status without checking current state first
