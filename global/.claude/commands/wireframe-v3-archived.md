---
description: Generate lean UI mockup SVGs with mandatory pre-flight approval
---

# /wireframe Skill v3.0 (Lean UI Mockups)

**Philosophy:** Wireframes show screens, not documentation.

---

## What Wireframes Show

| Include | Exclude |
|---------|---------|
| Desktop + Mobile UI mockups | User Stories section |
| STATE labels and flow arrows | Requirement descriptions |
| Small FR/SC badges ON elements | "Accessibility" / "Performance" panels |
| Simple color-key legend | 30-word legend definitions |
| Actual screens and components | Annotation boxes with text |

---

## User Input

```text
$ARGUMENTS
```

- `NNN` - Process all SVGs for feature (e.g., `/wireframe 002`)
- `NNN:NN` - Process single SVG (e.g., `/wireframe 002:01`)

---

## Phase 1: PRE-FLIGHT (Mandatory)

**Read these files first:**
1. `features/*/[NNN]-*/spec.md`
2. `docs/design/wireframes/[NNN]-*/*.issues.md` (if exists)
3. `docs/design/wireframes/GENERAL_ISSUES.md`

**Output this checklist:**

```
═══════════════════════════════════════════════════════════════
PRE-FLIGHT: [NNN]-[feature-name] wireframe [NN]
═══════════════════════════════════════════════════════════════

THEME: [Light/Dark] (Light=user-facing UI, Dark=backend/infra)
CANVAS: 1920×1080

SCREENS TO SHOW:
- STATE 1: [description] - [which FR/SC badges]
- STATE 2: [description] - [which FR/SC badges]
- STATE 3: [description] - [which FR/SC badges]

BADGE PLACEMENT (no duplicates):
- FR-001 → STATE 1, modal header
- FR-002 → STATE 1, button group
- SC-001 → STATE 3, confirmation
[list all, each code appears ONCE]

BLOCKING CHECKS:
[ ] Each badge appears exactly once
[ ] No User Stories section planned
[ ] No annotation descriptions planned
[ ] Legend is color-key only (1 row)

═══════════════════════════════════════════════════════════════
READY TO GENERATE? [Waiting for user confirmation]
═══════════════════════════════════════════════════════════════
```

**Do NOT proceed without user confirmation.**

---

## Phase 1.5: LAYOUT PLAN (Mandatory)

**After PRE-FLIGHT approval, BEFORE generating SVG, output a layout table.**

```
═══════════════════════════════════════════════════════════════
LAYOUT PLAN: [NNN]-[feature] wireframe [NN]
═══════════════════════════════════════════════════════════════

DESKTOP AREA: x=40, y=60, w=1366, h=768
State count: [N]
State width: [calculated]px each (see table below)

| Element          | x    | y    | w    | h    | Collision? |
|------------------|------|------|------|------|------------|
| STATE 1 panel    | 50   | 100  | 420  | 450  | ✓ OK       |
| STATE 2 panel    | 490  | 100  | 420  | 450  | ✓ OK       |
| STATE 3 panel    | 930  | 100  | 420  | 450  | ✓ OK       |
| FR-001 badge     | 320  | 115  | 55   | 20   | ✓ OK       |
| Toggle (OFF)     | 550  | 200  | 50   | 24   | ✓ OK       |
| FR-005 badge     | 610  | 200  | 55   | 20   | ✓ OK (10px gap from toggle) |
...

SPACE CHECK:
- Desktop width used: 1350px / 1366px = 99% ✓
- Badges 10px from UI elements: YES ✓
- No collisions detected: YES ✓

═══════════════════════════════════════════════════════════════
LAYOUT APPROVED? [Waiting for user confirmation]
═══════════════════════════════════════════════════════════════
```

### Space Distribution Table

| States | Width per State | Gap Between |
|--------|-----------------|-------------|
| 2 | 660px | 40px |
| 3 | 420px | 40px |
| 4 | 310px | 30px |

### Collision Detection

Two elements collide if ALL of these are true:
```
el1.x < el2.x + el2.width AND
el1.x + el1.width > el2.x AND
el1.y < el2.y + el2.height AND
el1.y + el1.height > el2.y
```

**DO NOT proceed to GENERATE if any collisions detected.**

---

## Phase 2: GENERATE

### Canvas Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ TITLE (y=28)                                                    │
├──────────────────────────────────────────────┬──────────────────┤
│                                              │                  │
│     DESKTOP VIEWPORT                         │  MOBILE VIEWPORT │
│     x=40, y=60, 1366×768                     │  x=1500, y=60    │
│                                              │  360×700         │
│     [All UI states and flows here]           │  [Mobile UI]     │
│     [FR/SC badges on elements]               │  [Badges]        │
│                                              │                  │
│                                              │                  │
│                                              │                  │
│                                    y=828 max │         y=760 max│
├──────────────────────────────────────────────┴──────────────────┤
│  LEGEND (y=1020): Color key only                                │
│  FOOTER (y=1050): NNN:NN | Feature Name | ScriptHammer          │
└─────────────────────────────────────────────────────────────────┘
```

### SVG Root Tag (REQUIRED)

**Every SVG MUST start with this exact format:**

```xml
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1920 1080" width="1920" height="1080">
```

**All 4 attributes are required:**
- `xmlns` - SVG namespace
- `xmlns:xlink` - XLink namespace (for links)
- `viewBox` - Coordinate system
- `width` and `height` - **CRITICAL: Without these, the SVG won't render in the viewer**

---

### 10 Rules (Total)

| # | Rule | Implementation |
|---|------|----------------|
| 1 | Canvas 1920×1080 | `viewBox="0 0 1920 1080" width="1920" height="1080"` |
| 2 | Desktop at (40,60) | 1366×768 viewport |
| 3 | Mobile at (1500,60) | 360×700 viewport |
| 4 | Badge-only labels | `FR-001` not `FR-001: description...` |
| 5 | No duplicates | Each FR/SC code appears exactly ONCE |
| 6 | Badges clickable | Link to spec.md sections |
| 7 | Color-key legend | One row, no descriptions |
| 8 | Headers from templates | Inject from `includes/` |
| 9 | Light theme: parchment | `#e8d4b8`, never `#ffffff` |
| 10 | 44px touch targets | All interactive elements |

---

## Badge Format

**Simple badge (no container, no description):**

```xml
<a href="https://github.com/TortoiseWolfe/ScriptHammer/blob/main/features/[cat]/[feature]/spec.md#functional-requirements">
  <rect x="10" y="10" width="50" height="18" rx="3" fill="#2563eb"/>
  <text x="35" y="23" fill="#fff" font-size="11" font-weight="bold" text-anchor="middle">FR-001</text>
</a>
```

**Colors:**
- FR: `#2563eb` (blue)
- SC: `#ea580c` (orange)
- Priority: P0=`#dc2626`, P1=`#f59e0b`, P2=`#3b82f6`

**Placement:** Badges go OUTSIDE UI elements (10px minimum gap). Never on top of toggles or buttons.

---

## UI Element Color Standards (G-015)

### Toggle Switches

| State | Track Color | Knob Color |
|-------|-------------|------------|
| OFF | `#6b7280` (gray-500) | `#ffffff` |
| ON | `#22c55e` (green-500) | `#ffffff` |

**NEVER use light grey or purple for toggles.**

### Buttons

| Button Type | Background | Text | Border |
|-------------|------------|------|--------|
| Primary | `#8b5cf6` (violet) | `#ffffff` | none |
| Secondary | `#f5f0e6` (cream) | `#8b5cf6` | `#8b5cf6` 2px |
| Tertiary | `#dcc8a8` (tan) | `#374151` | `#b8a080` 1px |

**NO transparent backgrounds.**

### Badge vs UI Element

| Element | Color | Purpose |
|---------|-------|---------|
| FR badge | `#2563eb` blue | Annotation |
| SC badge | `#ea580c` orange | Annotation |
| UI button | `#8b5cf6` violet | Interactive |

**Badges annotate. Buttons act. They must look DIFFERENT.**

---

## Legend Format (New)

```xml
<g transform="translate(40, 1020)">
  <rect width="400" height="24" rx="4" fill="#dcc8a8" stroke="#b8a080"/>
  <rect x="10" y="4" width="40" height="16" rx="3" fill="#2563eb"/>
  <text x="30" y="16" fill="#fff" font-size="10" text-anchor="middle">FR</text>
  <text x="58" y="16" fill="#374151" font-size="12">Functional Req</text>
  <text x="140" y="16" fill="#374151" font-size="12">•</text>
  <rect x="155" y="4" width="40" height="16" rx="3" fill="#ea580c"/>
  <text x="175" y="16" fill="#fff" font-size="10" text-anchor="middle">SC</text>
  <text x="203" y="16" fill="#374151" font-size="12">Success Criteria</text>
</g>
```

**No 30-word descriptions. Just color reference.**

---

## Footer Format

```xml
<text x="60" y="1050" fill="#4b5563" font-size="13">
  002:01 | Cookie Consent - Modal Flow | ScriptHammer
</text>
```

---

## Theme Selection

| Question | Answer | Theme |
|----------|--------|-------|
| User-facing UI? (modal, form, dashboard) | Yes | Light |
| Backend/infrastructure? (RLS, API, auth) | Yes | Dark |
| Would end users see this screen? | Yes→Light, No→Dark | - |

### Light Theme

| Element | Color |
|---------|-------|
| Background | `#c7ddf5` → `#b8d4f0` gradient |
| Panels | `#e8d4b8` (parchment) |
| Secondary | `#dcc8a8` |
| Inputs | `#f5f0e6` |
| Borders | `#b8a080` |
| Primary | `#8b5cf6` |

### Dark Theme

| Element | Color |
|---------|-------|
| Background | `#0f172a` → `#1e293b` gradient |
| Panels | `#1e293b` |
| Secondary | `#334155` |
| Borders | `#475569` |
| Primary | `#8b5cf6` |

---

## Template Injection

**Location:** `docs/design/wireframes/includes/`

| File | Inject At |
|------|-----------|
| `header-desktop.svg` | `translate(50, 70)` |
| `header-mobile.svg` | `translate(10, 10)` |
| `footer-mobile.svg` | `translate(10, 634)` |

**Process:** Read file → Copy `<g>` content → Paste into SVG

**Never redraw headers manually.**

---

## Phase 3: VERIFY

After writing SVG:

```
═══════════════════════════════════════════════════════════════
GENERATED: [NNN]-[feature]/[NN]-[name].svg
═══════════════════════════════════════════════════════════════

VERIFIED:
[x] SVG has width="1920" height="1080" attributes
[x] Canvas 1920×1080
[x] No User Stories section
[x] No annotation descriptions
[x] Each badge appears once
[x] Legend is color-key only
[x] Headers from templates

VISUAL CHECK: Badge positions, text overflow, flow arrows
═══════════════════════════════════════════════════════════════
```

---

## Phase 4: VIEWER SYNC (Mandatory)

**After writing any SVG, ALWAYS output this to the user:**

```
═══════════════════════════════════════════════════════════════
VIEWER SYNC
═══════════════════════════════════════════════════════════════
SVG written: docs/design/wireframes/[feature]/[file].svg

ACTION REQUIRED:
→ Hard-refresh browser: Ctrl+Shift+R (Cmd+Shift+R on Mac)
→ Or open: http://localhost:3000/#[feature]/[file].svg
═══════════════════════════════════════════════════════════════
```

**This step is NOT optional.** The user needs to know how to view the new SVG.

---

## What NOT to Include

| Old Element | Why Removed |
|-------------|-------------|
| USER STORIES section | Move to spec.md - wireframes show UI, not docs |
| Annotation boxes | Badges only, no violet containers with text |
| "Accessibility" panel | FR-022/23/24 go on actual keyboard/focus elements |
| "Performance" panel | SC-001/007 go on actual loading indicators |
| Legend descriptions | Color key only; full descriptions in spec.md |
| Mobile badge lists | Just show mobile UI with badges on elements |

---

## Triage (Existing SVGs)

**All existing SVGs need REGENERATION** (not patching) because:
- They have User Stories sections to remove
- They have annotation containers to remove
- They have verbose legends to simplify
- Layout fundamentally changes

**Process:**
1. Run `/wireframe NNN`
2. Show pre-flight
3. User confirms
4. Generate fresh SVG
5. Old SVG overwritten

---

## File Watermark

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- ============================================================
     GENERATED BY: /wireframe skill v3.0 (Lean UI Mockups)
     SOURCE: features/[category]/[feature]/spec.md
     DATE: [YYYY-MM-DD]
     THEME: [Light/Dark]
     ============================================================ -->
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1920 1080" width="1920" height="1080">
```

**The `width="1920" height="1080"` attributes are REQUIRED for the viewer to render the SVG.**
