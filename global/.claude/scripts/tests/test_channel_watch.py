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
MUSE_POLL = SCRIPTS / "muse_poll.sh"

FAKE_PULLER = """import json, os, sys

def load_creds():
    return "https://example.test", "synthetic-user", "synthetic-password"

class NC:
    def __init__(self, base, user, password):
        assert (base, user, password) == (
            "https://example.test", "synthetic-user", "synthetic-password")

    def get(self, path):
        with open(os.environ["FAKE_REQUESTS"], "a", encoding="utf-8") as log:
            log.write(path + "\\n")
        if os.environ.get("FAKE_NC_RAISE") == "1":
            raise RuntimeError("synthetic network failure")
        with open(os.environ["FAKE_RESPONSES"], encoding="utf-8") as source:
            responses = json.load(source)
        return responses[path]

if __name__ == "__main__":
    with open(os.environ["FAKE_PULLS"], "a", encoding="utf-8") as log:
        log.write(json.dumps(sys.argv[1:]) + "\\n")
    raise SystemExit(int(os.environ.get("FAKE_PULL_EXIT", "0")))
"""


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
        self.puller.write_text(FAKE_PULLER)
        self.requests = self.root / "requests.log"
        self.pulls = self.root / "pulls.log"
        self.responses = self.root / "responses.json"
        self.write_responses()

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
            "FAKE_REQUESTS": str(self.requests),
            "FAKE_PULLS": str(self.pulls),
            "FAKE_RESPONSES": str(self.responses),
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
        latest_id = max((int(message["id"].rsplit(":", 1)[1]) for message in messages), default=0)
        self.write_responses(rooms=[{"token": "room_test", "lastMessage": {"id": latest_id}}])

    def write_responses(self, *, rooms=None, notifications=None):
        self.responses.write_text(json.dumps({
            "/ocs/v2.php/apps/spreed/api/v4/room?format=json": rooms or [],
            "/ocs/v2.php/apps/notifications/api/v2/notifications?format=json": notifications or [],
        }))

    def request_paths(self):
        return self.requests.read_text().splitlines() if self.requests.exists() else []

    def pull_args(self):
        return [json.loads(line) for line in self.pulls.read_text().splitlines()] if self.pulls.exists() else []

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
        self.assertEqual(second.stdout, "")
        self.assertEqual((self.state / "hub-inbox.md").read_text().count("Client talk:"), 1)

    def test_own_empty_and_previously_watched_messages_do_not_queue(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("talk:room_test:3\n")
        self.write_room([
            self.message(1, "Jonathan wrote this", sender="jonathan@example.test"),
            self.message(2, ""),
            self.message(3, "Already watched plain message"),
        ])
        self.write_responses(rooms=[{"token": "room_test", "lastMessage": {"id": 4}}])
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
        self.write_responses(notifications=[{
            "id": 42, "app": "deck", "subject": "A card changed",
        }])
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
        self.write_room([self.message(4, "cached after failed pull")])
        self.write_responses(rooms=[{"token": "room_test", "lastMessage": {"id": 5}}])
        result = self.run_watch(FAKE_PULL_EXIT=1)
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
        self.write_responses(rooms=[{"token": "room_test", "lastMessage": {"id": 7}}])
        result = self.run_watch("--dry-run", "--since", "2026-11-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dated synthetic message", result.stdout)
        self.assertNotIn("older synthetic message", result.stdout)
        self.assertNotIn("hub-inbox.md", [path.name for path in self.state.iterdir()])
        self.assertFalse((self.state / "client-tag.wake").exists())
        self.assertEqual(watched.read_text(), "talk:room_test:5\ntalk:room_test:6\n")

    def test_other_machine_exits_quietly_without_pulling(self):
        (self.home / ".config/afa/machine").write_text("NCC-74656\n")
        result = self.run_watch()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(self.pull_args())

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

    def test_quiet_server_makes_exactly_two_requests_and_does_not_pull(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("talk:room_test:4\n")
        self.write_responses(rooms=[{"token": "room_test", "lastMessage": {"id": 4}}])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.request_paths(), [
            "/ocs/v2.php/apps/spreed/api/v4/room?format=json",
            "/ocs/v2.php/apps/notifications/api/v2/notifications?format=json",
        ])
        self.assertEqual(self.pull_args(), [])

    def test_changed_room_pulls_talk_only(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("talk:room_test:5\n")
        self.write_room([self.message(6, "one changed synthetic room")])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pull_args(), [["--since-days", "3", "--only", "talk"]])

    def test_new_deck_notification_pulls_deck_and_queues_tag_line(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("")
        self.write_responses(notifications=[{
            "id": 101, "app": "deck",
            "subject": "TurtleWolfe was mentioned on Synthetic card",
        }])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pull_args(), [["--since-days", "3", "--only", "deck"]])
        board = (self.state / "hub-inbox.md").read_text()
        self.assertIn('TAG deck: "TurtleWolfe was mentioned on Synthetic card"', board)
        self.assertIn("deck-notif:101", board)
        self.assertIn("deck-notif:101", (self.state / "client-tag.wake").read_text())
        self.assertEqual((self.state / "client-talk-notified.txt").read_text(), "101\n")

    def test_deck_assignment_notification_for_self_queues_tag_line(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("")
        self.write_responses(notifications=[{
            "id": 102, "app": "deck", "subject": "You were assigned a Synthetic card",
        }])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('TAG deck: "You were assigned a Synthetic card"',
                      (self.state / "hub-inbox.md").read_text())
        self.assertIn("deck-notif:102", (self.state / "client-tag.wake").read_text())

    def test_already_seen_notification_does_not_pull(self):
        self.state.mkdir(parents=True)
        (self.state / "client-talk-watched.txt").write_text("")
        (self.state / "client-talk-notified.txt").write_text("42\n")
        self.write_responses(notifications=[{"id": 42, "app": "deck", "subject": "Mention"}])
        result = self.run_watch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.request_paths()), 2)
        self.assertEqual(self.pull_args(), [])

    def test_cheap_look_error_falls_back_to_full_pull(self):
        result = self.run_watch(FAKE_NC_RAISE=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cheap look failed; falling back to full pull", result.stderr)
        self.assertEqual(self.pull_args(), [["--since-days", "3", "--only", "talk,deck"]])

    def test_talk_poll_interval_is_1800_seconds(self):
        source = MUSE_POLL.read_text()
        self.assertIn("TALK_EVERY=${MUSE_TALK_EVERY:-1800}", source)


if __name__ == "__main__":
    unittest.main()
