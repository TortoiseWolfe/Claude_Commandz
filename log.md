---
description: Persist findings to central log (prevents ephemeral output)
scope: personal
---

Append a log entry to the daily interoffice log, optionally notifying other terminals.

## Arguments

- `$ARGUMENTS` - The content to log, optionally with `--notify [terminal]` flag

## Instructions

### 1. Parse Arguments

Check if `--notify [terminal]` is present:
- `--notify toolsmith` → also add memo to `docs/interoffice/memos/to-toolsmith.md`
- `--notify architect` → also add memo to `docs/interoffice/memos/to-architect.md`
- etc.

Valid notify targets: cto, architect, coordinator, security-lead, toolsmith, devops, product-owner

### 2. Determine log file path
```
docs/interoffice/logs/YYYY-MM-DD.md
```
Use today's date.

### 3. Create directories if needed
```bash
mkdir -p docs/interoffice/logs
```

### 4. Get terminal identity from conversation context (e.g., "CTO", "Architect")
- If unknown, use "Unknown"

### 5. Append entry with timestamp
```markdown
## [HH:MM] [Terminal]

[content]

---
```

### 6. If --notify flag present, also add memo

Append to `docs/interoffice/memos/to-[target].md`:
```markdown
## YYYY-MM-DD HH:MM - From: [Terminal]
**Priority**: normal
**Re**: Log entry notification

[First 100 chars of content]...

**Full log**: `docs/interoffice/logs/YYYY-MM-DD.md`

---
```

### 7. Confirm
> "Logged to docs/interoffice/logs/YYYY-MM-DD.md"
> (if notified) "Memo sent to [target]'s inbox"

## Examples

**Simple log:**
```
/log Completed RFC-003 implementation review. Found 5 pending tasks.
```

**Log with notification:**
```
/log Found systemic issues in wireframe skill defaults --notify toolsmith
```
→ Logs entry AND adds memo to Toolsmith's inbox

## Use Cases

- Quick persistence of analysis results
- Recording decisions without full memo
- Session progress checkpoints
- Action item tracking with automatic forwarding

## DO NOT

- Skip creating the file if it doesn't exist
- Overwrite existing content (always append)
- Log trivial status updates (use for meaningful findings only)
- Notify without good reason (don't spam inboxes)
