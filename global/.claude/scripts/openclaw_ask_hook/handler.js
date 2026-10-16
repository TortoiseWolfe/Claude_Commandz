// hub-ask: copy a message typed in OpenClaw's own dashboard into the hub's drop folder.
//
// Gateway code, not a model tool: no model is offered it, so no chat message can call it. It writes only for
// the dashboard session (DASHBOARD_SESSION) arriving over the dashboard's own channel (DASHBOARD_CHANNELS);
// any other session or channel writes nothing. The item has the closed schema openclaw_pickup.py accepts:
//   {"id","source":"tray","route":"own","text","ts"}
// The file is written under a ".tmp-" name and renamed, so the reader never sees half a file.
import { writeFileSync, renameSync } from "node:fs";
import { randomBytes } from "node:crypto";

export const DROP = process.env.HUBASK_DROP || "/mnt/wsl/ocstage";
// The dashboard's own session kinds: dashboard, a named "+" session (explicit) and the main session. Jonathan's
// first test came from "agent:main:explicit:map-retest-20261016" (16 Oct). A key naming a channel (discord,
// twitch) never matches, even with channelId webchat.
export const DASHBOARD_SESSION = /^agent:main:(?:dashboard|explicit|main)(?::[A-Za-z0-9-]{1,64})?$/;
export const DASHBOARD_CHANNELS = new Set(["webchat"]);
const MAX_TEXT = 4000;
const PER_MINUTE = 20;
let recent = [];

export function itemFor(event, nowMs, rand = () => randomBytes(5).toString("hex")) {
  if (!event || event.type !== "message" || event.action !== "received") return null;
  if (typeof event.sessionKey !== "string" || !DASHBOARD_SESSION.test(event.sessionKey)) return null;
  const c = event.context || {};
  if (typeof c.channelId !== "string" || !DASHBOARD_CHANNELS.has(c.channelId)) return null;
  if (typeof c.content !== "string") return null;
  const text = c.content.replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, " ").trim();
  if (!text || text.length > MAX_TEXT) return null;
  return { id: "tray-" + nowMs.toString(36) + "-" + rand(), source: "tray", route: "own", text, ts: Math.floor(nowMs / 1000) };
}

export default function handler(event) {
  const now = Date.now();
  const item = itemFor(event, now);
  if (!item) {
    // Names only, never the text: a missed dashboard message can be traced to the session or channel check.
    if (event && event.type === "message" && event.action === "received") {
      const c = event.context || {};
      console.log("hub-ask: skipped key " + String(event.sessionKey).slice(0, 80) + " via " + String(c.channelId).slice(0, 40));
    }
    return;
  }
  recent = recent.filter((t) => now - t < 60000);
  if (recent.length >= PER_MINUTE) return;
  recent.push(now);
  try {
    const tmp = DROP + "/.tmp-" + item.id + ".json";
    writeFileSync(tmp, JSON.stringify(item), { mode: 0o644, flag: "wx" });
    renameSync(tmp, DROP + "/" + item.id + ".json");
  } catch (e) {
    console.error("hub-ask: could not write the item (" + (e && e.code) + ")");
  }
}
