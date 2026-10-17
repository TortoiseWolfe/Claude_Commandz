// session-handoff: before OpenClaw compacts or resets the receptionist's chat, write the hand-off to MEMORY.md.
// Gateway code, not a model tool. Events: session:compact:before and command:new / command:reset (all awaited).
// The 4 AM daily reset is not hooked here: it runs when the next message arrives, hours after the last turn, so
// handoff.js --reason sweep (called from the 15-minute status task) has already written the hand-off by then.
// A hook never throws into the gateway and never holds a turn for more than HOLD_MS.
import { writeHandoff } from "./handoff.js";

export const HOLD_MS = 60000;
const WANTED = new Set(["session:compact:before", "command:new", "command:reset"]);

export function wanted(event) {
  return !!event && WANTED.has(`${event.type}:${event.action}`);
}

export default async function handler(event, deps) {
  if (!wanted(event)) return;
  const ctx = event.context || {};
  let timer;
  try {
    const r = await Promise.race([
      writeHandoff({ sessionKey: event.sessionKey, agentId: ctx.agentId, workspaceDir: ctx.workspaceDir, force: true, ...(deps ? { deps } : {}) }),
      new Promise((resolve) => { timer = setTimeout(() => resolve({ status: "timeout" }), HOLD_MS); }),
    ]);
    console.log("session-handoff: " + event.type + ":" + event.action + " -> " + r.status + (r.how ? " (" + r.how + ")" : ""));
  } catch (e) {
    console.error("session-handoff: failed (" + String(e && e.message).slice(0, 120) + ")");
  } finally {
    clearTimeout(timer);
  }
}
