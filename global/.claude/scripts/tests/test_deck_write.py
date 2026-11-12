import contextlib
import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os

from tmpdirs import fresh_dir

import deck_write


class IsolatedState(unittest.TestCase):
    """Each test gets its own empty pacing folder, so no test reads or writes the live ~/.claude/state."""
    def setUp(self):
        self._dir = fresh_dir(self)
        patcher = mock.patch.dict(os.environ, {"DECK_STATE_DIR": self._dir})
        patcher.start()
        self.addCleanup(patcher.stop)


class DeckCommentVoiceGate(IsolatedState):
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
            text = "hey, did the link open? i found it in the notes, the page looked fine on my end."
            request.side_effect = [{"ocs": {"data": []}},
                                   {"ocs": {"data": {"id": 3}}},
                                   {"ocs": {"data": [{"id": 3, "message": text, "actorId": "x"}]}}]
            self.assertEqual(self.invoke(text), 0)

        # the list as it was, then the POST (comments exist only on the OCS route; the REST route answers 405), then the read-back
        listing = mock.call({"URL": "https://example.test"}, "GET", "/cards/9/comments", api="/ocs/v2.php/apps/deck/api/v1.0")
        self.assertEqual(request.call_args_list[0], listing)
        self.assertEqual(request.call_args_list[1], mock.call(
            {"URL": "https://example.test"}, "POST", "/cards/9/comments", {"message": text},
            api="/ocs/v2.php/apps/deck/api/v1.0"))
        self.assertEqual(request.call_args_list[2], listing)
        self.assertEqual(len(request.call_args_list), 3)
        self.assertIn("Verdict: PASS", output.getvalue())


class DeckTitleAndDue(IsolatedState):
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

    def test_due_reads_eastern_time_and_sends_the_same_moment_in_utc(self):
        request, out = self.run_action("due", "--card", "9", "--date", "2026-11-10T21:00",
                                       after=dict(self.CARD, duedate="2026-11-11T02:00:00+00:00"))
        body = request.call_args[0][3]
        self.assertEqual(body["duedate"], "2026-11-11T02:00:00+00:00")
        self.assertEqual(body["title"], "Made up old title")
        self.assertIn("Sat 31 Oct 2026 12:00 AM -> Tue 10 Nov 2026 9:00 PM", out)

    def test_summer_dates_use_daylight_time(self):
        self.assertEqual(deck_write.eastern_due("2026-07-01T09:30"), "2026-07-01T13:30:00+00:00")

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


OCS = "/ocs/v2.php/apps/deck/api/v1.0"
CREDS = {"URL": "https://example.test", "USER": "robot", "PASS": "x"}
SAY = "hey, the made up page loaded fine for me, thanks for checking it again today."
STACK_A = {"id": 5, "title": "In progress"}
STACK_B = {"id": 6, "title": "Done"}
CARD = {"id": 9, "title": "Made up secret title", "description": "d", "type": "plain", "owner": {"uid": "someone"},
        "order": 0, "duedate": "2026-10-31T04:00:00+00:00"}


class Harness(IsolatedState):
    """Runs main against a fake server (call, find_card, find_stack, read_env are faked); nothing touches a network."""
    def run_main(self, *action, finds=(), call=None, stack=None, gap_clear=True):
        if gap_clear:
            deck_write.last_write_path().unlink(missing_ok=True)
        finds = iter(finds)
        request = mock.Mock(side_effect=call) if call else mock.Mock(return_value={})
        out, err = io.StringIO(), io.StringIO()
        code = "returned"
        with (mock.patch.object(deck_write, "read_env", return_value=dict(CREDS)),
              mock.patch.object(deck_write, "find_card", side_effect=lambda *a: next(finds)),
              mock.patch.object(deck_write, "find_stack", return_value=stack or STACK_B),
              mock.patch.object(deck_write, "call", request),
              contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)):
            try:
                deck_write.main(["--env", "unused.cfg", "--board", "4", *action])
            except SystemExit as stop:
                code = stop.code
        self.request, self.out, self.err = request, out.getvalue(), err.getvalue()
        return code

    def log_lines(self):
        path = deck_write.state_dir() / "deck-write-log.tsv"
        return path.read_text().splitlines() if path.exists() else []


def comments_call(post_answer, listing, before=()):
    """A fake server for one comment: the list holds `before` until the POST and `listing` after it. A BaseException given as
    `before`, `post_answer` or `listing` is raised at that step. fake.methods records the order of the requests."""
    seen = []

    def fake(c, method, path, body=None, api=deck_write.API):
        seen.append(method)
        if method == "POST":
            if isinstance(post_answer, BaseException):
                raise post_answer
            return post_answer
        now = listing if "POST" in seen else list(before) if not isinstance(before, BaseException) else before
        if isinstance(now, BaseException):
            raise now
        return {"ocs": {"meta": {}, "data": now}}
    fake.methods = seen
    return fake


class CommentReadBack(Harness):
    def comment(self, post_answer, listing, before=()):
        self.fake = comments_call(post_answer, listing, before)
        return self.run_main("comment", "--card", "9", "--text", SAY, finds=[(STACK_A, CARD)], call=self.fake)

    def test_a_comment_that_reads_back_is_posted_and_logged(self):
        code = self.comment({"ocs": {"data": {"id": 31}}},
                            [{"id": 32, "message": "other", "actorId": "z"}, {"id": 31, "message": SAY + " ", "actorId": "robot"}])
        self.assertEqual(code, "returned")
        self.assertIn("commented on #9 (read back: comment 31)", self.out)
        fields = self.log_lines()[0].split("\t")
        self.assertEqual(fields[1:6], ["posted", "4", "9", "comment", "-"])

    def test_without_an_id_in_the_answer_a_comment_that_was_not_there_before_proves_it(self):
        self.assertEqual(self.comment("odd", [{"id": 8, "message": SAY, "actorId": "robot"}]), "returned")
        self.assertIn("read back: comment 8", self.out)
        self.assertEqual(self.fake.methods, ["GET", "POST", "GET"])

    def test_without_an_id_an_older_comment_with_the_same_words_is_not_the_proof(self):
        older = [{"id": 8, "message": SAY, "actorId": "robot"}]
        self.assertEqual(self.comment("odd", older, before=older), deck_write.EXIT_UNPROVEN)
        self.assertEqual(self.log_lines()[0].split("\t")[1], "unproven")
        newer = [{"id": 9, "message": SAY, "actorId": "robot"}] + older
        self.assertEqual(self.comment("odd", newer, before=older), "returned")
        self.assertIn("read back: comment 9", self.out)

    def test_an_id_that_is_a_number_in_one_place_and_text_in_the_other_still_matches(self):
        self.assertEqual(self.comment({"ocs": {"data": {"id": "31"}}}, [{"id": 31, "message": SAY}]), "returned")
        self.assertEqual(self.comment({"ocs": {"data": {"id": 31}}}, [{"id": "31", "message": SAY}]), "returned")

    def test_a_post_whose_answer_never_came_is_unproven_not_a_failure_to_retry(self):
        for lost in (TimeoutError("timed out"), ConnectionResetError("reset"), deck_write.MaybeTaken("POST /cards/9/comments: HTTP 502")):
            self.assertEqual(self.comment(lost, []), deck_write.EXIT_UNPROVEN, lost)
            self.assertIn("NOT PROVEN", self.err)
            self.assertIn("do not post it again", self.err)
            self.assertEqual(self.log_lines()[-1].split("\t")[1], "unproven")
            self.assertEqual(self.fake.methods, ["GET", "POST"])

    def test_a_lost_connection_before_the_post_left_is_an_ordinary_failure(self):
        code = self.comment({"ocs": {"data": {"id": 31}}}, [], before=TimeoutError("timed out"))
        self.assertIsInstance(code, str)
        self.assertIn("TimeoutError", code)
        self.assertNotIn("NOT PROVEN", self.err)
        self.assertEqual(self.fake.methods, ["GET"])   # the POST never left
        self.assertEqual(self.log_lines()[0].split("\t")[1], "failed")

    def test_a_lost_connection_while_finding_the_card_is_an_ordinary_failure(self):
        def no_card(*a):
            raise OSError("network is unreachable")
        out, err = io.StringIO(), io.StringIO()
        with (mock.patch.object(deck_write, "read_env", return_value=dict(CREDS)),
              mock.patch.object(deck_write, "find_card", side_effect=no_card),
              mock.patch.object(deck_write, "call") as request,
              contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),
              self.assertRaises(SystemExit) as stop):
            deck_write.last_write_path().unlink(missing_ok=True)
            deck_write.main(["--env", "unused.cfg", "--board", "4", "comment", "--card", "9", "--text", SAY])
        self.assertIn("OSError", str(stop.exception.code))
        request.assert_not_called()
        self.assertEqual(self.log_lines()[0].split("\t")[1], "failed")

    def test_a_list_without_the_comment_is_unproven(self):
        code = self.comment({"ocs": {"data": {"id": 31}}}, [{"id": 30, "message": SAY, "actorId": "robot"}])
        self.assertEqual(code, deck_write.EXIT_UNPROVEN)
        self.assertIn("deck_write: comment on card 9 was accepted but is NOT PROVEN:", self.err)
        self.assertEqual(self.log_lines()[0].split("\t")[1], "unproven")

    def test_a_matching_id_with_other_text_is_unproven(self):
        self.assertEqual(self.comment({"ocs": {"data": {"id": 31}}}, [{"id": 31, "message": "changed", "actorId": "robot"}]),
                         deck_write.EXIT_UNPROVEN)

    def test_a_read_back_http_error_after_a_good_post_is_unproven_not_a_plain_failure(self):
        code = self.comment({"ocs": {"data": {"id": 31}}}, SystemExit("deck_write: GET /cards/9/comments: HTTP 500"))
        self.assertEqual(code, deck_write.EXIT_UNPROVEN)
        self.assertIn("HTTP 500", self.err)

    def test_a_read_back_network_error_after_a_good_post_is_unproven(self):
        self.assertEqual(self.comment({"ocs": {"data": {"id": 31}}}, TimeoutError("timed out")), deck_write.EXIT_UNPROVEN)

    def test_a_refused_post_is_an_ordinary_failure(self):
        code = self.comment(SystemExit("deck_write: POST /cards/9/comments: HTTP 403"), [])
        self.assertIn("HTTP 403", code)
        self.assertNotIn("NOT PROVEN", self.err)
        self.assertEqual(self.log_lines()[0].split("\t")[1], "failed")

    def test_the_log_never_holds_the_comment_text_or_the_card_title(self):
        self.comment({"ocs": {"data": {"id": 31}}}, [])
        text = (deck_write.state_dir() / "deck-write-log.tsv").read_text()
        self.assertNotIn(SAY[:20], text)
        self.assertNotIn("secret title", text)
        self.assertEqual(oct((deck_write.state_dir() / "deck-write-log.tsv").stat().st_mode & 0o777), "0o600")


class OtherReadBacks(Harness):
    def test_append_and_replace_pass_when_the_description_matches_and_fail_when_not(self):
        base = dict(CARD, description="one")
        good = dict(base, description="one\n\nplus  \n")
        self.assertEqual(self.run_main("append", "--card", "9", "--text", "plus", finds=[(STACK_A, base), (STACK_A, good)]), "returned")
        self.assertEqual(self.run_main("append", "--card", "9", "--text", "plus",
                                       finds=[(STACK_A, base), (STACK_A, base)]), deck_write.EXIT_UNPROVEN)
        self.assertEqual(self.run_main("replace", "--card", "9", "--old", "one", "--new", "two",
                                       finds=[(STACK_A, base), (STACK_A, dict(base, description="two"))]), "returned")
        self.assertEqual(self.run_main("replace", "--card", "9", "--old", "one", "--new", "two",
                                       finds=[(STACK_A, base), (STACK_A, base)]), deck_write.EXIT_UNPROVEN)

    def test_title(self):
        self.assertEqual(self.run_main("title", "--card", "9", "--text", "New", finds=[(STACK_A, CARD), (STACK_A, dict(CARD, title="New"))]), "returned")
        self.assertEqual(self.run_main("title", "--card", "9", "--text", "New", finds=[(STACK_A, CARD), (STACK_A, CARD)]),
                         deck_write.EXIT_UNPROVEN)

    def test_due_compares_the_same_instant_not_the_text(self):
        args = ("due", "--card", "9", "--date", "2026-11-10T21:00")
        same_instant = dict(CARD, duedate="2026-11-10T21:00:00-05:00")
        self.assertEqual(self.run_main(*args, finds=[(STACK_A, CARD), (STACK_A, same_instant)]), "returned")
        self.assertEqual(self.run_main(*args, finds=[(STACK_A, CARD), (STACK_A, CARD)]), deck_write.EXIT_UNPROVEN)
        self.assertEqual(self.run_main(*args, finds=[(STACK_A, CARD), (STACK_A, dict(CARD, duedate=None))]), deck_write.EXIT_UNPROVEN)

    def test_move_mismatch_is_unproven(self):
        args = ("move", "--card", "9", "--stack", "Done")
        self.assertEqual(self.run_main(*args, finds=[(STACK_A, CARD), (STACK_B, CARD)]), "returned")
        self.assertEqual(self.run_main(*args, finds=[(STACK_A, CARD), (STACK_A, CARD)]), deck_write.EXIT_UNPROVEN)
        self.assertIn("NOT PROVEN", self.err)

    def test_create_needs_title_stack_and_every_assignee(self):
        args = ("create", "--stack", "Done", "--title", "Made up new", "--assign", "ann", "--assign", "bob")
        people = [{"participant": {"uid": "ann"}}, {"participant": {"uid": "bob"}}]
        made = dict(CARD, id=12, title="Made up new", assignedUsers=people)
        post = lambda c, m, p, body=None, api=None: {"id": 12}
        self.assertEqual(self.run_main(*args, finds=[(STACK_B, made)], call=post), "returned")
        self.assertEqual(self.log_lines()[-1].split("\t")[3], "12")
        for bad in (dict(made, title="Other"), dict(made, assignedUsers=people[:1])):
            self.assertEqual(self.run_main(*args, finds=[(STACK_B, bad)], call=post), deck_write.EXIT_UNPROVEN)
        self.assertEqual(self.run_main(*args, finds=[(STACK_A, made)], call=post), deck_write.EXIT_UNPROVEN)

    def test_an_assign_error_after_the_card_exists_is_unproven(self):
        def fake(c, method, path, body=None, api=None):
            if method == "PUT":
                raise SystemExit("deck_write: PUT assignUser: HTTP 500")
            return {"id": 12}
        code = self.run_main("create", "--stack", "Done", "--title", "T", "--assign", "ann", finds=[(STACK_B, CARD)], call=fake)
        self.assertEqual(code, deck_write.EXIT_UNPROVEN)


class WhatAnAnswerMeans(IsolatedState):
    """call() itself, with the network opener faked: which HTTP answers to a write say "refused" and which say "maybe taken"."""
    def answer(self, method, code):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://example.test/x", code, "made up", None, None)
        with mock.patch.object(deck_write.urllib.request, "build_opener", return_value=opener):
            return deck_write.call(dict(CREDS), method, "/cards/9/comments", {"message": "x"} if method != "GET" else None)

    def test_a_write_answered_500_502_or_504_may_have_been_taken(self):
        self.assertEqual(deck_write.MAYBE_TAKEN, (500, 502, 504))
        for method in ("POST", "PUT", "DELETE"):
            for code in deck_write.MAYBE_TAKEN:
                with self.assertRaises(deck_write.MaybeTaken, msg=f"{method} {code}") as caught:
                    self.answer(method, code)
                self.assertIn(f"HTTP {code}", str(caught.exception))

    def test_a_refused_write_is_a_plain_stop(self):
        for code in (400, 401, 403, 404, 405, 409, 429, 503):
            with self.assertRaises(SystemExit, msg=str(code)) as stop:
                self.answer("POST", code)
            self.assertIn(f"HTTP {code}", stop.exception.code)

    def test_a_read_changes_nothing_so_any_error_on_it_is_a_plain_stop(self):
        for code in (500, 502, 504, 404):
            with self.assertRaises(SystemExit, msg=str(code)) as stop:
                self.answer("GET", code)
            self.assertIn(f"GET /cards/9/comments: HTTP {code}", stop.exception.code)


class TwoPartVisit(Harness):
    """A comment and a move joined with + are one visit. Once the first part reached the board, a retry of the whole visit would
    post that part twice, so any stop after it is unproven, never a failure to retry."""
    PAIR = ("comment", "--card", "9", "--text", SAY, "+", "move", "--card", "9", "--stack", "Done")

    def board(self, refuse=()):
        """A fake board that keeps the comment it is sent. `refuse` lists the write methods it answers with HTTP 403."""
        kept, seen = [], []

        def fake(c, method, path, body=None, api=deck_write.API):
            seen.append(method)
            if method == "GET":
                return {"ocs": {"data": list(kept)}}
            if method in refuse:
                raise SystemExit(f"deck_write: {method} {path}: HTTP 403")
            if method == "POST":
                kept.append({"id": 31, "message": body["message"]})
                return {"ocs": {"data": {"id": 31}}}
            return None
        fake.methods = seen
        return fake

    def test_both_parts_post_and_read_back(self):
        fake = self.board()
        self.assertEqual(self.run_main(*self.PAIR, finds=[(STACK_A, CARD), (STACK_A, CARD), (STACK_B, CARD)], call=fake), "returned")
        self.assertEqual(fake.methods, ["GET", "POST", "GET", "PUT"])
        self.assertEqual(self.log_lines()[0].split("\t")[1:5], ["posted", "4", "9", "comment+move"])

    def test_a_second_part_refused_after_the_first_posted_is_unproven(self):
        fake = self.board(refuse=("PUT",))
        code = self.run_main(*self.PAIR, finds=[(STACK_A, CARD), (STACK_A, CARD)], call=fake)
        self.assertEqual(code, deck_write.EXIT_UNPROVEN)
        self.assertIn("part of this write reached the board", self.err)
        self.assertIn("do not post it again", self.err)
        fields = self.log_lines()[0].split("\t")
        self.assertEqual(fields[1:5], ["unproven", "4", "9", "comment+move"])
        self.assertIn("HTTP 403", fields[6])

    def test_a_lookup_that_fails_after_the_first_part_posted_is_unproven(self):
        fake = self.board()

        def finds():
            yield (STACK_A, CARD)
            raise OSError("network is unreachable")
        code = self.run_main(*self.PAIR, finds=finds(), call=fake)
        self.assertEqual(code, deck_write.EXIT_UNPROVEN)
        self.assertIn("NOT PROVEN", self.err)
        self.assertEqual(fake.methods, ["GET", "POST", "GET"])   # the move never left
        self.assertEqual(self.log_lines()[0].split("\t")[1], "unproven")

    def test_a_first_part_refused_is_an_ordinary_failure_and_the_second_never_runs(self):
        fake = self.board(refuse=("POST",))
        code = self.run_main(*self.PAIR, finds=[(STACK_A, CARD)], call=fake)
        self.assertIn("HTTP 403", code)
        self.assertNotIn("NOT PROVEN", self.err)
        self.assertEqual(fake.methods, ["GET", "POST"])
        self.assertEqual(self.log_lines()[0].split("\t")[1], "failed")

    def test_took_does_not_leak_from_one_write_into_the_next(self):
        self.run_main(*self.PAIR, finds=[(STACK_A, CARD), (STACK_A, CARD), (STACK_B, CARD)], call=self.board())
        code = self.run_main("comment", "--card", "9", "--text", SAY, finds=[(STACK_A, CARD)], call=self.board(refuse=("POST",)))
        self.assertIn("HTTP 403", code)   # a plain failure: the earlier visit's writes are not counted against this one
        self.assertEqual(self.log_lines()[-1].split("\t")[1], "failed")


class QueueAndLog(Harness):
    def queue(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = deck_write.main(["queue", "--env", "unused.cfg", "4", "title", "--card", "9", "--text", "Made up queued title", *extra])
        return code, out.getvalue()

    def test_queue_has_its_own_exit_code_and_says_not_posted(self):
        code, out = self.queue()
        self.assertEqual(code, deck_write.EXIT_QUEUED)
        self.assertEqual(deck_write.EXIT_QUEUED, 3)
        self.assertTrue(out.startswith("NOT POSTED YET: queued "), out)
        self.assertIn("; 1 waiting.", out)
        self.assertIn("deck_write.py log", out)
        lines = self.log_lines()
        self.assertEqual(len(lines), 1)
        fields = lines[0].split("\t")
        self.assertEqual(fields[1:5], ["queued", "4", "9", "title"])
        self.assertEqual(fields[5], deck_write.load_queue()[0]["id"])
        self.assertNotIn("queued title", lines[0])

    def test_log_subcommand_counts_by_date_and_all(self):
        path = deck_write.state_dir() / "deck-write-log.tsv"
        rows = ["2026-11-10T15:00:00+00:00\tposted\t4\t9\tcomment\t-\t",
                "2026-11-10T15:01:00+00:00\tqueued\t4\t9\ttitle\tab12\t",
                "2026-11-09T15:00:00+00:00\tfailed\t4\t9\ttitle\t-\tHTTP 500",
                "2026-11-10T15:02:00+00:00\tunproven\t4\t9\tdue\t-\tx"]
        path.write_text("\n".join(rows) + "\n")
        for argv, last in ((["--date", "2026-11-10"], "1 queued, 1 posted, 0 failed, 1 unproven"),
                           (["--all"], "1 queued, 1 posted, 1 failed, 1 unproven"),
                           (["--date", "2026-01-01"], "0 queued, 0 posted, 0 failed, 0 unproven")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(deck_write.main(["log", *argv]), 0)
            self.assertEqual(out.getvalue().splitlines()[-1], last)

    def test_log_with_no_file(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            deck_write.main(["log", "--all"])
        self.assertEqual(out.getvalue().strip(), "0 queued, 0 posted, 0 failed, 0 unproven")

    def test_a_log_that_cannot_be_written_does_not_stop_the_write(self):
        with mock.patch.object(deck_write, "log_path", side_effect=OSError("nope")):
            code = self.run_main("title", "--card", "9", "--text", "New", finds=[(STACK_A, CARD), (STACK_A, dict(CARD, title="New"))])
        self.assertEqual(code, "returned")

    def test_refusal_for_the_wait_and_dry_run_and_show_log_nothing(self):
        deck_write.record_write()
        self.run_main("title", "--card", "9", "--text", "New", finds=[], gap_clear=False)
        self.run_main("--dry-run", "title", "--card", "9", "--text", "New", finds=[])
        self.run_main("show", "--card", "9", finds=[(STACK_A, CARD)])
        self.assertEqual(self.log_lines(), [])


NOON = deck_write.datetime(2026, 11, 11, 17, 0, tzinfo=deck_write.timezone.utc)   # noon Eastern, inside the window


class DrainSafety(Harness):
    def setUp(self):
        super().setUp()
        clock = mock.patch.object(deck_write, "now", return_value=NOON)
        clock.start()
        self.addCleanup(clock.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            deck_write.main(["queue", "--env", "unused.cfg", "4", "title", "--card", "9", "--text", "New"])
        self.qid = deck_write.load_queue()[0]["id"]

    def drain(self, main_effect=None):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            if main_effect is None:
                code = deck_write.main(["drain"])
            else:
                with mock.patch.object(deck_write, "main", side_effect=main_effect) as m:
                    code = deck_write.cmd_drain([])
                self.main_mock = m
        self.drain_err = err.getvalue()
        return code

    def entry(self):
        return deck_write.load_queue()[0]

    def test_success_removes_the_entry_and_logs_posted_with_the_queue_id(self):
        finds = iter([(STACK_A, CARD), (STACK_A, dict(CARD, title="New"))])
        with (mock.patch.object(deck_write, "read_env", return_value=dict(CREDS)),
              mock.patch.object(deck_write, "find_card", side_effect=lambda *a: next(finds)),
              mock.patch.object(deck_write, "call", return_value={})):
            self.assertEqual(self.drain(), 0)
        self.assertEqual(deck_write.load_queue(), [])
        queued, posted = [ln.split("\t") for ln in self.log_lines()]
        self.assertEqual((queued[1], posted[1]), ("queued", "posted"))
        self.assertEqual(queued[5], self.qid)
        self.assertEqual(posted[5], self.qid)

    def test_unproven_is_parked_and_never_retried(self):
        self.assertEqual(self.drain(SystemExit(deck_write.EXIT_UNPROVEN)), 1)
        self.assertEqual(self.entry()["status"], "unproven")
        self.assertIn("NOT PROVEN", self.drain_err)
        self.assertIn("it will not be retried", self.drain_err)
        self.assertEqual(self.drain(SystemExit(0)), 0)
        self.main_mock.assert_not_called()

    def test_an_exception_that_is_not_a_system_exit_is_parked_as_unproven(self):
        self.assertEqual(self.drain(TimeoutError("timed out")), 1)
        self.assertEqual(self.entry()["status"], "unproven")
        self.assertIn("timed out", self.entry()["last_error"])

    def test_an_ordinary_failure_is_retried_as_before(self):
        self.assertEqual(self.drain(SystemExit("deck_write: HTTP 403")), 1)
        e = self.entry()
        self.assertEqual((e["status"], e["attempts"]), ("queued", 1))
        self.assertTrue(e["not_before"])
        self.assertNotIn("posting_since", e)

    def real_drain(self, call, finds):
        """The drain with the real main under it and a fake board, so the whole path from queue to exit code runs."""
        finds = iter(finds)
        with (mock.patch.object(deck_write, "read_env", return_value=dict(CREDS)),
              mock.patch.object(deck_write, "find_card", side_effect=lambda *a: next(finds)),
              mock.patch.object(deck_write, "find_stack", return_value=STACK_B),
              mock.patch.object(deck_write, "call", side_effect=call) as request):
            code = self.drain()
        self.request = request
        return code

    def test_a_lost_connection_before_anything_was_sent_is_retried(self):
        def finds():
            raise OSError("network is unreachable")
            yield
        self.assertEqual(self.real_drain(lambda *a, **k: {}, finds()), 1)
        e = self.entry()
        self.assertEqual((e["status"], e["attempts"]), ("queued", 1))
        self.assertIn("OSError", e["last_error"])
        self.request.assert_not_called()

    def test_a_write_whose_answer_never_came_is_parked_and_never_sent_again(self):
        def lost(c, method, path, body=None, api=None):
            raise TimeoutError("timed out")
        self.assertEqual(self.real_drain(lost, [(STACK_A, CARD)]), 1)
        e = self.entry()
        self.assertEqual((e["status"], e["attempts"]), ("unproven", 0))
        self.assertIn("it will not be retried", self.drain_err)
        self.assertEqual(self.request.call_count, 1)
        deck_write.last_write_path().unlink(missing_ok=True)            # the wait is over, so only the parking holds it back
        self.assertEqual(self.real_drain(lost, [(STACK_A, CARD)]), 0)   # a later drain leaves it alone
        self.request.assert_not_called()

    def test_a_queued_pair_whose_second_part_fails_is_parked_not_posted_twice(self):
        with contextlib.redirect_stdout(io.StringIO()):
            deck_write.main(["queue", "--env", "unused.cfg", "4", *TwoPartVisit.PAIR])
        deck_write.save_queue(deck_write.load_queue()[1:])   # only the pair waits
        posts = []

        def fake(c, method, path, body=None, api=None):
            if method == "GET":
                return {"ocs": {"data": [{"id": 31, "message": SAY}] if posts else []}}
            if method == "PUT":
                raise SystemExit("deck_write: PUT /cards/9/reorder: HTTP 403")
            posts.append(path)
            return {"ocs": {"data": {"id": 31}}}
        self.assertEqual(self.real_drain(fake, [(STACK_A, CARD), (STACK_A, CARD)]), 1)
        e = self.entry()
        self.assertEqual((e["status"], e["attempts"]), ("unproven", 0))
        deck_write.last_write_path().unlink(missing_ok=True)   # the wait is over, so only the parking holds it back
        self.assertEqual(self.real_drain(fake, [(STACK_A, CARD), (STACK_A, CARD)]), 0)
        self.assertEqual(posts, ["/cards/9/comments"])   # the comment went out once, and only once

    def test_a_refusal_for_the_wait_puts_it_back(self):
        self.assertEqual(self.drain(SystemExit(deck_write.EXIT_WAIT)), 0)
        self.assertEqual(self.entry()["status"], "queued")
        self.assertNotIn("posting_since", self.entry())

    def test_the_entry_is_marked_posting_before_the_write_starts(self):
        seen = []
        self.drain(lambda argv: seen.append(deck_write.load_queue()[0]["status"]))
        self.assertEqual(seen, ["posting"])

    def test_an_entry_left_posting_is_never_picked_again(self):
        entries = deck_write.load_queue()
        entries[0]["status"] = "posting"
        entries[0]["posting_since"] = NOON.isoformat()
        deck_write.save_queue(entries)
        self.assertEqual(self.drain(SystemExit(0)), 0)
        self.main_mock.assert_not_called()

    def test_queue_status_counts_unproven_and_stuck_apart_from_queued(self):
        entries = deck_write.load_queue()
        old = (NOON - deck_write.timedelta(seconds=300)).isoformat()
        entries += [dict(entries[0], id="u1", status="unproven"),
                    dict(entries[0], id="p1", status="posting", posting_since=old),
                    dict(entries[0], id="p2", status="posting", posting_since=NOON.isoformat())]
        deck_write.save_queue(entries)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            deck_write.main(["queue-status"])
        line = out.getvalue()
        self.assertTrue(line.startswith("1 queued"), line)
        self.assertIn(", 1 not proven (check by hand)", line)
        self.assertIn(", 1 stuck mid-post (check by hand)", line)


if __name__ == "__main__":
    unittest.main()
