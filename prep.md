---
description: Prepare to discuss this repository (non-verbose)
scope: personal
---

Read and internalize key project files without summarizing.

## Arguments

- `/prep` - Load root context only
- `/prep [terminal]` - Load root + terminal-specific context

**Valid terminals**: manager, assistant, planner, generator, viewer, reviewer, validator, inspector, author, tester, implementer, auditor

## Instructions

### 1. Always Read Root Context

- `CLAUDE.md` (if exists)
- `.specify/memory/constitution.md` (if exists)

### 2. If Terminal Argument Provided

Load additional context based on terminal:

| Terminal | Additional Context |
|----------|-------------------|
| manager | `.terminal-status.json` queue summary |
| assistant | `.claude/commands/CLAUDE.md` |
| planner | `features/CLAUDE.md` |
| generator | `docs/design/wireframes/CLAUDE.md` + current queue item |
| viewer | `docs/design/wireframes/CLAUDE.md` |
| reviewer | `docs/design/wireframes/CLAUDE.md` + recent issues files |
| validator | `docs/design/wireframes/CLAUDE.md` + `GENERAL_ISSUES.md` |
| inspector | `docs/design/wireframes/CLAUDE.md` + all SVG patterns |
| author | `docs/CLAUDE.md` |
| tester | Root context only (no tests folder yet) |
| implementer | Root context only (no src folder yet) |
| auditor | `.specify/CLAUDE.md` |

### 3. Terminal-Specific Extras

**generator**: Show first queue item assigned to generator
```bash
cat docs/design/wireframes/.terminal-status.json | jq '.queue[] | select(.assignedTo == "generator") | .feature + "/" + .svg' | head -1
```

**reviewer**: List recent issues files (last 5)
```bash
ls -t docs/design/wireframes/*/*.issues.md 2>/dev/null | head -5
```

**validator**: Show escalation candidates count
```bash
python docs/design/wireframes/validate-wireframe.py --check-escalation 2>/dev/null | grep -c "candidate" || echo "0"
```

**inspector**: Show SVG count and last inspection
```bash
ls docs/design/wireframes/*/*.svg 2>/dev/null | wc -l
```

### 4. Output

> "Read [terminal] context. Ready."

Or if no terminal specified:

> "Read project context. Ready."

## DO NOT

- Summarize the repository
- List what you learned
- Create tables or analysis
- Explain the project structure

The user already knows the project. This command loads context silently.
