---
description: Take standardized screenshots of wireframe SVGs for review
---

# /wireframe-screenshots - Automated Wireframe Review Screenshots

Takes 6 screenshots per SVG wireframe (overview + 5 quadrants) for the Reviewer terminal workflow.

## Usage Modes

```bash
/wireframe-screenshots --all              # Process all features with SVGs
/wireframe-screenshots --feature 002      # Process single feature
/wireframe-screenshots --svg 002:01       # Process single SVG
/wireframe-screenshots --batch 3          # Process 3 features at a time
```

## Instructions

### Step 1: Parse Arguments

Determine the mode from user input:
- `--all` → Process all features
- `--feature NNN` → Process single feature (e.g., 002, 002-cookie-consent)
- `--svg NNN:NN` → Process single SVG (e.g., 002:01)
- `--batch N` → Process N features at a time (default: all)

### Step 2: Discover Wireframes

Find all features with SVG wireframes:

```bash
find docs/design/wireframes -maxdepth 2 -name "*.svg" -not -path "*/includes/*" | sort
```

**Current features with wireframes (12 SVGs total):**
| Feature | SVGs |
|---------|------|
| 000-landing-page | 1 |
| 000-rls-implementation | 2 |
| 001-wcag-aa-compliance | 3 |
| 002-cookie-consent | 2 |
| 004-mobile-first-design | 2 |
| 005-security-hardening | 2 |

**Note:** 003-user-authentication has no SVGs (issues files only).

### Step 3: Display Processing Plan

Output the plan before executing:

```
═══════════════════════════════════════════════════════════════
WIREFRAME SCREENSHOTS
═══════════════════════════════════════════════════════════════

Mode: [--all | --feature NNN | --svg NNN:NN]

Features to process:
  ☐ 000-landing-page (1 SVG)
  ☐ 001-wcag-aa-compliance (3 SVGs)
  ☐ 002-cookie-consent (2 SVGs)
  ...

Total: X features, Y SVGs
Output: docs/design/wireframes/png/

═══════════════════════════════════════════════════════════════
```

### Step 4: Execute Screenshot Tool

Run Docker command for each feature:

```bash
cd docs/design/wireframes && docker compose run --rm review [FEATURE_ARG]
```

**Examples:**
```bash
# Single feature
docker compose run --rm review 002

# Single SVG
docker compose run --rm review 002:01
```

**For batch mode:**
Process features sequentially, reporting progress after each.

### Step 5: Report Results

After processing, display summary:

```
═══════════════════════════════════════════════════════════════
RESULTS
═══════════════════════════════════════════════════════════════

Feature                    SVGs    Screenshots    Errors
─────────────────────────────────────────────────────────────
000-landing-page           1       6              0
001-wcag-aa-compliance     3       18             2
002-cookie-consent         2       12             0
─────────────────────────────────────────────────────────────
TOTAL                      6       36             2

Output location: docs/design/wireframes/png/

To view screenshots:
  open docs/design/wireframes/png/[feature]/[svg-name]/

To review in viewer:
  cd docs/design/wireframes && npm run dev
  # OR
  docker compose up wireframe-viewer

═══════════════════════════════════════════════════════════════
```

## Output Structure

```
docs/design/wireframes/png/
├── 002-cookie-consent/
│   ├── 01-consent-modal-flow/
│   │   ├── overview.png          # Full canvas (fit to view)
│   │   ├── quadrant-center.png   # Center region
│   │   ├── quadrant-tl.png       # Top-left corner
│   │   ├── quadrant-tr.png       # Top-right corner
│   │   ├── quadrant-bl.png       # Bottom-left corner
│   │   ├── quadrant-br.png       # Bottom-right corner
│   │   └── manifest.json         # Paths + validator results
│   └── summary.json              # Feature summary
```

## Manifest Contents

Each `manifest.json` includes:
- `generated`: ISO timestamp
- `svg_file`: Path to source SVG
- `screenshots`: Map of screenshot paths
- `validation`: Validator results (error count + details)

## Integration with Reviewer Workflow

After screenshots are taken:

1. **Reviewer terminal** analyzes screenshots shared by user
2. Documents issues in `docs/design/wireframes/NNN-feature/*.issues.md`
3. Classifies as PATCH (quick fix) or REGENERATE (needs new SVG)
4. **Validator terminal** promotes recurring issues to GENERAL_ISSUES.md
5. **Generator terminal** regenerates SVGs based on feedback

## Prerequisites

- Docker and docker-compose installed
- First run will build the review container (includes Playwright + Chromium)
- Wireframe viewer files present in `docs/design/wireframes/`

## Troubleshooting

**Container build fails:**
```bash
cd docs/design/wireframes && docker compose build review
```

**Permission issues on output files:**
The tool automatically fixes permissions using HOST_UID/HOST_GID environment variables.

**Server port conflict:**
Screenshot tool uses port 8080 internally. Ensure it's not in use.
