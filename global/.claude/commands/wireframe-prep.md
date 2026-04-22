---
description: Prepare context for wireframe work (patch or new)
---

Load context and prime for wireframe generation.

## Arguments

```text
$ARGUMENTS
```

- No args: Prep for patching (reads issues)
- Feature number (e.g., `002`): Prep for specific feature

## Instructions

### Step 1: Load Feature Context (Cached)

**If feature number provided:**

```bash
# Load cached feature context (auto-invalidates on spec.md change)
python3 scripts/feature-context.py [feature] --json
```

This returns structured JSON with:
- `feature_id`, `feature_name`, `category`, `path`
- `wireframes.svgs`, `wireframes.issue_files`
- `dependencies.depends_on`, `dependencies.blocks`
- `spec.title`, `spec.overview`, `spec.user_stories`

**Cache benefits**: Single parse, ~10,000 tokens saved per day.

### Step 2: Get File List

```bash
# For specific feature
python3 docs/design/wireframes/wireframe-prep-loader.py files [feature]

# For patch mode (no feature)
python3 docs/design/wireframes/wireframe-prep-loader.py files
```

### Step 3: Read Mandatory Files

The Python script outputs which files to read. Read them using the Read tool:

1. `docs/design/wireframes/validate-wireframe.py` - understand ALL checks
2. `docs/design/wireframes/GENERAL_ISSUES.md` - avoid known mistakes
3. `~/.claude/commands/wireframe.md` - follow v5 rules exactly

### Step 4: Read Feature-Specific Files

**If feature number provided:**

Use the context from Step 1 to identify files. If needed:

```bash
# Get spec path
python3 docs/design/wireframes/wireframe-prep-loader.py spec [feature]

# Get issues files
python3 docs/design/wireframes/wireframe-prep-loader.py issues [feature]
```

Read the spec.md and any issues.md files found.

**If no arguments (patching mode):**

```bash
# Get all issues files
python3 docs/design/wireframes/wireframe-prep-loader.py issues
```

### Step 5: Run Escalation Check

```bash
python3 docs/design/wireframes/wireframe-prep-loader.py escalation
```

If candidates are found, add them to GENERAL_ISSUES.md before generating.

### Step 6: Output Priming Block

```bash
python3 docs/design/wireframes/wireframe-prep-loader.py prime [feature]
```

Show the output directly. It includes:
- Non-negotiable rules
- Critical validator rules
- Issue workflow reminder
- Feature name and existing SVG count

## Quick Reference

```bash
# Full workflow for feature 002:
python3 scripts/feature-context.py 002 --json          # Cached context
python3 docs/design/wireframes/wireframe-prep-loader.py files 002
python3 docs/design/wireframes/wireframe-prep-loader.py escalation
python3 docs/design/wireframes/wireframe-prep-loader.py prime 002
```

## Cache Management

```bash
# Clear cache for a specific feature
python3 scripts/feature-context.py 002 --clear-cache

# Clear all cached contexts
python3 scripts/feature-context.py --clear-cache

# Force fresh parse (skip cache)
python3 scripts/feature-context.py 002 --no-cache --json
```

## DO NOT

- Summarize file contents after reading them
- Skip reading the mandatory files
- Skip the escalation check
- Modify the priming block output
