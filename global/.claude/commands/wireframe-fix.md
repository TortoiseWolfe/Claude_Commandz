---
description: Auto-load feature context and issues for targeted SVG fixes
---

# Wireframe Fix

Load all relevant context for fixing a specific SVG wireframe, including issues files, spec, and validation rules.

**Primary user**: Generator terminals

## Usage

```
/wireframe-fix [feature]            # Load context for entire feature
/wireframe-fix [feature]/[svg]      # Load context for specific SVG
```

**Examples**:
```
/wireframe-fix 001                  # Fix all SVGs in 001-wcag-aa-compliance
/wireframe-fix 001/02               # Fix specific SVG (02-cicd-pipeline-integration)
/wireframe-fix 003-user-auth        # Can use partial feature name
```

## Arguments

ARGUMENTS: $ARGUMENTS

- `[feature]`: Feature folder name or number (e.g., "001" or "001-wcag-aa-compliance")
- `[feature]/[svg]`: Feature and SVG number (e.g., "001/02" for second SVG)

---

## Instructions

### 1. Parse Arguments

Extract feature identifier and optional SVG number.

Resolve partial names:
- `001` → `001-wcag-aa-compliance`
- `003-user` → `003-user-authentication`

```bash
ls -d docs/design/wireframes/[feature]*/ | head -1
```

### 2. Validate Feature Exists

Check feature folder exists:

```bash
ls docs/design/wireframes/[feature]/ 2>/dev/null
```

If not found:
```
┌─────────────────────────────────────────────────┐
│ ERROR: Feature Not Found                        │
├─────────────────────────────────────────────────┤
│ No feature matching "[feature]" found.          │
│                                                 │
│ Available features:                             │
│   000-landing-page                              │
│   001-wcag-aa-compliance                        │
│   002-cookie-consent                            │
│   ...                                           │
└─────────────────────────────────────────────────┘
```

### 3. Run Autofix Check (NEW - Token Saver)

**IMPORTANT**: Before loading AI context, check if issues can be auto-fixed by script.

```bash
# Check all SVGs for auto-fixable issues (shows summary)
python3 scripts/svg-autofix.py all --summary

# Check specific SVG
python3 scripts/svg-autofix.py check docs/design/wireframes/[feature]/[svg-name].svg

# Filter all results by feature (JSON output)
python3 scripts/svg-autofix.py all --json | grep -A50 '"file": "[feature]'
```

**If fixable issues found**, show preview and apply:

```bash
# Preview fixes (dry-run)
python3 scripts/svg-autofix.py diff docs/design/wireframes/[feature]/[svg-name].svg

# Apply fixes (if user approves)
python3 scripts/svg-autofix.py fix docs/design/wireframes/[feature]/[svg-name].svg
```

**Auto-fixable issue types** (handled by script, no AI needed):
| Issue ID | Description | Fix Applied |
|----------|-------------|-------------|
| `panel_color_white` | Wrong panel color (#ffffff) | → #e8d4b8 |
| `title_position` | Title x position wrong | → x="960" |
| `signature_bold` | Signature not bold | → font-weight="bold" |
| `mobile_frame_x` | Mobile frame position | → x="1360" |
| `desktop_frame_x` | Desktop frame position | → x="40" |
| `viewbox_format` | ViewBox incorrect | → "0 0 1920 1080" |
| `annotation_overlap` | Annotations below y=800 | → y="780" |

**Decision Tree**:
```
Issues Found?
    │
    ├─ Auto-fixable only → Run svg-autofix.py fix → Done (no AI tokens)
    │
    ├─ Mixed (auto + semantic) → Run svg-autofix.py fix → Load remaining for AI
    │
    └─ Semantic only → Skip script → Load for AI (REGEN/complex PATCH)
```

### 4. Load Issues Files (if semantic issues remain)

**For entire feature**:
```bash
cat docs/design/wireframes/[feature]/*.issues.md 2>/dev/null
```

**For specific SVG**:
```bash
cat docs/design/wireframes/[feature]/[svg-name].issues.md 2>/dev/null
```

### 6. Load Feature Spec (Summary)

```bash
# Get first 100 lines of spec for context
head -100 features/[feature-category]/[feature]/spec.md 2>/dev/null
```

Or check `.specify/memory/spec-inventory.md` for feature location.

### 7. Load Validation Rules Reference

Display relevant rules from `GENERAL_ISSUES.md`:

```bash
cat docs/design/wireframes/GENERAL_ISSUES.md
```

### 8. Check Queue Status

```bash
cat docs/design/wireframes/.terminal-status.json | jq '.queue[] | select(.feature | contains("[feature]"))'
```

---

### 9. Display Context Summary

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ FIX CONTEXT: 001-wcag-aa-compliance                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ Queue Status: REVIEW (assigned to reviewer)                                 │
│ SVGs: 3 total                                                               │
│ Issues Files: 3 found                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ AUTOFIX RESULTS (from svg-autofix.py)                                       │
│ ─────────────────────────────────────────────────────────────────────────── │
│                                                                             │
│ ✅ 01-accessibility-dashboard.svg: 31 auto-fixable issues                   │
│    - panel_color_white (31x)                                                │
│    Run: python3 scripts/svg-autofix.py fix [path] --dry-run                 │
│                                                                             │
│ ✅ 02-cicd-pipeline-integration.svg: 39 auto-fixable issues                 │
│    - panel_color_white (39x)                                                │
│                                                                             │
│ ✅ 03-accessibility-controls-overlay.svg: 45 auto-fixable issues            │
│    - panel_color_white (45x)                                                │
│                                                                             │
│ TOTAL: 115 auto-fixable (script) | 0 semantic (AI needed)                   │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ SEMANTIC ISSUES (require AI attention)                                      │
│ ─────────────────────────────────────────────────────────────────────────── │
│                                                                             │
│ ## Issue: TOUCH-001 - Button too small                                      │
│ - Classification: PATCH (semantic - size calculation)                       │
│ - Location: Mobile mockup, submit button                                    │
│ - Problem: Button height is 32px, needs 44px minimum                        │
│ - Fix: Increase height attribute to 44                                      │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ RELEVANT RULES FROM GENERAL_ISSUES.md                                       │
│ ─────────────────────────────────────────────────────────────────────────── │
│                                                                             │
│ G-001: Panel backgrounds must use #e8d4b8, never #ffffff                    │
│ G-002: Touch targets minimum 44px                                           │
│ G-005: Toggle colors: OFF=#6b7280, ON=#22c55e                               │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ NEXT STEPS                                                                  │
│                                                                             │
│ 1. APPLY AUTOFIX (saves ~5,000 tokens per SVG):                             │
│    python3 scripts/svg-autofix.py fix docs/design/wireframes/[feature]/     │
│                                                                             │
│ 2. For remaining semantic issues, use Edit tool or /wireframe (REGEN)       │
│                                                                             │
│ 3. Run validator to confirm all fixes applied:                              │
│    python3 docs/design/wireframes/validate-wireframe.py [feature]/          │
│                                                                             │
│ 4. Update issues file or mark resolved                                      │
│                                                                             │
│ ⚡ TOKEN SAVINGS: Script handles 115/115 issues (100%) - no AI needed!     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 10. Single SVG Mode

When specific SVG requested (`/wireframe-fix 001/02`):

Only load context for that specific SVG:
- Its issues file
- Its SVG content (first 50 lines for reference)
- Relevant general rules

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ FIX CONTEXT: 001-wcag-aa-compliance / 02-cicd-pipeline-integration          │
├─────────────────────────────────────────────────────────────────────────────┤
│ SVG: docs/design/wireframes/001-wcag-aa-compliance/                         │
│      02-cicd-pipeline-integration.svg                                       │
│                                                                             │
│ ISSUES (from 02-cicd-pipeline-integration.issues.md):                       │
│ ─────────────────────────────────────────────────────────────────────────── │
│ [Full issues file content]                                                  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ SVG PREVIEW (first 50 lines):                                               │
│ ─────────────────────────────────────────────────────────────────────────── │
│ <?xml version="1.0" encoding="UTF-8"?>                                      │
│ <svg viewBox="0 0 1920 1080" ...>                                           │
│   ...                                                                       │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ To fix: Read full SVG, apply changes, run validator                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Issue Classification Actions

| Classification | Action | Priority |
|----------------|--------|----------|
| AUTOFIX | `python3 scripts/svg-autofix.py fix [path]` | ⚡ First (no AI) |
| PATCH | Use Edit tool to fix specific lines in SVG | 2nd (AI) |
| REGEN | Use /wireframe to regenerate entire SVG | 3rd (AI) |
| APPROVED | No action needed, move to inspector | - |

**Priority Order**: Always run AUTOFIX first. Many PATCH issues are actually auto-fixable.

---

## After Fixing

1. **Run validator** to confirm fixes:
   ```bash
   python3 docs/design/wireframes/validate-wireframe.py docs/design/wireframes/[feature]/
   ```

2. **Update issues file** if issues are resolved (or regenerated)

3. **Update queue status** if all SVGs in feature are fixed:
   ```
   /wireframe-status [feature] review
   ```

---

## Related Skills

- `/wireframe [feature]` - Regenerate SVG (for REGEN issues)
- `/wireframe-prep [feature]` - Full context prep before generation
- `/wireframe-status` - Update status after fixes
- `/review-queue` - See what's pending review
