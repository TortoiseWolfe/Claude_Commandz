# Mercor Commands

Slash commands for the Mercor A/B evaluation workflow.

## Command structure

Each `.md` file here becomes a `/command-name` slash command.

```yaml
---
description: One-line description shown in command list
---

# Command Title

Instructions for Claude to follow when this command is invoked.
```

## Current commands

| Command | Purpose |
|---------|---------|
| mercor-help | List all commands |
| mercor-clean | Clean sandbox for next batch |
| mercor-tmux | Launch or attach to the 4-terminal tmux eval session |
| mercor-eval-start | Initialize eval session file with prompt/checklist |
| mercor-prompt-check | Validate starting prompt complexity |
| mercor-status | Check turns/time mid-session |
| mercor-checklist | Full requirements checklist |
| mercor-log | Log observation for a specific model (maintains isolation) |
| mercor-quote | Log behavioral issue quote (15-20+ words required) |
| mercor-quotes | Find quote candidates from session logs |
| mercor-logs | Generate readable transcripts from session logs |
| mercor-classify | Classify behavioral issue and draft description |
| mercor-diff | Compare model_a vs model_b |
| mercor-salvage | Generate salvage plan to cherry-pick best model outputs into upstream repo |
| mercor-followup | Generate a meaningful follow-up prompt for the active model |
| mercor-feedback | Write comparative AirTable comments |
| mercor-rating-check | Verify rating/feedback alignment |
| mercor-validate | Validate submission before sending |
| mercor-eval-end | Synthesize session and verify requirements |
| mercor-submit | Generate AirTable submission |
| mercor-sprint-doc | Compare new sprint doc against existing, archive old, move new to docs/ |
| mercor-review | Review submissions (others, re-review, or self) |
| prime-eval | Prime terminal for Evaluator role (standard evals) |
| prime-eval-oneshot | Prime terminal for Evaluator role (one-shot evals) |
| prime-monitor | Prime terminal for Monitor role (standard evals) |
| prime-monitor-oneshot | Prime terminal for Monitor role (one-shot evals) |
| prime-qa | Prime terminal for QA role (standard evals) |
| prime-qa-oneshot | Prime terminal for QA role (one-shot evals) |
| prime-analyst | Prime terminal for Analyst role (standard evals) |
| prime-analyst-oneshot | Prime terminal for Analyst role (one-shot evals) |
| prime-eval-b | Prime terminal for Evaluator role (Track B standard evals) |
| prime-monitor-b | Prime terminal for Monitor role (Track B standard evals) |
| prime-qa-b | Prime terminal for QA role (Track B standard evals) |
| prime-analyst-b | Prime terminal for Analyst role (Track B standard evals) |
| debrief | After-action session analysis — corrections, friction, memory gaps |

## Writing style

Keep instructions clear and actionable. Use bash code blocks for commands Claude should run. Include output format examples so Claude knows what to produce.
