---
description: Prepare context for wireframe work (patch or new)
---

Load context and prime for wireframe generation.

## Arguments

```text
$ARGUMENTS
```

- No args: Prep for patching (reads issues)
- Feature number (e.g., `002`): Prep for specific feature (reads spec)

## Instructions

### Step 1: Read these files (MANDATORY)
1. `docs/design/wireframes/validate-wireframe.py` - understand ALL checks
2. `docs/design/wireframes/GENERAL_ISSUES.md` - avoid known mistakes
3. `~/.claude/commands/wireframe.md` - follow v5 rules exactly

### Step 2: Feature-specific reads
**If no arguments (patching mode):**
- Find and read all `docs/design/wireframes/**/*.issues.md`

**If feature number provided:**
- Read `features/*/[NNN]-*/spec.md`
- Read any existing `docs/design/wireframes/[NNN]-*/*.issues.md`

### Step 3: Check for escalation candidates (EXECUTE THIS)

Use the Bash tool to run:

```bash
python3 docs/design/wireframes/validate-wireframe.py --check-escalation
```

If candidates are found, add them to GENERAL_ISSUES.md before generating.

### Step 4: Output this priming block

```
═══════════════════════════════════════════════════════════════
WIREFRAME GENERATION PRIMED
═══════════════════════════════════════════════════════════════

NON-NEGOTIABLE:
- Validator MUST show STATUS: PASS before done
- If errors → FIX THEM. No excuses.
- NEVER say "false positives" or "validator limitations"

CRITICAL RULES (validator-enforced):
- Canvas: blue gradient #c7ddf5 → #b8d4f0
- Title: centered at y=28, text-anchor="middle"
- Signature: y=1060, 18px bold
- Numbered callouts ①②③④ ON mockup UI elements
- Use <use href="includes/..."/> for headers/footers
- Minimum font: 14px everywhere
- Modal overlay: dark (#000 with opacity)
- Footer <use> AFTER modal content

ISSUE WORKFLOW:
1. Validator auto-logs issues to feature/*.issues.md
2. Issues escalate to GENERAL_ISSUES.md when seen in 2+ features
3. Run --check-escalation periodically to find candidates

WORKFLOW: PRE-FLIGHT → Generate → Validate → PASS or fix

Feature: [NNN-name or "patch mode"]
Ready for /wireframe command.
═══════════════════════════════════════════════════════════════
```

DO NOT summarize file contents. Output the priming block exactly.
