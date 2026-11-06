"""deck_board.py marks finished work so the hub digest can count open cards: a done column on its stack header, " | done" on a card
that is done or archived. Deck is replaced by canned answers; nothing here reads a .env file or the network."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import deck_board as db  # noqa: E402

BOARD = {"title": "Operations"}
STACKS = [
    {"title": "Next", "order": 0, "isDoneColumn": False, "cards": [
        {"id": 1, "title": "Open", "order": 0, "assignedUsers": [{"participant": {"uid": "TurtleWolfe"}}], "owner": {"uid": "greg"}},
        {"id": 2, "title": "Ticked", "order": 1, "done": "2026-11-01T10:00:00+00:00", "assignedUsers": [], "owner": {"uid": "greg"}},
        {"id": 3, "title": "Archived", "order": 2, "archived": True, "assignedUsers": [], "owner": {"uid": "greg"}}]},
    {"title": "Review", "order": 1, "isDoneColumn": True, "cards": []},
    {"title": "Done", "order": 2, "isDoneColumn": False, "cards": []},
]


class Marks(unittest.TestCase):
    def run_main(self):
        answers = iter([(200, BOARD), (200, STACKS)])
        out = io.StringIO()
        with mock.patch.object(db, "read_env", return_value={"URL": "https://deck.example", "USER": "u", "PASS": "p"}), \
                mock.patch.object(db, "get", side_effect=lambda *a, **k: next(answers)), redirect_stdout(out):
            rc = db.main(["--env", "x", "--board", "2"])
        self.assertEqual(rc, 0)
        return out.getvalue().splitlines()

    def test_done_and_archived_cards_end_with_done_and_open_ones_do_not(self):
        lines = {l.split(" ")[1]: l for l in self.run_main() if l.startswith("- #")}
        self.assertFalse(lines["#1"].endswith(" | done"))
        self.assertTrue(lines["#2"].endswith(" | done"))
        self.assertTrue(lines["#3"].endswith(" | done"))

    def test_a_done_column_is_marked_on_its_header_and_a_plain_stack_is_not(self):
        heads = [l for l in self.run_main() if l.startswith("## ")]
        self.assertEqual(heads, ["## Next (3)", "## Review (0) [done column]", "## Done (0)"])

    def test_a_due_date_with_an_offset_shows_as_its_eastern_day(self):
        self.assertEqual(db.when("2026-11-11T02:00:00+00:00"), "2026-11-10")   # 9 PM on the 10th, Eastern
        self.assertEqual(db.when("2026-10-31T04:00:00+00:00"), "2026-10-31")
        self.assertEqual(db.when("2026-10-31"), "2026-10-31")
        self.assertEqual(db.when("2026-10-31T09:00:00"), "2026-10-31")   # no offset: the text's own day
        self.assertEqual(db.when(None), "")


if __name__ == "__main__":
    unittest.main()
