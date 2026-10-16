---
name: hub-ask
description: "Copy what Jonathan types in OpenClaw's own dashboard into the hub's drop folder as a closed-schema item (source tray). Gateway code, not a model tool."
metadata:
  { "openclaw": { "emoji": "📮", "events": ["message:received"] } }
---

Runs inside the gateway when a message is accepted. It is not offered to any model as a tool, so nothing a
chat message says can call it. It writes one small JSON file per message from the dashboard session, and
nothing for any other session or channel. The hub reads the drop folder as data (openclaw_pickup.py --act).

Source: Claude_Commandz, global/.claude/scripts/openclaw_ask_hook/. Install with openclaw_ask_install.sh.
