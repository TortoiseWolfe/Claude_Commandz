---
description: Prime terminal for Evaluator role (Track B)
---

# Evaluator Role - Primed (Track B)

You're helping me (TurtleWolfe) run a Mercor Code Agent A/B evaluation (STANDARD, Track B).

Startup sequence (do these FIRST, in order):
1. Verify tmux is running: tmux has-session -t mercor-b 2>/dev/null || ./scripts/mercor-tmux-b.sh
2. After /mercor-eval-start creates the session file, inject the task ID into all terminals:
     ./scripts/mercor-dispatch-b.sh monitor "TASK_XXXX is the active session"
     ./scripts/mercor-dispatch-b.sh qa "TASK_XXXX is the active session"
     ./scripts/mercor-dispatch-b.sh analyst "TASK_XXXX is the active session. Logs are in ~/repos/sandbox_b/logs/"
3. Dispatch initial status check: ./scripts/mercor-dispatch-b.sh monitor "/mercor-status"

Key files:
- README.md: starting prompts, follow-up rules, checklists
- CLAUDE.md: writing style, proactive commands, delegation patterns
- evals/TASK_XXXX.md: session file (created by /mercor-eval-start)

Tmux session: mercor-b (4 terminals, all primed):
- Window 0 (Eval-B): You're here. Run the session, log observations.
- Window 1 (Monitor-B): Turn counts, time thresholds, pacing checks.
- Window 2 (QA-B): Comment length, rating alignment, LLM-voice detection.
- Window 3 (Analyst-B): Parse JSONL logs, find behavioral issue quotes.

Sandbox: ~/repos/sandbox_b/
Logs: ~/repos/sandbox_b/logs/model_a/ and model_b/

Delegation (ALWAYS use the dispatch script, NEVER raw tmux send-keys):
  Dispatch:  ./scripts/mercor-dispatch-b.sh <role> "<command>"
  Read back: tmux capture-pane -t mercor-b:<window> -p
  Roles: monitor (window 1), qa (window 2), analyst (window 3)

  Examples:
    ./scripts/mercor-dispatch-b.sh monitor "/mercor-status"
    ./scripts/mercor-dispatch-b.sh qa "/mercor-validate"
    ./scripts/mercor-dispatch-b.sh analyst "/mercor-quotes b"

  Delegate: turn counts, time checks, validation, LLM detection, quote searching
  Keep: observations, AirTable drafts, follow-up prompts, session coordination

Proactive commands (run these without being asked):
- /mercor-status every 30 minutes (are we hitting turn/time minimums?)
- /mercor-quote [a|b] [turn] "quote" when you spot a behavioral issue
- /mercor-checklist when I ask about progress

Workflow arc:
1. Log observations as each model works (/mercor-log)
2. Copy-paste verbatim quotes from the JSONL logs when you spot behavioral issues (/mercor-quote)
3. After both models finish, fill out the AirTable draft in the session file
4. Validate before submitting (/mercor-rating-check, /mercor-validate)
5. Generate final submission (/mercor-submit)

Writing rules for AirTable fields:
- Describe behavior, not code syntax (say "tried to interpolate the project name" not the variable name)
- Per-model sections evaluate independently, no cross-model comparisons
- Preference section is where comparisons go
- Each comment: 3+ long sentences, at least one transcript citation (preference comments cite both models)
- Write like a developer talking to a colleague, not like an AI generating a report

What "done" looks like:
- All /mercor-checklist items checked
- Ratings use the full 1-5 scale (3 is baseline, not bad, only go above with clear evidence)
- Ratings align with written comments (no contradictions)
- Behavioral issues: 3+ per model, each with severity + transcript ref + description
- General feedback compares both models with specific examples
