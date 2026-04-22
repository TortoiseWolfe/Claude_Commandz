---
description: After-action session analysis — find corrections, friction, and memory gaps
---

# Debrief

Analyze the most recent Claude Code session log to identify what went wrong and what to remember for next time.

## Usage

```
/debrief              # Analyze most recent session
/debrief <path>       # Analyze a specific JSONL file
```

## Instructions

### 1. Find the session log

If no path argument provided, find the most recent JSONL:

```bash
python3 scripts/debrief_session.py --latest
```

If a path argument is provided:

```bash
python3 scripts/debrief_session.py "$ARGUMENTS"
```

### 2. Present findings

Read the JSON output and present it in developer voice. Group by category:

**Format:**

```
Session debrief: <session-id>
Duration: ~X min | User turns: Y | Corrections: Z

## Corrections received
1. [line N] "user quote truncated..." — what I got wrong
   → Memory: "proposed MEMORY.md entry"

## Workflow friction
- N tool denials
- N verbose responses (500+ words before user could respond)
- Repeated reads: file.md (Nx), other.md (Nx)

## Redundant work
- Duplicate tool calls: Read same-file 4x
- Manual work that /mercor-quotes could have handled

## Memory gaps
- [line N] User asked to remember X — check if already in MEMORY.md
```

Skip any category that has zero findings. Don't pad empty sections.

### 3. Propose memory updates

For each correction and memory gap, draft a concise MEMORY.md entry. Read the current MEMORY.md first to avoid duplicates:

```
Read ~/.claude/projects/-home-TurtleWolfe-repos-good-prompt-bad-prompt/memory/MEMORY.md
```

Present proposed additions:

```
## Proposed memory updates

1. "Don't treat ambiguous form copy as authoritative rules"
2. "When drafting for Slack forms, use flat developer voice"

Apply all / select which ones / skip?
```

### 4. Apply updates

If the user confirms, use the Edit tool to add entries to the appropriate section of MEMORY.md. Group related entries under existing headers when possible. Create a new header only if nothing fits.

### 5. Done

```
Debrief complete. N corrections, N friction points, N memory updates applied.
```

## DO NOT

- Summarize the whole session (we were there, we know what happened)
- Include corrections the user already addressed during the session
- Add memory entries that duplicate what's already in MEMORY.md
- Over-explain findings. One line per item, quote + what went wrong
