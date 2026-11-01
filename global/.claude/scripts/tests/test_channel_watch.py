"""Synthetic-only tests for the co-op channel cache watcher."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tmpdirs import fresh_dir


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
WATCH = SCRIPTS / "channel_watch.py"
RECORD = SCRIPTS / "muse_poll_record.sh"


class ChannelWatch(unittest.TestCase):
    def setUp(self):
        self.root = Path(fresh_dir(self))
        self.home = self.root / "home"
        (self.home / ".config/afa").mkdir(parents=True)
        (self.home / ".config/afa/machine").write_text("NX-01\n")
        self.state = self.home / ".claude/state"
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.puller = self.root / "puller.py"
        self.puller.write_text("raise SystemExit(0)\n")

    def env(self, **overrides):
        values = dict(os.environ)
        values.update({
            "HOME": str(self.home),
            "STATE": str(self.state),
            "MUSE_STATE": str(self.state),
            "CHANNEL_RAW": str(self.raw),
            "CHANNEL_PULLER": str(self.puller),
            "MUSE_RECORD": str(RECORD),
            "CHANNEL_SELF": "jonathan@example.test",
            "CHANNEL_CLIENT": "Synthetic co-op",
        })
        values.update({key: str(value) for key, value in overrides.items()})
        return values

    def run_watch(self, *args, **overrides):
        return subprocess.run(
            [sys.executable, str(WATCH), *args],
            capture_output=True,
            text=True,
            env=self.env(**overrides),
            timeout=10,
        )

    def write_room(self, messages, *, room="Example Ops", filename="channel-talk-room_test.json"):
        path = self.raw / filename
        path.write_text(json.dumps({
            "id": "talk-room_test",
            "_provenance": {
                "source": "nextcloud-talk",
                "room": "room_test",
                "room_name": room,
                "pulled_at": "2026-11-01T10:00:00Z",
            },
            "messages": messages,
        }))

    @staticmethod
    def message(number, body, sender="greg@example.test", date="2026-10-28T22:47:49Z", prefix="talk"):
        room = "room_test" if prefix == "talk" else "card_42"
        return {
            "id": f"{prefix}:{room}:{number}",
            "date": date,
            "sender": sender,
            "subject": "[Talk] Example Ops" if prefix == "talk" else "Deck card subject",
            "plaintextBody": body,
        }

    def test_first_run_seeds_cache_and_queues_nothing(self):
        self.write_room([self.message(1, "@TurtleWolfe please review these synthetic notes")])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "seeded 1")
        self.assertEqual((self.state / "client-talk-watched.txt").read_text().strip(), "talk:room_test:1")
        self.assertFalse((self.state / "hub-inbox.md").exists())

    def test_tag_queues_one_line_writes_text_free_wake_and_is_not_requeued(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("")
        self.write_room([self.message(1142, "@TurtleWolfe could you look|at these\nsynthetic logs\x01")])
        first = self.run_watch()
        self.assertEqual(first.returncode, 0, first.stderr)
        board = (self.state / "hub-inbox.md").read_text()
        self.assertEqual(board.count("Client talk:"), 1)
        self.assertIn('TAG greg, Example Ops: "@TurtleWolfe could you look/at these synthetic logs"', board)
        wake = (self.state / "client-tag.wake").read_text()
        self.assertIn("talk:room_test:1142", wake)
        self.assertNotIn("synthetic logs", wake)
        second = self.run_watch()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("0 new, 0 tags", second.stdout)
        self.assertEqual((self.state / "hub-inbox.md").read_text().count("Client talk:"), 1)

    def test_own_empty_and_previously_watched_messages_do_not_queue(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("talk:room_test:3\n")
        self.write_room([
            self.message(1, "Jonathan wrote this", sender="jonathan@example.test"),
            self.message(2, ""),
            self.message(3, "Already watched plain message"),
        ])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / "hub-inbox.md").exists())
        self.assertIn("talk:room_test:1", (self.state / "client-talk-watched.txt").read_text())

    def test_three_plain_messages_roll_up_once_with_newest_id(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("")
        self.write_room([
            self.message(1, "first message", "alice@example.test", "2026-10-28T10:00:00Z"),
            self.message(2, "second message", "bob@example.test", "2026-10-28T11:00:00Z"),
            self.message(3, "the newest synthetic message", "alice@example.test", "2026-10-28T12:00:00Z"),
        ])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in (self.state / "hub-inbox.md").read_text().splitlines() if "Client talk:" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("Example Ops: 3 new (alice, bob)", lines[0])
        self.assertIn('newest: "the newest synthetic message"', lines[0])
        self.assertTrue(lines[0].endswith("talk:room_test:3"))

    def test_deck_cards_use_their_subject_and_card_id(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("")
        card = self.raw / "channel-deck-card_42.json"
        card.write_text(json.dumps({
            "id": "deck-card_42",
            "_provenance": {"source": "nextcloud-deck", "pulled_at": "2026-11-01T10:00:00Z"},
            "messages": [self.message(7, "synthetic card update", "maria@example.test",
                                      "2026-11-01T09:00:00Z", prefix="deck")],
        }))
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        line = (self.state / "hub-inbox.md").read_text()
        self.assertIn("Deck card subject: 1 new (maria)", line)
        self.assertIn("deck:card_42:7", line)

    def test_failed_puller_still_diffs_the_cache(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("")
        self.puller.write_text("raise SystemExit(1)\n")
        self.write_room([self.message(4, "cached after failed pull")])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("puller failed; diffing cache", result.stderr)
        self.assertIn("cached after failed pull", (self.state / "hub-inbox.md").read_text())

    def test_dry_run_since_lists_dated_messages_without_writing(self):
        self.state.mkdir(parents=True)
        watched = self.state / "client-talk-watched.txt"
        watched.write_text("talk:room_test:5\ntalk:room_test:6\n")
        self.write_room([
            self.message(5, "older synthetic message", date="2026-10-31T23:59:59Z"),
            self.message(6, "dated synthetic message", date="2026-11-01T00:00:00Z"),
        ])
        result = self.run_watch("--dry-run", "--since", "2026-11-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dated synthetic message", result.stdout)
        self.assertNotIn("older synthetic message", result.stdout)
        self.assertNotIn("hub-inbox.md", [path.name for path in self.state.iterdir()])
        self.assertFalse((self.state / "client-tag.wake").exists())
        self.assertEqual(watched.read_text(), "talk:room_test:5\ntalk:room_test:6\n")

    def test_other_machine_exits_quietly_without_pulling(self):
        (self.home / ".config/afa/machine").write_text("NCC-74656\n")
        marker = self.root / "puller-ran"
        self.puller.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        result = self.run_watch()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(marker.exists())

    def test_private_settings_supply_self_address_and_client_label(self):
        config = self.home / ".config/afa/channel-watch.json"
        config.write_text(json.dumps({"self": "jonathan@example.test", "client": "Synthetic co-op"}))
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("")
        self.write_room([
            self.message(8, "my synthetic message", sender="jonathan@example.test"),
            self.message(9, "@Jonathan check this synthetic note"),
        ])
        env = self.env()
        env.pop("CHANNEL_SELF")
        env.pop("CHANNEL_CLIENT")
        result = subprocess.run([sys.executable, str(WATCH)], capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        board = (self.state / "hub-inbox.md").read_text()
        self.assertIn("Client talk: Synthetic co-op", board)
        self.assertNotIn("my synthetic message", board)


if __name__ == "__main__":
    unittest.main()
