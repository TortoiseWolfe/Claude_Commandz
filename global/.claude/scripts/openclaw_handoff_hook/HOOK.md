---
name: session-handoff
description: "Before a compaction or a reset, write the receptionist's hand-off (decisions, open asks, what Jonathan waits on) to a bounded block in MEMORY.md, which loads into every fresh session. Gateway code, not a model tool."
metadata:
  { "openclaw": { "emoji": "🤝", "events": ["session:compact:before", "command:new", "command:reset"] } }
---

Runs inside the gateway. It is not offered to any model as a tool, so nothing a chat message says can call it.
It acts only for a main chat (agent:<id>:main), writes one marked block (at most 2 KB, the newest replaces the old)
and nothing else. The text is drafted by a one-shot, tool-less model call (openclaw infer model run) and the file
is written by this code. With no model available it writes a plain fallback that names the open lanes and says the
decisions were not captured.

The 4 AM daily reset happens when the next message arrives, so handoff.js --reason sweep, run from the
15-minute status task, keeps the block current while the chat is quiet.

Source: Claude_Commandz, global/.claude/scripts/openclaw_handoff_hook/. Install with openclaw_handoff_install.sh.
