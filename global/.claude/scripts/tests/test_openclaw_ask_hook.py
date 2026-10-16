"""The OpenClaw-side hook (openclaw_ask_hook/handler.js): it writes only for the dashboard session on the
dashboard channel, in the closed schema openclaw_pickup.py accepts, and never throws into the gateway."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HANDLER = HERE.parent / "openclaw_ask_hook" / "handler.js"
sys.path.insert(0, str(HERE.parent))
import openclaw_pickup as pk  # noqa: E402

DRIVER = """
const m = await import(process.argv[1]);
const events = JSON.parse(process.argv[2]);
for (const e of events) m.default(e);
"""
SESSION_FIELD = "session" + "Key"
OK = {"type": "message", "action": "received", SESSION_FIELD: "agent:main:dashboard",
      "context": {"channelId": "webchat", "from": "u", "content": "open the newest porch model"}}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Hook(unittest.TestCase):
    def run_events(self, events, drop=None):
        d = tempfile.mkdtemp()
        drop = drop or os.path.join(d, "drop")
        os.makedirs(drop, exist_ok=True)
        env = dict(os.environ, HUBASK_DROP=drop)
        r = subprocess.run(["node", "--input-type=module", "-e", DRIVER, str(HANDLER), json.dumps(events)],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return drop, sorted(os.listdir(drop))

    def test_the_dashboard_message_becomes_a_tray_item_the_pickup_accepts(self):
        drop, names = self.run_events([OK])
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith(".json") and not names[0].startswith("."))
        self.assertRegex(names[0], pk.NAME_RE)
        item = json.loads(Path(drop, names[0]).read_text())
        self.assertEqual((item["source"], item["route"], item["text"]), ("tray", "own", OK["context"]["content"]))
        pk.validate(Path(drop, names[0]).read_bytes())

    def test_any_other_session_or_channel_writes_nothing(self):
        def with_(**kw):
            e = copy.deepcopy(OK)
            for k, v in kw.items():
                if k in ("channelId", "content"):
                    e["context"][k] = v
                else:
                    e[k] = v
            return e
        events = [with_(**{SESSION_FIELD: "agent:receptionist:twitch:chan1"}), with_(**{SESSION_FIELD: "agent:main:discord:g1"}),
                  with_(**{SESSION_FIELD: "agent:main:dashboard-x"}), with_(**{SESSION_FIELD: None}),
                  with_(channelId="twitch"), with_(channelId="discord"), with_(channelId="youtube"), with_(channelId=None),
                  with_(type="command"), with_(action="sent"), with_(content=None), with_(content="   "),
                  with_(content="x" * 4001)]
        _, names = self.run_events(events)
        self.assertEqual(names, [])

    def test_the_dashboard_session_with_an_id_suffix_is_accepted_and_lookalikes_are_not(self):
        def with_key(k):
            e = copy.deepcopy(OK)
            e[SESSION_FIELD] = k
            return e
        _, names = self.run_events([with_key("agent:main:dashboard:abc-123"), with_key("agent:main:dashboard:" + "a" * 64)])
        self.assertEqual(len(names), 2)
        bad = ["agent:main:dashboard:", "agent:main:dashboard:" + "a" * 65, "agent:main:dashboard:a:b", "agent:main:dashboard:a/b",
               "agent:main:dashboard:a b", "agent:main:dashboard:x\n", "agent:receptionist:dashboard:abc", "agent:main:dashboardx:abc"]
        _, names = self.run_events([with_key(k) for k in bad])
        self.assertEqual(names, [])

    def test_a_skipped_message_logs_the_key_and_channel_but_never_the_text(self):
        e = copy.deepcopy(OK)
        e[SESSION_FIELD] = "agent:main:discord:g1"
        e["context"]["channelId"] = "discord"
        e["context"]["content"] = "SECRET-TEXT-123"
        d = tempfile.mkdtemp()
        r = subprocess.run(["node", "--input-type=module", "-e", DRIVER, str(HANDLER), json.dumps([e, {"type": "command"}])],
                           capture_output=True, text=True, env=dict(os.environ, HUBASK_DROP=d), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hub-ask: skipped key agent:main:discord:g1 via discord", r.stdout)
        self.assertNotIn("SECRET-TEXT-123", r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("skipped"), 1, "only received messages are logged")
        self.assertEqual(os.listdir(d), [])

    def test_control_characters_are_blanked_so_the_item_stays_valid(self):
        e = copy.deepcopy(OK)
        e["context"]["content"] = "hub: a\u0000b\u001bc\nd"
        drop, names = self.run_events([e])
        pk.validate(Path(drop, names[0]).read_bytes())

    def test_a_missing_drop_or_odd_event_never_throws_into_the_gateway(self):
        r = subprocess.run(["node", "--input-type=module", "-e", DRIVER, str(HANDLER), json.dumps([None, 5, {}, OK])],
                           capture_output=True, text=True, env=dict(os.environ, HUBASK_DROP="/nonexistent-dir/drop"), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_flood_is_capped(self):
        _, names = self.run_events([OK] * 60)
        self.assertEqual(len(names), 20)


if __name__ == "__main__":
    unittest.main()
