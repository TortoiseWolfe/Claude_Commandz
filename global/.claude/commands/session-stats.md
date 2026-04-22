---
description: Display session statistics including token usage and costs
---

# Session Stats

Show current session statistics including message count, token usage estimates, and approximate costs.

## Usage

```
/session-stats              # Display current session statistics
/session-stats --export     # Export stats to session-log.json
```

**Examples**:
```
/session-stats              # Show stats dashboard
/session-stats --export     # Save to file for analysis
```

## Arguments

ARGUMENTS: $ARGUMENTS

- No args: Display current session statistics
- `--export`: Save stats to `docs/interoffice/session-logs/[date]-[terminal].json`

---

## Instructions

### 1. Identify Terminal Role

Determine which terminal is running this command from context (e.g., "You are the Toolsmith terminal").

### 2. Gather Session Metrics

Estimate the following metrics for the current conversation:

| Metric | How to Estimate |
|--------|-----------------|
| Messages | Count user/assistant message pairs |
| Input tokens | ~4 chars per token, sum all user messages + system prompts |
| Output tokens | ~4 chars per token, sum all assistant responses |
| Tool calls | Count tool invocations |
| Files read | Count unique Read tool uses |
| Files written | Count Write/Edit tool uses |
| Duration | Time since session start (if available) |

**Note**: These are estimates. Actual token counts may vary.

### 3. Calculate Cost Estimates

Use current Claude pricing (as of 2025):

| Model | Input | Output |
|-------|-------|--------|
| Claude Opus 4.5 | $15/1M tokens | $75/1M tokens |
| Claude Sonnet | $3/1M tokens | $15/1M tokens |
| Claude Haiku | $0.25/1M tokens | $1.25/1M tokens |

Calculate:
```
input_cost = (input_tokens / 1,000,000) * input_rate
output_cost = (output_tokens / 1,000,000) * output_rate
total_cost = input_cost + output_cost
```

### 4. Display Stats Dashboard

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Session Statistics                                           [Terminal]     │
├─────────────────────────────────────────────────────────────────────────────┤
│ Messages                                                                    │
│   User messages:     [N]                                                    │
│   Assistant turns:   [N]                                                    │
│   Tool calls:        [N]                                                    │
│                                                                             │
│ Token Usage (estimated)                                                     │
│   Input tokens:      ~[N] (~$[X.XX])                                        │
│   Output tokens:     ~[N] (~$[X.XX])                                        │
│   Total:             ~[N] (~$[X.XX])                                        │
│                                                                             │
│ File Operations                                                             │
│   Files read:        [N]                                                    │
│   Files written:     [N]                                                    │
│   Edits made:        [N]                                                    │
│                                                                             │
│ Session Info                                                                │
│   Model:             [claude-opus-4-5-20251101]                             │
│   Started:           [timestamp if available]                               │
│   Working dir:       [path]                                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ Cost breakdown assumes Claude Opus 4.5 pricing                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5. Export Stats (--export)

If `--export` flag is provided:

Create directory if needed:
```bash
mkdir -p docs/interoffice/session-logs
```

Generate filename: `YYYY-MM-DD-[terminal]-[HHmm].json`

Write JSON:
```json
{
  "date": "2026-01-14",
  "terminal": "[terminal]",
  "model": "claude-opus-4-5-20251101",
  "workingDir": "/path/to/repo",
  "metrics": {
    "userMessages": N,
    "assistantTurns": N,
    "toolCalls": N,
    "filesRead": N,
    "filesWritten": N,
    "edits": N
  },
  "tokens": {
    "inputEstimate": N,
    "outputEstimate": N,
    "total": N
  },
  "costEstimate": {
    "input": 0.00,
    "output": 0.00,
    "total": 0.00,
    "currency": "USD"
  },
  "notes": "[optional user notes]"
}
```

Output:
```
┌─────────────────────────────────────────────────┐
│ Stats Exported                                  │
├─────────────────────────────────────────────────┤
│ File: docs/interoffice/session-logs/            │
│       2026-01-14-toolsmith-1530.json            │
└─────────────────────────────────────────────────┘
```

---

## Limitations

- Token counts are **estimates** (actual billing may differ)
- Cannot track tokens from previous sessions
- Context window usage not directly measurable
- Costs assume list prices (may differ with volume discounts)

---

## Related Skills

- `/status` - Project-wide health check
- `/session-summary` - Generate continuation prompt for next session
