# Broadcast Announcement

Send an announcement to all terminals.

**Access**: Council members only (CTO, Architect, Security Lead, Toolsmith, DevOps)

## Usage

```
/broadcast [title]
```

**Examples**:
```
/broadcast "Wireframe v5 Released"
/broadcast "New Skill: /memo Available"
/broadcast "RFC-003 Decided: Payment Provider"
```

## Purpose

Broadcasts are for announcements that all terminals should be aware of:
- New tool/skill releases
- Important workflow changes
- RFC decisions that affect everyone
- Process updates
- Urgent notices

## Instructions

### 1. Verify Council Membership

This skill is for council members only. If the terminal is not one of:
- CTO
- Architect
- Security Lead
- Toolsmith
- DevOps

Output error:
> "Error: Only council members can send broadcasts."

### 2. Identify Sender

Ask which council member is sending the broadcast (or infer from context).

### 3. Get Broadcast Content

Ask for:
1. **Category**: announcement | decision | urgent | release
2. **Summary**: Brief description (1-2 sentences)
3. **Details**: Full content of the announcement
4. **Action Required**: What terminals should do (if anything)

### 4. Create Broadcast File

Create `docs/interoffice/broadcast/YYYY-MM-DD-[slug].md`:

```markdown
# Broadcast: [Title]

**Date**: [Today's Date]
**From**: [Sender]
**Category**: [announcement|decision|urgent|release]

## Summary

[Brief summary]

## Details

[Full announcement content]

## Action Required

[What terminals should do, or "No action required - for awareness only."]

---

*This broadcast will be shown to all terminals on their next /prep.*
```

### 5. Output Confirmation

```
Broadcast sent: [Title]

File: docs/interoffice/broadcast/[filename]
Category: [category]
From: [sender]

All terminals will see this announcement on their next /prep.
```

## Broadcast Categories

| Category | Use For |
|----------|---------|
| announcement | General updates, new features |
| decision | RFC outcomes, policy changes |
| urgent | Critical issues requiring immediate attention |
| release | New tool/skill/version releases |

## Cleanup

Broadcasts should be archived/deleted after a reasonable time (1-2 weeks). The Coordinator terminal is responsible for cleanup.

To archive a broadcast:
```bash
mv docs/interoffice/broadcast/YYYY-MM-DD-title.md docs/interoffice/broadcast/archive/
```

ARGUMENTS: $ARGUMENTS
