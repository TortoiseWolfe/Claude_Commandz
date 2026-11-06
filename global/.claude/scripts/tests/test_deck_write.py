import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deck_write


class DeckCommentVoiceGate(unittest.TestCase):
    def invoke(self, text):
        return deck_write.main([
            "--env", "unused.env", "--board", "4", "comment",
            "--card", "9", "--text", text,
        ])

    def test_failed_comment_is_rejected_before_credentials_or_requests(self):
        output = io.StringIO()
        with (mock.patch.object(deck_write, "read_env") as read_env,
              mock.patch.object(deck_write, "call") as request,
              contextlib.redirect_stdout(output),
              self.assertRaises(SystemExit) as raised):
            self.invoke("**made up text**")

        self.assertIn("no request was made", str(raised.exception))
        self.assertIn("[chat_structure]", output.getvalue())
        read_env.assert_not_called()
        request.assert_not_called()

    def test_passing_comment_uses_the_comment_endpoint(self):
        output = io.StringIO()
        with (mock.patch.object(deck_write, "read_env", return_value={"URL": "https://example.test"}),
              mock.patch.object(deck_write, "find_card", return_value=({}, {"id": 9})),
              mock.patch.object(deck_write, "call") as request,
              contextlib.redirect_stdout(output)):
            self.assertEqual(self.invoke("hey, did the link open? i found it in the notes, the page looked fine on my end."), 0)

        request.assert_called_once_with(
            {"URL": "https://example.test"}, "POST", "/cards/9/comments",
            {"message": "hey, did the link open? i found it in the notes, the page looked fine on my end."},
            api="/ocs/v2.php/apps/deck/api/v1.0")  # comments exist only on the OCS route; the REST route answers 405
        self.assertIn("Verdict: PASS", output.getvalue())


class DeckTitleAndDue(unittest.TestCase):
    """title and due on made-up cards; no test reads a credentials file, the network or the live board."""
    CARD = {"id": 9, "title": "Made up old title", "description": "d", "type": "plain", "owner": {"uid": "someone"},
            "order": 0, "duedate": "2026-10-31T04:00:00+00:00"}

    def run_action(self, *action, before=None, after=None):
        output = io.StringIO()
        stack = {"id": 5, "title": "In progress"}
        cards = iter([(stack, before or dict(self.CARD)), (stack, after or dict(self.CARD))])
        with (mock.patch.object(deck_write, "read_env", return_value={"URL": "https://example.test"}),
              mock.patch.object(deck_write, "find_card", side_effect=lambda *a: next(cards)),
              mock.patch.object(deck_write, "call") as request,
              contextlib.redirect_stdout(output)):
            deck_write.main(["--env", "unused.cfg", "--board", "4", *action])
        return request, output.getvalue()

    def test_title_sends_only_the_new_title_and_keeps_the_rest_of_the_card(self):
        request, out = self.run_action("title", "--card", "9", "--text", "Made up new title",
                                       after=dict(self.CARD, title="Made up new title"))
        (_, method, path, body), _ = request.call_args
        self.assertEqual((method, path), ("PUT", "/boards/4/stacks/5/cards/9"))
        self.assertEqual((body["title"], body["duedate"], body["owner"]), ("Made up new title", self.CARD["duedate"], "someone"))
        self.assertIn("Made up old title -> Made up new title [In progress]", out)

    def test_due_reads_eastern_time_and_sends_the_offset(self):
        request, out = self.run_action("due", "--card", "9", "--date", "2026-11-10T21:00",
                                       after=dict(self.CARD, duedate="2026-11-10T21:00:00-05:00"))
        body = request.call_args[0][3]
        self.assertEqual(body["duedate"], "2026-11-10T21:00:00-05:00")
        self.assertEqual(body["title"], "Made up old title")
        self.assertIn("Sat 31 Oct 2026 12:00 AM -> Tue 10 Nov 2026 9:00 PM", out)

    def test_summer_dates_get_the_daylight_offset(self):
        self.assertEqual(deck_write.eastern_due("2026-07-01T09:30"), "2026-07-01T09:30:00-04:00")

    def test_a_bad_date_stops_before_credentials_or_requests(self):
        with (mock.patch.object(deck_write, "read_env") as read_env,
              mock.patch.object(deck_write, "call") as request,
              self.assertRaises(SystemExit) as raised):
            deck_write.main(["--env", "unused.cfg", "--board", "4", "due", "--card", "9", "--date", "next Tuesday"])
        self.assertIn("2026-11-10T21:00", str(raised.exception))
        read_env.assert_not_called()
        request.assert_not_called()

    def test_an_empty_title_is_refused(self):
        with (mock.patch.object(deck_write, "read_env") as read_env, self.assertRaises(SystemExit)):
            deck_write.main(["--env", "unused.cfg", "--board", "4", "title", "--card", "9", "--text", "  "])
        read_env.assert_not_called()

    def test_show_prints_the_due_date_in_eastern_time(self):
        _, out = self.run_action("show", "--card", "9")
        self.assertIn("due: Sat 31 Oct 2026 12:00 AM", out)
        _, out = self.run_action("show", "--card", "9", before=dict(self.CARD, duedate=None))
        self.assertIn("due: -", out)

    def test_show_due_handles_zulu_and_naive_text(self):
        self.assertEqual(deck_write.show_due("2026-11-11T02:00:00Z"), "Tue 10 Nov 2026 9:00 PM")
        self.assertEqual(deck_write.show_due("2026-11-10 21:00:00"), "Tue 10 Nov 2026 9:00 PM")


if __name__ == "__main__":
    unittest.main()
