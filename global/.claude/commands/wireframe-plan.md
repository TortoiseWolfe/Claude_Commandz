---
description: Plan wireframe assignments for a feature (Planner terminal)
---

# /wireframe-plan - SVG Assignment Planning

**Purpose:** Analyze feature spec and create focused assignments for Generator terminals.

## Arguments

`$ARGUMENTS` = feature code (e.g., `002` or `002-cookie-consent`)

## Workflow

### 1. Parse Feature Spec

```bash
python3 docs/design/wireframes/wireframe-plan-generator.py parse [feature]
```

Shows extracted User Stories (US-XXX), Requirements (FR-XXX), and Criteria (SC-XXX).

### 2. Get SVG Suggestions

```bash
python3 docs/design/wireframes/wireframe-plan-generator.py suggest [feature]
```

Shows recommended SVG count and names based on complexity analysis.

### 3. Review Suggestions with User

Use `AskUserQuestion` to confirm:
- SVG count (accept suggestion or override)
- SVG names/focus areas
- Any additional screens needed

### 4. Create Assignments

```bash
python3 docs/design/wireframes/wireframe-plan-generator.py create [feature]
```

Creates `docs/design/wireframes/NNN-feature/assignments.json` with:
- SVG names
- Focus descriptions
- Linked requirements
- Mode (new/regen)

**If user requested changes:** Edit the generated assignments.json before proceeding.

### 5. Assign to Generator

```bash
# Auto-assign to least-loaded generator
python3 docs/design/wireframes/wireframe-plan-generator.py assign [feature]

# Or specify generator
python3 docs/design/wireframes/wireframe-plan-generator.py assign [feature] generator-2
```

This:
- Uses `queue_manager.py` to find least-loaded generator
- Adds all SVGs to queue with same generator (feature-grouped)
- Updates terminal status

### 6. Show Summary

```bash
python3 docs/design/wireframes/wireframe-plan-generator.py summary [feature]
```

Output the planning summary and handoff message.

## Quick Workflow

```bash
# Full automated flow
python3 docs/design/wireframes/wireframe-plan-generator.py suggest [feature]
# Review with user via AskUserQuestion
python3 docs/design/wireframes/wireframe-plan-generator.py assign [feature]
```

## Output Files

- `docs/design/wireframes/NNN-feature/assignments.json` - Machine-readable assignments
- `.terminal-status.json` queue updated via `queue_manager.py`

## SVG Naming Convention

```
NN-kebab-case-description.svg

01-landing-page-hero.svg
02-user-registration-flow.svg
03-dashboard-overview.svg
```

## DO NOT

- Use jq for queue operations (use queue_manager.py)
- Skip user confirmation of SVG count/names
- Assign different SVGs from same feature to different generators
