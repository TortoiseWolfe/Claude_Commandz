---
description: Review SVG wireframes. Find issues, classify as PATCH or REGEN.
---

# /wireframe-review v4.0

**Philosophy:** Docker does the work, Claude does the thinking.

## Token-Efficient Workflow

1. Docker takes screenshots + runs validator (0 tokens)
2. Claude reads PNGs once (minimal tokens)
3. Claude applies visual checks
4. Auto-logs issues to feature/*.issues.md

---

## Input

```text
$ARGUMENTS
```

- `NNN` - Review all SVGs for feature
- `NNN:NN` - Review single SVG

---

## Step 1: Run Docker Screenshot Tool (EXECUTE THIS)

Use the Bash tool to run:

```bash
docker compose -f docs/design/wireframes/docker-compose.yml run --rm review $ARGUMENTS
```

This takes 5 screenshots per SVG at 2x resolution and runs the validator.
Wait for completion before proceeding to Step 2.

---

## Step 2: Read Results

Read the summary manifest:

```
docs/design/wireframes/png/[feature]/summary.json
```

This contains:
- List of all SVGs processed
- Screenshot paths
- Validator error counts

---

## Step 3: Visual Review (Read PNGs)

For each SVG, read these images:

1. `overview.png` - Full wireframe at 100%
2. `quadrant-tl.png` - Top-left detail at 200%
3. `quadrant-tr.png` - Top-right detail at 200%
4. `quadrant-bl.png` - Bottom-left detail at 200%
5. `quadrant-br.png` - Bottom-right detail at 200%

---

## Step 4: Apply Visual Checks

Check for issues the validator can't catch:

| Check | What to Look For | Code |
|-------|------------------|------|
| Theme mismatch | Dark bg on UI wireframe | VIS-001 |
| Text readability | Truncated, overlapping, or illegible text | VIS-002 |
| Arrow collisions | Arrows crossing through text or UI elements | VIS-003 |
| Element overflow | Content outside container bounds | VIS-004 |
| Missing sections | Expected UI components not visible | VIS-005 |
| Visual balance | Awkward spacing, cramped layout | VIS-006 |
| Callout placement | Callouts obscuring important UI | VIS-007 |

---

## Step 5: Log Issues

Combine validator errors (from manifest) with visual issues found.

Use standard classification:
- **PATCH**: Color, font, typo, badge position
- **REGENERATE**: Layout, structure, missing sections

Update the feature's `.issues.md` file:

```markdown
# [SVG Name] Review

**Status:** REGENERATE | PATCHABLE | PASS
**Date:** [YYYY-MM-DD]
**Validator Errors:** [N from manifest]
**Visual Issues:** [N from CV review]

## Validator Issues

[Copy from manifest.json]

## Visual Issues

| # | Check | Location | Issue | Classification |
|---|-------|----------|-------|----------------|
| 1 | VIS-002 | Desktop header | Text truncated | PATCH |
```

---

## Output Rules

- Report issues only - no confirmations
- Binary classification: PATCH or REGENERATE
- If ANY REGENERATE → entire file needs regeneration
- Summary table at end

---

## Step 6: Queue Regeneration (MANDATORY if REGENERATE found)

If **any SVG has REGENERATE classification**, automatically queue for Generator:

**Read** `.terminal-status.json`:
```bash
cat docs/design/wireframes/.terminal-status.json | jq '.'
```

**Add REGEN action** for each SVG needing regeneration:
```json
{
  "feature": "[NNN]-[feature-name]",
  "svg": "[NN]-[name].svg",
  "action": "REGEN",
  "reason": "Reviewer: [primary issue from review]",
  "assignedTo": null
}
```

**Add completion entry** to `completedToday`:
```
"Reviewer: Reviewed [NNN]-[feature-name] - [N] PASS, [N] REGEN queued"
```

**Validate JSON before writing:**
```bash
# Test parse - will error if invalid JSON
python3 -c "import json; json.load(open('docs/design/wireframes/.terminal-status.json'))"
```
If validation fails, fix the JSON structure before proceeding.

**Write** updated `.terminal-status.json`.

**Output:**
```
═══════════════════════════════════════════
REGENERATION QUEUED
═══════════════════════════════════════════
Feature: [NNN]-[feature-name]
REGEN: [list of SVGs needing regeneration]
PASS: [list of SVGs that passed]

Manager can assign to idle Generator with /next
═══════════════════════════════════════════
```

**If all SVGs PASS:**
- Update `wireframe-status.json` to status: "approved"
- No queue changes needed
- Output: "All SVGs passed review. Feature [NNN] marked approved."

---

## After Review (Manual Alternative)

If not using auto-queue:

```bash
/wireframe [feature]    # Patches PATCH, regenerates REGEN, skips PASS
```
