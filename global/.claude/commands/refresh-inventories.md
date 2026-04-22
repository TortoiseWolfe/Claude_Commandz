---
description: Regenerate all inventory files from current codebase state
scope: project
---

Scan the codebase and regenerate all inventory files in `.claude/inventories/`.

## When to Run

- After forking the repository
- After adding/removing features
- After adding/removing skills
- After modifying IMPLEMENTATION_ORDER.md
- After adding GitHub workflows

## Inventory Files to Regenerate

| File | Source |
|------|--------|
| `skill-index.md` | `~/.claude/commands/*.md` descriptions |
| `dependency-graph.md` | `features/IMPLEMENTATION_ORDER.md` |
| `workflow-status.md` | `.github/workflows/*.yml` |
| `security-touchpoints.md` | Keyword scan of all specs (auth, security, privacy, etc.) |
| `screen-inventory.md` | `docs/design/wireframes/*/` SVGs |
| `acceptance-criteria.md` | `features/*/*/spec.md` AC sections |

## Instructions

### 1. skill-index.md

Extract descriptions from all skill files:
```bash
cd ~/.claude/commands && for f in *.md; do
  name="${f%.md}"
  desc=$(awk '/^---$/{p=1;next}/^---$/{p=0}p && /^description:/{sub(/^description: */,"");print;exit}' "$f")
  [ -n "$desc" ] && echo "| /$name | $desc |"
done
```

Organize by category (Workflow, SpecKit, Wireframe, Council, etc.)

### 2. dependency-graph.md

Parse `features/IMPLEMENTATION_ORDER.md`:
- Extract tier tables
- Extract dependency blockers
- Extract wave-based approach

### 3. workflow-status.md

Scan `.github/workflows/*.yml`:
- List active workflows with triggers
- List jobs and their purposes
- Note enforcement status

### 4. security-touchpoints.md

**Discovery-based** - scan ALL specs for security keywords:

```bash
# Find security-related features dynamically
grep -rli "auth\|security\|privacy\|RLS\|OWASP\|consent\|password\|session\|token\|encryption" \
  features/*/*/spec.md 2>/dev/null | \
  while read f; do
    feature=$(basename $(dirname "$f"))
    title=$(head -1 "$f" | sed 's/# Feature Specification: //')
    echo "| $feature | $title |"
  done
```

**Keywords scanned**:
- `auth` / `authentication` / `authorization`
- `security` / `secure`
- `privacy` / `GDPR` / `consent`
- `RLS` / `row level security`
- `OWASP` / `vulnerability`
- `password` / `credential` / `session` / `token`
- `encryption` / `hash`

Cross-reference discovered features with OWASP Top 10.

### 5. screen-inventory.md

Scan wireframe directories:
```bash
for dir in docs/design/wireframes/*/; do
  feature=$(basename "$dir")
  count=$(ls "$dir"*.svg 2>/dev/null | wc -l)
  echo "| $feature | $count |"
done
```

### 6. acceptance-criteria.md

Scan feature specs:
```bash
grep -c "Given.*When.*Then" features/*/*/spec.md
```

Count stories by priority (P0, P1, P2).

## Output

After regeneration:
```
Inventory files refreshed:
- skill-index.md: [N] skills
- dependency-graph.md: [N] features, [N] tiers
- workflow-status.md: [N] workflows
- security-touchpoints.md: [N] touchpoints
- screen-inventory.md: [N] SVGs across [M] features
- acceptance-criteria.md: [N] features with AC
```

## Fork Reminder

This skill should be run immediately after forking ScriptHammer to ensure inventory files reflect your project's actual state.
