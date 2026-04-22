---
description: Cross-SVG consistency checker (Inspector terminal)
---

# /wireframe-inspect - Cross-SVG Pattern Inspection

**Purpose:** Check all SVGs across features for structural consistency. Run after Validator passes.

## Arguments

- No arguments: Inspect all SVGs
- `$ARGUMENTS` = specific SVG path (optional)

## Workflow

### 1. Run Inspector Script

```bash
cd docs/design/wireframes && python inspect-wireframes.py --all
```

### 2. Review Output

The script checks each SVG against expected patterns:

| Check | Expected |
|-------|----------|
| Title position | x=960, y=28 (centered) |
| Signature position | y=1060, bold |
| Desktop header | `includes/header-desktop.svg` |
| Desktop footer | `includes/footer-desktop.svg` at y=640 |
| Mobile header | `includes/header-mobile.svg` |
| Mobile footer | `includes/footer-mobile.svg` at y=664 |
| Desktop mockup | x=40, y=60 |
| Mobile mockup | x=1360, y=60 |
| Annotation panel | x=40, y=800 |

### 3. Find Oddballs

The script also compares SVGs against each other to find deviations from the majority pattern. If most SVGs have title at y=28 but one has y=35, that's flagged as an oddball.

### 4. Log Issues

Pattern violations are logged to `*.issues.md` files with classification: `PATTERN_VIOLATION`

Example entry:
```
## Inspector Issues (2026-01-13)

| Check | Expected | Actual | Classification |
|-------|----------|--------|----------------|
| title_y_position | y=28 | y=35 | PATTERN_VIOLATION |
```

## Commands

```bash
# Inspect all SVGs
python docs/design/wireframes/inspect-wireframes.py --all

# JSON report (for automation)
python docs/design/wireframes/inspect-wireframes.py --report

# Inspect single SVG
python docs/design/wireframes/inspect-wireframes.py 002-cookie-consent/01-consent-modal.svg
```

## Output

On completion:
- PASS: All SVGs follow consistent patterns
- FAIL: N pattern violations found (logged to *.issues.md)

## When to Run

1. After all SVGs pass Validator (no errors)
2. Before marking features as "approved"
3. When adding new SVGs to verify consistency

## Issue Classification

| Classification | Meaning | Action |
|----------------|---------|--------|
| PATTERN_VIOLATION | SVG deviates from expected pattern | Generator regenerates or patches |

## DO NOT

- Run before Validator passes
- Ignore pattern violations (they indicate inconsistency)
- Manually edit issues files (auto-generated)
