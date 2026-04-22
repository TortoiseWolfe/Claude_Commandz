---
description: Generate a single SVG wireframe from assignment (focused context)
---

# /wireframe-focused - Single SVG Generation

**Purpose:** Generate ONE SVG with minimal context. ~800 lines vs ~3,550 for full /wireframe.

## Arguments

`$ARGUMENTS` = `NNN:NN` (e.g., `002:01` for feature 002, SVG 01)

## Pre-Flight (30 seconds)

### 1. Read Assignment

```bash
cat docs/design/wireframes/NNN-feature/assignments.json | jq '.assignments[N-1]'
```

Extract:
- `svg`: filename
- `focus`: what to show
- `userStories`: which US to include
- `mode`: new or patch

### 2. Read Checklist

```bash
cat docs/design/wireframes/VALIDATION_CHECKLIST.md
```

### 3. Read Relevant User Stories Only

From spec.md, extract ONLY the user stories listed in assignment.

---

## Layout Reference

```
Canvas: 1920×1080
Desktop: x=40, y=60, 1280×720
Mobile: x=1360, y=60, 360×720
Annotations: y=800, height≤220
```

## Color Reference (Light Theme)

| Element | Color |
|---------|-------|
| Panel background | `#e8d4b8` |
| Primary button | `#8b5cf6` |
| Toggle OFF/ON | `#6b7280` / `#22c55e` |
| US badge | `#0891b2` |
| FR badge | `#2563eb` |
| SC badge | `#f97316` |

## Typography

| Element | Size | Weight |
|---------|------|--------|
| Annotation title | 16px | bold |
| Narrative | 14px | regular |
| Callout number | 14px | bold white |

---

## Generate SVG

Create `docs/design/wireframes/NNN-feature/NN-name.svg`:

1. **Desktop mockup** (1280×720) showing the UI for this assignment's focus
2. **Mobile mockup** (360×720) mirroring desktop
3. **Callout circles** (①②③) on key elements
4. **Annotation panel** with US-anchored groups

---

## Validate

```bash
python3 docs/design/wireframes/validate-wireframe.py docs/design/wireframes/NNN-feature/NN-name.svg
```

**Must show `STATUS: PASS`** before marking complete.

If errors:
1. Fix the specific error
2. Re-run validator
3. Repeat until PASS

---

## Complete

Update `.terminal-status.json`:
- Remove from queue
- Add to completedToday

```
SVG COMPLETE: NNN-feature/NN-name.svg
Validator: PASS
```
