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
            {"message": "hey, did the link open? i found it in the notes, the page looked fine on my end."})
        self.assertIn("Verdict: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
