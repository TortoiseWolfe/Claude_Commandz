---
description: Plan wireframe assignments for a feature (Planner terminal)
---

# /wireframe-plan - SVG Assignment Planning

**Purpose:** Analyze feature spec and create focused assignments for Generator terminals.

## Arguments

`$ARGUMENTS` = feature code (e.g., `002` or `002-cookie-consent`)

## Workflow

### 1. Read Feature Spec

```bash
# Find and read the spec
cat features/*/NNN-*/spec.md
```

Extract:
- Feature name and description
- User Stories (US-XXX)
- Functional Requirements (FR-XXX)
- Success Criteria (SC-XXX)

### 2. Determine SVG Count

**Guidelines:**
- 1 SVG per major user flow or screen
- Complex features: 2-4 SVGs
- Simple features: 1-2 SVGs
- Backend/architecture features: 1-2 SVGs (diagrams)

### 3. Create Assignments

Output to `docs/design/wireframes/NNN-feature/assignments.json`:

```json
{
  "feature": "NNN-feature-name",
  "plannedAt": "ISO-8601-timestamp",
  "svgCount": N,
  "assignments": [
    {
      "svg": "01-descriptive-name.svg",
      "focus": "One sentence describing what this SVG shows",
      "userStories": ["US-001", "US-002"],
      "requirements": ["FR-001", "FR-002", "SC-001"],
      "mode": "new"
    }
  ]
}
```

### 4. Update Queue

Add assignments to `.terminal-status.json` queue:

```json
{
  "feature": "NNN-feature",
  "svg": "01-name.svg",
  "action": "GENERATE",
  "reason": "Planned by Planner",
  "assignedTo": null
}
```

**Validate JSON before writing:**
```bash
# Test parse - will error if invalid JSON
python3 -c "import json; json.load(open('docs/design/wireframes/.terminal-status.json'))"
```
If validation fails, fix the JSON structure before proceeding.

### 5. Handoff

Output summary for Manager:

```
WIREFRAME PLAN COMPLETE

Feature: NNN-feature-name
SVGs Planned: N

Assignments:
  01-name.svg → [US-001, US-002] - "Focus description"
  02-name.svg → [US-003] - "Focus description"

Queue updated. Generator terminals can now pick up assignments.
```

## SVG Naming Convention

```
NN-kebab-case-description.svg

01-landing-page-hero.svg
02-user-registration-flow.svg
03-dashboard-overview.svg
```

## Output Files

- `docs/design/wireframes/NNN-feature/assignments.json` - Machine-readable assignments
- `.terminal-status.json` queue updated with GENERATE actions
