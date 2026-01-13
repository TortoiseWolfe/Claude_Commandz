---
description: Generate clean UI wireframes with numbered callouts (v5)
---

# /wireframe v5 - Clean UI Mockups

**Philosophy:** Wireframes preview the app. Callouts explain, not clutter.

---

## Layout (1920×1080 canvas)

```
┌────────────────────────────────────────────────────────────────────┐
│             CENTERED TITLE - HUMAN READABLE                (y=28)  │
│ DESKTOP (16:9)                                    MOBILE    (y=52) │
├────────────────────────────────────────┬───────────────────────────┤
│                                        │                           │
│   DESKTOP MOCKUP (1280×720)            │   MOBILE MOCKUP (360×720) │
│   x=40, y=60                           │   x=1360, y=60            │
│                                        │                           │
│   Clean UI with numbered callouts      │   Same callout numbers    │
│   ① ② ③ on EVERY annotation concept   │   positioned appropriately│
│                                        │                           │
│   NO FR/SC badges on UI elements       │                           │
│                                        │                           │
├────────────────────────────────────────┴───────────────────────────┤
│ ANNOTATION PANEL (y=800, full width, height=220 max)               │
│                                                                    │
│ ① Primary action - Description of what this element does.         │
│   [FR-001] [SC-001] ← colored clickable pills                      │
│                                                                    │
│ ② Secondary element - Brief explanation of functionality.         │
│   [FR-002] [FR-003]                                                │
│                                                                    │
│ ③ Tertiary feature - What it does and why it matters.             │
│   [US-001] [SC-002]                                                │
│                                                                    │
│ NO Legend/Coverage/Integration rows - they add no value            │
│                                                                    │
├────────────────────────────────────────────────────────────────────┤
│ SIGNATURE: NNN:NN | Feature Name | ScriptHammer   (y=1060, 18px)   │
└────────────────────────────────────────────────────────────────────┘
```

### Title Format (CRITICAL)

```xml
<!-- CENTERED, HUMAN-READABLE - no pipe, no page count -->
<text x="700" y="28" text-anchor="middle" font-family="system-ui, sans-serif"
      font-size="18" font-weight="700" fill="#4b5563" letter-spacing="1">
  COOKIE CONSENT - MODAL FLOW
</text>

<!-- Section labels below title -->
<text x="40" y="52" fill="#8b5cf6" font-family="system-ui, sans-serif"
      font-size="18" font-weight="bold">DESKTOP (16:9)</text>
<text x="1500" y="52" fill="#d946ef" font-family="system-ui, sans-serif"
      font-size="15" font-weight="bold">MOBILE</text>
```

**NEVER use:** `NNN-feature | Page X of Y` - this is wrong format

---

## Callout System

### On mockups: Numbered circles only

```xml
<!-- Red circle with white number -->
<g class="callout" transform="translate(150, 200)">
  <circle r="14" fill="#dc2626"/>
  <text y="5" fill="#fff" font-size="14" font-weight="bold" text-anchor="middle">1</text>
</g>
```

### In annotation panel: US-anchored groups with badge pills

```xml
<g transform="translate(60, 820)">
  <!-- Callout number -->
  <circle cx="14" cy="14" r="14" fill="#dc2626"/>
  <text x="14" y="19" fill="#fff" font-size="14" font-weight="bold" text-anchor="middle">1</text>

  <!-- US title (from spec.md User Story name) -->
  <text x="40" y="14" fill="#374151" font-size="16" font-weight="bold">[User Story Title]</text>

  <!-- US narrative (from spec.md "As a...") -->
  <text x="40" y="34" fill="#4b5563" font-size="14">As a [user type], I need [goal] so that [benefit].</text>

  <!-- Badge pills: US FIRST (cyan), then FR (blue), SC (orange) -->
  <a href="#US-001">
    <rect x="40" y="44" width="55" height="22" rx="4" fill="#0891b2"/>
    <text x="67" y="59" fill="#fff" font-size="12" text-anchor="middle">US-001</text>
  </a>
  <a href="#FR-001">
    <rect x="101" y="44" width="55" height="22" rx="4" fill="#2563eb"/>
    <text x="128" y="59" fill="#fff" font-size="12" text-anchor="middle">FR-001</text>
  </a>
  <a href="#FR-003">
    <rect x="162" y="44" width="55" height="22" rx="4" fill="#2563eb"/>
    <text x="189" y="59" fill="#fff" font-size="12" text-anchor="middle">FR-003</text>
  </a>
  <a href="#SC-001">
    <rect x="223" y="44" width="55" height="22" rx="4" fill="#ea580c"/>
    <text x="250" y="59" fill="#fff" font-size="12" text-anchor="middle">SC-001</text>
  </a>
</g>
```

---

## Annotation Panel Layout

### Column Grid (1840px panel, 20px padding)

Use 4 columns × 450px each to prevent overlap:

| Column | X Position | Width |
|--------|------------|-------|
| 1 | x=20 | 450px |
| 2 | x=470 | 450px |
| 3 | x=920 | 450px |
| 4 | x=1370 | 450px |

### Row Heights

| Row | Y Position | Content |
|-----|------------|---------|
| Row 1 | y=20 | Numbered callouts (①②③④) |
| Row 2 | y=90 | Cross-cutting concerns or additional groups |
| Row 3 | y=160 | More callout groups if needed (⑤⑥⑦⑧) |

Max 3 rows = 210px content + 20px padding = 230px panel height.
**Leave 40px gap before signature at y=1060.**

### What NOT to Include in Annotation Panel

- **Legend row** - Badge colors are self-explanatory (blue=FR, orange=SC, cyan=US)
- **Coverage row** - Internal tracking ("24/24 FRs") doesn't belong in wireframe
- **Integration row** - If it's not visual, don't include it

### Badge Layout Rules

| Badge Count | Layout |
|-------------|--------|
| 1-5 | Single row, 66px spacing (60px pill + 6px gap) |
| 6-8 | Two rows of badges |
| 9+ | Split across two annotation groups |

**Max badges per 450px column:** 6 in single row, 12 in two rows.

### Badge Containment (G-036)

**Badges must stay WITHIN their container boundaries:**

| Container | Right Edge Limit |
|-----------|------------------|
| Desktop mockup | x=1320 (40 + 1280) |
| Mobile mockup | x=1720 (1360 + 360) |
| Annotation panel | x=1880 (40 + 1840) |

**If badges might overflow:**
- Use smaller font (11px instead of 12px)
- Abbreviate text (e.g., "US-001" not "User Story 001")
- Wrap to second row earlier
- Never let `badge_x + badge_width` exceed container right edge

### Annotation Text Styling (G-037)

**Annotation titles MUST be readable:**

| Element | Font Size | Weight | Color |
|---------|-----------|--------|-------|
| US Title (①②③ lines) | 14px | **bold** | #1f2937 |
| US Narrative | 14px | regular | #374151 |
| Badge text | 11-12px | regular | #ffffff |

**NEVER use these on annotation text:**
- `#9ca3af`, `#d1d5db`, `#e5e7eb` (too light, hard to read)
- `#6b7280` (toggle grey - wrong context)
- Font sizes below 14px for narrative text

### User Story Anchoring (Required)

**Each annotation group MUST be anchored by a User Story from spec.md.**

User Stories provide the narrative context ("As a [user], I need [goal]...") that makes wireframes meaningful. FR/SC codes are supporting evidence.

| Group | Anchor | Supporting |
|-------|--------|------------|
| ① | US-001 | FR-001, FR-003, SC-001 |
| ② | US-002 | FR-005, FR-006, SC-002 |
| ... | ... | ... |

**Structure per annotation group:**
1. **Callout number** - Red circle with number
2. **US title** - User Story name from spec (bold, 16px)
3. **US narrative** - "As a [user], I need [goal]..." (14px, muted)
4. **Badge pills** - US badge FIRST (cyan), then FR (blue), SC (orange)

**Mapping strategy:**
- Read spec.md `### User Story N` sections
- Each US becomes an annotation group anchor
- FR/SC codes that fulfill the US go in that group
- Cross-cutting concerns (accessibility, performance) can form their own group anchored by the most relevant US

**Badge colors:**
- US badges: cyan (`#0891b2`) - ALWAYS FIRST
- FR badges: blue (`#2563eb`)
- SC badges: orange (`#ea580c`)

---

## Rules (30 total)

| # | Rule |
|---|------|
| 1 | Canvas: `viewBox="0 0 1920 1080" width="1920" height="1080"` |
| 2 | Desktop mockup: x=40, y=60, 1280×720 |
| 3 | Mobile mockup: x=1360, y=60, 360×720 |
| 4 | Callouts: red circles (①②③) on elements needing explanation |
| 5 | Annotation panel: y=800, spans full width |
| 6 | FR/SC/US as clickable colored badge pills (blue/orange/cyan) |
| 7 | Light theme: panels #e8d4b8, NO #ffffff |
| 8 | Toggle colors: OFF=#6b7280, ON=#22c55e |
| 9 | Touch targets: 44px minimum |
| 10 | **Minimum font size: 14px** (validator enforced) |
| 11 | **Mobile frame: light colors only** (NO #1f2937 dark) |
| 12 | **Headers: use templates from includes/** (eye, gear, hamburger icons) |
| 13 | **Decorative dividers: single color only** (no two-tone patterns) |
| 14 | **Annotation groups: 20px vertical gap** between each |
| 15 | **Modal SVG order: footer `<use>` AFTER modal content** (SVG paints in order) |
| 16 | **Canvas background: blue gradient** `#c7ddf5` → `#b8d4f0` (validator: G-022) |
| 17 | **Title block required: y=28, centered** "FEATURE - PAGE NAME" (validator: G-024) |
| 18 | **Signature required: y=1060, 18px bold** "NNN:NN \| Feature \| ScriptHammer" (validator: G-025) |
| 19 | **Numbered callouts ON mockups** ①②③④ red circles on UI elements (validator: G-026) |
| 20 | **Badges near, not on text** place on solid color areas, never blocking what they highlight |
| 21 | **No REQUIREMENTS KEY section** move definitions into callout explanations |
| 22 | **Annotation column distribution** 4 groups = 4 columns (x=20, 470, 920, 1370), NO clustering |
| 23 | **Callout placement** RIGHT or BELOW element (supportive), NEVER left/above (leading) |
| 24 | **Desktop content centering** 2 panels=600px each, 3 panels=400px each, use full 1280px |
| 25 | **Callout Y-alignment** same-row callouts share Y coordinate; place RIGHT of elements |
| 26 | **Mobile content safe area** y >= 78 (after header), y < 664 (before footer) (validator: MOBILE-001, G-034) |
| 27 | **Button colors: NO parchment** use #8b5cf6, #f5f0e6, #dcc8a8 only, NEVER #e8d4b8 (validator: BTN-001, G-035) |
| 28 | **Badge containment** badges must stay within container bounds, no overflow (validator: G-036) |
| 29 | **Annotation readability** titles 14px bold (#1f2937), narrative 14px regular (#374151) (validator: G-037) |
| 30 | **Navigation active state** Desktop nav + mobile footer must show current page highlighted (#8b5cf6 fill, white text/icon) (G-039) |

---

## Workflow

### Step 0: READ VALIDATOR (Required)

Before planning the SVG, read the validator to refresh on all checks:

```bash
cat docs/design/wireframes/validate-wireframe.py
```

Pay attention to:
- `_check_*()` methods - each is a validation rule
- `ALLOWED_COLORS` and `FORBIDDEN_*` constants
- Error codes (SVG-001, G-001, TITLE-001, etc.)

**Critical checks to watch (G-034 through G-037):**
- `MOBILE-001` / G-034: Mobile content y >= 78 (after header safe area)
- `BTN-001` / G-035: No buttons using #e8d4b8 or transparent fills
- `G-036`: All badges within container bounds (no overflow)
- `G-037`: Annotation titles must be bold, text colors must be dark

Also check for issues file if regenerating:

```bash
cat docs/design/wireframes/[feature]/[svg-name].issues.md
```

Process issues by classification:
- **PATCHABLE** items → Apply fixes during generation
- **REGENERATE** items → Incorporate feedback into new layout
- **COVERAGE** items → Add missing FR/SC groups to annotations

### Issues Files Policy (CRITICAL)

**NEVER delete `.issues.md` files.** They serve as historical documentation:
- Record of review findings and their resolution
- Audit trail for wireframe quality improvements
- Reference for recurring patterns that inform GENERAL_ISSUES.md

When regenerating wireframes:
1. READ the issues file to understand what was wrong
2. ADDRESS the issues in the new SVG
3. KEEP the issues file (optionally add "Resolved: [date]" notes)

### Step 1: PRE-FLIGHT

First, run theme analysis to classify User Stories:

```bash
python3 docs/design/wireframes/validate-wireframe.py --analyze-themes [spec.md]
```

This outputs JSON showing each US as **light** (UX wireframe) or **dark** (backend diagram).
Use this to determine which SVGs to generate.

Then output the PRE-FLIGHT plan:

```
═══════════════════════════════════════════════════════════════
PRE-FLIGHT: [NNN]-[feature] page [NN]
═══════════════════════════════════════════════════════════════

TITLE: [HUMAN READABLE TITLE]
THEME: [light/dark] (from --analyze-themes)

USER STORIES (from spec.md):
- US-001: [title] (P0) → Group ①
- US-002: [title] (P1) → Group ②
- US-003: [title] (P1) → Group ③
- ...

SCREENS TO SHOW:
- Desktop: [description]
- Mobile: [description]

ANNOTATION GROUPS (US-anchored):
① [US-001 title] - [narrative summary]
   [US-001] [FR-XXX] [FR-XXX] [SC-XXX]
② [US-002 title] - [narrative summary]
   [US-002] [FR-XXX] [SC-XXX]
③ [US-003 title] - [narrative summary]
   [US-003] [FR-XXX] [FR-XXX]
...

BLOCKING CHECKS:
[ ] All User Stories from spec.md mapped to groups
[ ] No FR/SC badges on UI elements (only in annotation panel)
[ ] Annotation panel fits (max 8 groups across 2 rows)
[ ] Issues file checked (if exists)
═══════════════════════════════════════════════════════════════
```

**Wait for user approval before generating.**

### Step 2: GENERATE

Write SVG following the layout and rules above.

### Step 3: VALIDATE (MANDATORY)

```bash
python docs/design/wireframes/validate-wireframe.py [file.svg]
```

**MUST show `STATUS: PASS` before done.**

- If validator shows errors → FIX THEM. Do not proceed.
- If you think a check is wrong → STOP and report to user. Do not bypass.
- **NEVER say "errors are false positives" or "validator limitations"** - either fix the SVG or fix the validator.
- No exceptions. No excuses. PASS or fix.

### Step 4: VIEWER SYNC

```
═══════════════════════════════════════════
VIEWER SYNC
═══════════════════════════════════════════
SVG written: docs/design/wireframes/[feature]/[file].svg

→ Hard-refresh browser: Ctrl+Shift+R
→ Or open: http://localhost:3000/#[feature]/[file].svg
═══════════════════════════════════════════
```

### Step 5: REGISTER (MANDATORY)

After validation passes, register the wireframe in the viewer. The viewer uses TWO systems:

| System | Purpose | File |
|--------|---------|------|
| `wireframes` array + nav HTML | Actual wireframe navigation | `index.html` |
| `wireframe-status.json` | Status badges (📝 ✅ etc) | JSON |

**Both must be updated for wireframes to appear.**

#### 5A. Update index.html

1. **Read** `docs/design/wireframes/index.html`

2. **Add to `wireframes` array** (find `const wireframes = [`, add after previous feature):
   ```javascript
   // [NNN]-[feature-name]
   { path: '[NNN]-[feature-name]/[NN]-[page].svg', title: '[Title from SVG]' },
   ```

3. **Add nav section** (find appropriate `#cat-*` category, add inside `.category-links`):
   ```html
   <!-- [NNN]-[feature-name] -->
   <div class="nav-section">
     <h2>[NNN] - [Feature Name] <span class="shortcut">^[N]</span></h2>
     <div class="nav-links">
       <a href="#" data-svg="[NNN]-[feature-name]/[NN]-[page].svg">
         [Title]
         <span class="path">[Brief description]</span>
       </a>
     </div>
   </div>
   ```

4. **Write** updated index.html

#### 5B. Update wireframe-status.json

1. **Read** `docs/design/wireframes/wireframe-status.json`

2. **Add/update entry:**
   ```json
   "[NNN]-[feature-name]": {
     "status": "draft",
     "svgs": {
       "[NN]-[page].svg": "draft"
     }
   }
   ```

3. **Write** updated JSON

**Category mapping for nav sections:**

| Feature Range | Category ID | Nav Location |
|---------------|-------------|--------------|
| 000-006 | `#cat-foundation` | Foundation |
| 007-012 | `#cat-core` | Core Features |
| 013-016 | `#cat-auth` | Auth OAuth |
| 017-021 | `#cat-enhancements` | Enhancements |
| 022-026 | `#cat-integrations` | Integrations |
| 027-030 | `#cat-polish` | Polish |
| 031-037 | `#cat-testing` | Testing |
| 038-043 | `#cat-payments` | Payments |
| 044-045 | `#cat-quality` | Code Quality |

---

## Color Palette (Light Theme)

| Element | Color |
|---------|-------|
| Background | `#c7ddf5` → `#b8d4f0` gradient |
| Panels | `#e8d4b8` (parchment) |
| Secondary | `#dcc8a8` |
| Inputs | `#f5f0e6` |
| Borders | `#b8a080` |
| Primary button | `#8b5cf6` |
| Callout circles | `#dc2626` |
| Text dark | `#374151` |
| Text muted | `#4b5563` |
| **Badge FR** | `#2563eb` (blue) |
| **Badge SC** | `#ea580c` (orange) |
| **Badge US** | `#0891b2` (cyan) |

**NEVER use #ffffff for panels.**

**NEVER use on buttons (G-035):**
- `#e8d4b8` (panel parchment) - blends with background, looks disabled
- `fill="none"` or `transparent` - buttons must have solid fills

**Valid button fills ONLY:**
- Primary: `#8b5cf6` (violet)
- Secondary: `#f5f0e6` (cream)
- Tertiary: `#dcc8a8` (tan) - distinct from panel bg

---

## What NOT to Include

| Old Pattern | Why Removed |
|-------------|-------------|
| FR/SC badges on UI elements | Creates visual clutter |
| Violet annotation containers | Confuses annotations with UI |
| 30-word legend definitions | Now in annotation panel |
| Separate User Stories section | US badges go in annotation groups |
| "Accessibility" panel | FR codes in annotations |
| "Performance" panel | SC codes in annotations |

---

## Includes (Runtime Resolution)

**NEVER embed header/footer content.** Use `<use>` references - the viewer resolves them at runtime.

### Include References

```xml
<use href="includes/header-desktop.svg#desktop-header" x="0" y="0"/>
<use href="includes/footer-desktop.svg#site-footer" x="0" y="640"/>
<use href="includes/header-mobile.svg#mobile-header-group" x="0" y="0"/>
<use href="includes/footer-mobile.svg#mobile-bottom-nav" x="0" y="664"/>
```

**Benefits:**
- Edit `includes/*.svg` once → ALL wireframes update automatically
- Smaller SVG files (no embedded content)
- Fork-friendly: rebrand includes, all wireframes reflect changes

### Step 6: QUEUE FOR REVIEW (MANDATORY)

After registration, queue the feature for Reviewer terminal to pick up:

**Read** `.terminal-status.json`:
```bash
cat docs/design/wireframes/.terminal-status.json | jq '.'
```

**Update queue** with REVIEW action for this feature:
```json
{
  "feature": "[NNN]-[feature-name]",
  "action": "REVIEW",
  "reason": "Generator completed [N] SVG(s) - ready for review",
  "assignedTo": "reviewer"
}
```

**Remove GENERATE items** for this feature from queue (they're done).

**Add completion entry** to `completedToday`:
```
"Generator-N: Completed [NNN]-[feature-name] ([N] SVGs: [list])"
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
REVIEW QUEUED
═══════════════════════════════════════════
Feature: [NNN]-[feature-name]
SVGs: [list]
Action: REVIEW assigned to reviewer

Reviewer can pick up with /next or /wireframe-screenshots
═══════════════════════════════════════════
```

---

## SVG Templates

Load the template matching your `--analyze-themes` result:

```bash
# Light theme (UX wireframes - forms, dashboards, user screens)
cat docs/design/wireframes/templates/light-theme.svg

# Dark theme (backend diagrams - RLS, OAuth, CSRF, CI/CD)
cat docs/design/wireframes/templates/dark-theme.svg
```

See `templates/README.md` for color palettes, diagram patterns, and element references.
