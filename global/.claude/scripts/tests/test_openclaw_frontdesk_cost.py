"""openclaw_frontdesk_cost.py: what the receptionist's own model calls cost, split by what started them, and the one
config write that stops the idle 30-minute heartbeat from re-reading the whole chat on Sonnet.

Numbers only: nothing here may print a prompt, a message or a key."""

import copy
import importlib.util
import io
import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "openclaw_frontdesk_cost.py"
spec = importlib.util.spec_from_file_location("openclaw_frontdesk_cost", SCRIPT)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

SONNET, HAIKU = "claude-sonnet-5-5", "claude-haiku-5-5"
HB_PROMPT = "Follow the heartbeat monitor scratch context when provided. If nothing needs attention, reply NO_REPLY."
SECRET_CHAT = "please wire the SECRETWORD thing"


def usage(write=0, read=0, inp=0, out=0):
    return {"input": inp, "output": out, "cacheRead": read, "cacheWrite": write}


def db(turns):
    """turns: (run, iso ts, model, prompt, usage) -> an in-memory copy of the two event kinds the report reads."""
    conn = sqlite3.connect(":memory:")
    conn.execute("create table trajectory_runtime_events (session_id text, seq integer, run_id text, event_json text, created_at integer)")
    seq = 0
    for run, ts, model, prompt, u in turns:
        for kind, data in (("prompt.submitted", {"prompt": prompt}), ("model.completed", {"usage": u})):
            seq += 1
            conn.execute("insert into trajectory_runtime_events values (?,?,?,?,?)", ("s", seq, run, json.dumps(
                {"type": kind, "runId": run, "ts": ts, "modelId": model, "data": data}), seq))
    return conn


class Cost(unittest.TestCase):
    def test_sonnet_cold_turn_is_priced_at_cache_write(self):
        # 40K tokens written cold: 40000 * 3.75 / 1e6
        self.assertAlmostEqual(C.list_cost(SONNET, usage(write=40000)), 0.15, places=6)

    def test_haiku_is_a_third_of_sonnet(self):
        self.assertAlmostEqual(C.list_cost(HAIKU, usage(write=40000)), 0.05, places=6)

    def test_every_part_of_the_usage_counts(self):
        # input 3, write 3.75, read 0.30, output 15 (dollars per million)
        self.assertAlmostEqual(C.list_cost(SONNET, usage(inp=1_000_000, write=1_000_000, read=1_000_000, out=1_000_000)), 22.05, places=6)

    def test_an_unpriced_model_is_loud(self):
        with self.assertRaises(KeyError):
            C.list_cost("claude-mystery-9", usage(write=1))


class Kind(unittest.TestCase):
    def test_heartbeat_prompt(self):
        self.assertEqual(C.kind_of(HB_PROMPT), "heartbeat")

    def test_anything_else_is_chat(self):
        self.assertEqual(C.kind_of(SECRET_CHAT), "chat")
        self.assertEqual(C.kind_of(None), "chat")


class Report(unittest.TestCase):
    def setUp(self):
        self.conn = db([
            ("r1", "2026-10-19T14:06:00Z", SONNET, HB_PROMPT, usage(write=50000)),
            ("r2", "2026-10-19T14:36:00Z", SONNET, HB_PROMPT, usage(write=50000)),
            ("r3", "2026-10-19T14:40:00Z", SONNET, SECRET_CHAT, usage(write=40000, read=10000, out=100)),
            ("r4", "2026-10-18T14:40:00Z", HAIKU, SECRET_CHAT, usage(write=30000)),
        ])

    def test_split_by_day_model_and_kind(self):
        rows = C.summarize(C.read_turns(self.conn))
        hb = rows[("2026-10-19", SONNET, "heartbeat")]
        self.assertEqual((hb["turns"], hb["tokens"]), (2, 100000))
        self.assertAlmostEqual(hb["cost"], 0.375, places=6)
        chat = rows[("2026-10-19", SONNET, "chat")]
        self.assertEqual((chat["turns"], chat["tokens"]), (1, 50000))
        self.assertEqual(rows[("2026-10-18", HAIKU, "chat")]["turns"], 1)

    def test_a_turn_without_usage_is_skipped_not_counted(self):
        conn = db([("r9", "2026-10-19T15:00:00Z", SONNET, SECRET_CHAT, None)])
        self.assertEqual(C.summarize(C.read_turns(conn)), {})

    def test_report_prints_numbers_and_never_the_chat(self):
        out = io.StringIO()
        with redirect_stdout(out):
            C.print_report(C.summarize(C.read_turns(self.conn)))
        text = out.getvalue()
        self.assertIn("heartbeat", text)
        self.assertIn("2026-10-19", text)
        self.assertNotIn("SECRETWORD", text)
        self.assertNotIn("scratch context", text)

    def test_the_database_is_opened_read_only(self):
        self.assertIn("mode=ro", C.ro_uri("/x/openclaw-agent.sqlite"))


def base_config():
    return {"agents": {"defaults": {
        "model": {"primary": "claude/claude-haiku-5-5", "fallbacks": ["claude/claude-sonnet-5-5"]},
        "modelPolicy": {"allow": ["claude/claude-haiku-5-5", "claude/claude-sonnet-5-5"]},
        "heartbeat": {"agentId": "main"},
    }, "entries": {"main": {"workspace": "/s/workspace", "name": "Receptionist"}}}}


def merge(base, patch):
    """RFC 7396 merge patch, which is what `openclaw config patch` applies."""
    if not isinstance(patch, dict):
        return patch
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge(out.get(k), v)
    return out


class Heartbeat(unittest.TestCase):
    def test_patch_makes_the_idle_beat_cheap(self):
        beat = merge(base_config(), C.heartbeat_patch(base_config()))["agents"]["defaults"]["heartbeat"]
        self.assertEqual(beat["model"], "claude/claude-haiku-5-5")
        self.assertIs(beat["lightContext"], True)
        self.assertIs(beat["isolatedSession"], True)
        self.assertEqual(beat["every"], "1h")

    def test_patch_keeps_the_agent_that_owns_the_heartbeat(self):
        beat = merge(base_config(), C.heartbeat_patch(base_config()))["agents"]["defaults"]["heartbeat"]
        self.assertEqual(beat["agentId"], "main")

    def test_patch_touches_nothing_but_the_heartbeat(self):
        patch = C.heartbeat_patch(base_config())
        self.assertEqual(list(patch), ["agents"])
        self.assertEqual(list(patch["agents"]), ["defaults"])
        self.assertEqual(list(patch["agents"]["defaults"]), ["heartbeat"])

    def test_patch_is_idempotent(self):
        once = merge(base_config(), C.heartbeat_patch(base_config()))
        self.assertEqual(merge(once, C.heartbeat_patch(once)), once)

    def test_refuses_a_model_the_policy_does_not_allow(self):
        cfg = base_config()
        cfg["agents"]["defaults"]["modelPolicy"]["allow"] = ["claude/claude-sonnet-5-5"]
        with self.assertRaises(C.Refused):
            C.heartbeat_patch(cfg)

    def test_verify_fails_before_and_passes_after(self):
        before = C.verify_heartbeat(base_config())
        self.assertFalse(all(ok for _, ok in before))
        after = C.verify_heartbeat(merge(base_config(), C.heartbeat_patch(base_config())))
        self.assertTrue(all(ok for _, ok in after), after)


if __name__ == "__main__":
    unittest.main()
