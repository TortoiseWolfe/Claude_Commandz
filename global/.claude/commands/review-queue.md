---
description: Show items pending review with age and issues file timestamps
---

# Review Queue

Display wireframes awaiting review with age tracking and issues file status.

**Primary user**: Reviewer terminal

## Usage

```
/review-queue                       # Show all items pending review
/review-queue --stale               # Show items older than 24 hours
/review-queue --with-issues         # Show items that have issues files
/review-queue [feature]             # Show details for specific feature
```

**Examples**:
```
/review-queue                       # Full review backlog
/review-queue --stale               # Items that need attention
/review-queue 001                   # Details for 001-wcag-aa-compliance
```

## Arguments

ARGUMENTS: $ARGUMENTS

- No args: Show all items with REVIEW action in queue
- `--stale`: Filter to items older than 24 hours
- `--with-issues`: Filter to items with existing `.issues.md` files
- `[feature]`: Show detailed status for specific feature

---

## Instructions

### 1. Read Queue Data

```bash
cat docs/design/wireframes/.terminal-status.json
```

Filter queue items where `action == "REVIEW"`.

### 2. Check Issues Files

For each feature in review queue, check for issues files:

```bash
ls docs/design/wireframes/[feature]/*.issues.md 2>/dev/null
```

Get modification timestamps:

```bash
stat -c "%Y %n" docs/design/wireframes/[feature]/*.issues.md 2>/dev/null
```

### 3. Count SVGs

For each feature:

```bash
ls docs/design/wireframes/[feature]/*.svg 2>/dev/null | wc -l
```

### 4. Calculate Age

Compare queue item timestamp (from `lastUpdated` or feature completion log) to current time.

Age categories:
- Fresh: < 1 hour
- Recent: 1-6 hours
- Waiting: 6-24 hours
- Stale: > 24 hours

---

### 5. Display Review Queue

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ REVIEW QUEUE                                          4 items pending       │
├─────────────────────────────────────────────────────────────────────────────┤
│ # │ Feature                    │ SVGs │ Issues │ Age      │ Status         │
├───┼────────────────────────────┼──────┼────────┼──────────┼────────────────┤
│ 1 │ 001-wcag-aa-compliance     │ 3    │ 3      │ 2h       │ Has issues     │
│ 2 │ 002-cookie-consent         │ 3    │ 3      │ 1h       │ Has issues     │
│ 3 │ 005-security-hardening     │ 3    │ 3      │ 1h       │ Has issues     │
│ 4 │ 007-e2e-testing-framework  │ 2    │ 2      │ 30m      │ Has issues     │
├─────────────────────────────────────────────────────────────────────────────┤
│ Total: 11 SVGs across 4 features                                            │
│                                                                             │
│ Start review: /wireframe-screenshots [feature]                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Status indicators**:
- `Fresh` - No issues files yet, needs initial review
- `Has issues` - Issues files exist, may need re-review
- `Needs regen` - Issues marked as REGEN classification

---

### 6. Feature Detail View

When a specific feature is requested:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ REVIEW DETAIL: 001-wcag-aa-compliance                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ Source: Generator-1                                                         │
│ Reason: Generator-1 completed 3 SVGs - ready for review                     │
│ Added to queue: 2h ago                                                      │
│                                                                             │
│ SVG FILES (3):                                                              │
│                                                                             │
│ 1. 01-accessibility-dashboard.svg                                           │
│    Issues: 01-accessibility-dashboard.issues.md (modified 1h ago)           │
│    Status: PATCH items logged                                               │
│                                                                             │
│ 2. 02-cicd-pipeline-integration.svg                                         │
│    Issues: 02-cicd-pipeline-integration.issues.md (modified 1h ago)         │
│    Status: PATCH items logged                                               │
│                                                                             │
│ 3. 03-accessibility-controls-overlay.svg                                    │
│    Issues: 03-accessibility-controls-overlay.issues.md (modified 1h ago)    │
│    Status: PATCH items logged                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ Actions:                                                                    │
│   /wireframe-screenshots 001     - Generate new screenshots                 │
│   /wireframe-status 001 approved - Mark as approved                         │
│   /wireframe-status 001 issues   - Mark issues found                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 7. Stale Filter (--stale)

Show only items older than 24 hours:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STALE REVIEWS (> 24 hours)                            2 items               │
├─────────────────────────────────────────────────────────────────────────────┤
│ # │ Feature                    │ SVGs │ Age      │ Reason                   │
├───┼────────────────────────────┼──────┼──────────┼──────────────────────────┤
│ 1 │ 003-user-authentication    │ 3    │ 2d 4h    │ Blocked on validator fix │
│ 2 │ 006-template-fork          │ 2    │ 1d 12h   │ Awaiting reviewer        │
├─────────────────────────────────────────────────────────────────────────────┤
│ These items need attention!                                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

If no stale items:
```
│ No stale reviews - all items are less than 24 hours old.                    │
```

---

### 8. With Issues Filter (--with-issues)

Show only items that have `.issues.md` files:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ REVIEWS WITH ISSUES                                   4 items               │
├─────────────────────────────────────────────────────────────────────────────┤
│ Feature                    │ Issue Files │ Last Modified │ Classification   │
├────────────────────────────┼─────────────┼───────────────┼──────────────────┤
│ 001-wcag-aa-compliance     │ 3           │ 1h ago        │ PATCH            │
│ 002-cookie-consent         │ 3           │ 1h ago        │ PATCH            │
│ 005-security-hardening     │ 3           │ 1h ago        │ PATCH            │
│ 007-e2e-testing-framework  │ 2           │ 30m ago       │ PATCH            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Issue Classification Summary

When displaying issues, check file content for classification:

| Classification | Meaning | Action |
|----------------|---------|--------|
| PATCH | Cosmetic fixes | Generator can patch |
| REGEN | Structural issues | Generator must regenerate |
| APPROVED | No issues | Ready for inspector |

To determine classification, grep issues files:
```bash
grep -l "REGEN" docs/design/wireframes/[feature]/*.issues.md
grep -l "PATCH" docs/design/wireframes/[feature]/*.issues.md
```

---

## Empty Queue

If no items pending review:

```
┌─────────────────────────────────────────────────┐
│ REVIEW QUEUE                      0 items       │
├─────────────────────────────────────────────────┤
│ No items pending review.                        │
│                                                 │
│ Check /queue for other task types.              │
└─────────────────────────────────────────────────┘
```

---

## Related Skills

- `/wireframe-screenshots [feature]` - Generate screenshots for review
- `/wireframe-review [feature]` - Full review workflow
- `/wireframe-status [feature]` - Update status after review
- `/queue` - Full queue management
- `/status` - Project overview
