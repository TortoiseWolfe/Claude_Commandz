"""test_notes_check.py: unit tests for notes_check.py.

Stdlib only, no network: replaces gmail_api's API calls with a fake.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))

import gmail_api as g  # noqa: E402
import notes_check as nc  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402


class FakeNotesGmail:
    def __init__(self):
        self.drafts_by_query = {}
        self.messages_by_query = {}
        self.messages = {}
        self.draft_details = {}
        self.calls = []
        self.error_to_raise = None

    def __call__(self, method, path, params=None, body=None):
        params = params or {}
        self.calls.append((method, path, params))
        if self.error_to_raise:
            raise self.error_to_raise
        if method == "GET" and path == "/drafts":
            q = params.get("q", "")
            pages = self.drafts_by_query.get(q)
            if isinstance(pages, list) and pages and isinstance(pages[0], dict) and "items" in pages[0]:
                token = params.get("pageToken", "0")
                idx = int(token)
                page = pages[idx]
                res = {"drafts": page.get("items", [])}
                if idx + 1 < len(pages):
                    res["nextPageToken"] = str(idx + 1)
                return res
            return {"drafts": list(self.drafts_by_query.get(q, []))}
        if method == "GET" and path == "/messages":
            q = params.get("q", "")
            pages = self.messages_by_query.get(q)
            if isinstance(pages, list) and pages and isinstance(pages[0], dict) and "items" in pages[0]:
                token = params.get("pageToken", "0")
                idx = int(token)
                page = pages[idx]
                res = {"messages": page.get("items", [])}
                if idx + 1 < len(pages):
                    res["nextPageToken"] = str(idx + 1)
                return res
            return {"messages": list(self.messages_by_query.get(q, []))}
        if method == "GET" and path.startswith("/drafts/"):
            did = path.split("/")[-1]
            if did in self.draft_details:
                return self.draft_details[did]
            return {"id": did, "message": {"id": f"msg-for-{did}"}}
        if method == "GET" and path.startswith("/messages/"):
            mid = path.split("/")[-1]
            if mid in self.messages:
                return self.messages[mid]
            raise g.GmailError(f"fake: message {mid} not found")
        raise g.GmailError(f"fake: unhandled {method} {path}")


class TestNotesCheck(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(fresh_dir(self))
        self.processed_file = self.test_dir / "agent-notes-processed.txt"
        self.log_file = self.test_dir / "notes-check.log"
        self.fake = FakeNotesGmail()
        patcher = mock.patch.object(g, "api", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_message_detail(self, mid, from_hdr="Hatch <hatch@example.com>",
                           subject="MUSE>CC Test", date_hdr="Sun, 25 Oct 2026 12:00:00 -0400",
                           label_ids=None):
        self.fake.messages[mid] = {
            "id": mid,
            "labelIds": list(label_ids or ["DRAFT"]),
            "payload": {
                "headers": [
                    {"name": "From", "value": from_hdr},
                    {"name": "Subject", "value": subject},
                    {"name": "Date", "value": date_hdr},
                ]
            },
        }

    def run_check(self):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = nc.main(["--processed", str(self.processed_file), "--log", str(self.log_file)])
        return code, out.getvalue(), err.getvalue()

    def test_everything_already_processed_prints_none_new(self):
        # 1 draft and 1 mail match, but both are already in the processed file
        q_draft = nc.DRAFT_QUERIES[0]
        q_mail = nc.MAIL_QUERIES[0]
        self.fake.drafts_by_query[q_draft] = [{"id": "r-100", "message": {"id": "m-100"}}]
        self.fake.messages_by_query[q_mail] = [{"id": "m-200"}]
        self.processed_file.write_text("r-100\nm-200\n", encoding="utf-8")

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "notes: none new\n")
        self.assertEqual(stderr, "")
        # Step 2 dropped both: messages.get was never called for either
        msg_calls = [c for c in self.fake.calls if c[1].startswith("/messages/")]
        self.assertEqual(msg_calls, [])

    def test_one_new_draft_prints_one_note_line(self):
        q_draft = nc.DRAFT_QUERIES[0]
        self.fake.drafts_by_query[q_draft] = [{"id": "r-7150954647153477729", "message": {"id": "1a0ff8f6dc0c886e"}}]
        self.add_message_detail(
            "1a0ff8f6dc0c886e",
            from_hdr="Hatch <hatch@example.com>",
            subject="MUSE>CC plan review",
            date_hdr="Sun, 25 Oct 2026 14:15:00 -0400",
            label_ids=["DRAFT"],
        )

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            stdout,
            "NOTE r-7150954647153477729 | draft | Hatch <hatch@example.com> | MUSE>CC plan review | Sun, 25 Oct 2026 14:15:00 -0400\n",
        )

    def test_label_32_note_is_appended_to_processed_file_and_not_printed(self):
        q_draft = nc.DRAFT_QUERIES[0]
        q_mail = nc.MAIL_QUERIES[0]
        self.fake.drafts_by_query[q_draft] = [{"id": "r-draft-handled", "message": {"id": "m-draft-handled"}}]
        self.fake.messages_by_query[q_mail] = [{"id": "m-mail-handled"}]
        self.add_message_detail("m-draft-handled", label_ids=["DRAFT", "Label_32"])
        self.add_message_detail("m-mail-handled", label_ids=["INBOX", "Label_32"])

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "notes: none new\n")
        self.assertEqual(stderr, "")

        processed = self.processed_file.read_text(encoding="utf-8").splitlines()
        self.assertIn("r-draft-handled", processed)  # draft ID appended for draft
        self.assertIn("m-mail-handled", processed)   # message ID appended for mail

    def test_same_id_from_two_queries_prints_once(self):
        q1 = nc.DRAFT_QUERIES[0]
        q3 = nc.DRAFT_QUERIES[2]
        self.fake.drafts_by_query[q1] = [{"id": "r-dup", "message": {"id": "m-dup"}}]
        self.fake.drafts_by_query[q3] = [{"id": "r-dup", "message": {"id": "m-dup"}}]
        self.add_message_detail("m-dup", subject="Re: [CC>MUSE] update", date_hdr="Sun, 25 Oct 2026 11:00:00 -0400")

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        lines = stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("NOTE r-dup | draft | "))

    def test_hex_message_id_in_processed_file_drops_that_mail(self):
        q_mail = nc.MAIL_QUERIES[0]
        self.fake.messages_by_query[q_mail] = [
            {"id": "1a0ff8f6dc0c886e"},
            {"id": "1a0ff8f6dc0c9999"},
        ]
        self.processed_file.write_text("1a0ff8f6dc0c886e\n", encoding="utf-8")
        self.add_message_detail("1a0ff8f6dc0c9999", subject="[MUSE>CC] Fresh note")

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        self.assertNotIn("1a0ff8f6dc0c886e", stdout)
        self.assertIn("NOTE 1a0ff8f6dc0c9999 | mail | ", stdout)

    def test_hex_message_id_in_processed_file_drops_draft(self):
        q_draft = nc.DRAFT_QUERIES[0]
        self.fake.drafts_by_query[q_draft] = [
            {"id": "r-draft-999", "message": {"id": "1a0ff8f6dc0c886e"}},
        ]
        self.processed_file.write_text("1a0ff8f6dc0c886e\n", encoding="utf-8")

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "notes: none new\n")

    def test_gmail_error_exits_1_with_stderr_and_no_stdout(self):
        self.fake.error_to_raise = g.GmailError("Network connect failed: 503 Service Unavailable")

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Network connect failed: 503 Service Unavailable", stderr)

    def test_oldest_first_sorting(self):
        q = nc.MAIL_QUERIES[0]
        self.fake.messages_by_query[q] = [
            {"id": "m-later"},
            {"id": "m-earlier"},
        ]
        self.add_message_detail("m-later", subject="Later note", date_hdr="Sun, 25 Oct 2026 16:00:00 -0400")
        self.add_message_detail("m-earlier", subject="Earlier note", date_hdr="Sun, 25 Oct 2026 09:00:00 -0400")

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        lines = stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("NOTE m-earlier", lines[0])
        self.assertIn("NOTE m-later", lines[1])

    def test_paging_drafts_and_messages(self):
        q = nc.MAIL_QUERIES[0]
        self.fake.messages_by_query[q] = [
            {"items": [{"id": "m-p1"}]},
            {"items": [{"id": "m-p2"}]},
        ]
        self.add_message_detail("m-p1", subject="Page 1", date_hdr="Sun, 25 Oct 2026 10:00:00 -0400")
        self.add_message_detail("m-p2", subject="Page 2", date_hdr="Sun, 25 Oct 2026 11:00:00 -0400")

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        lines = stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("NOTE m-p1", lines[0])
        self.assertIn("NOTE m-p2", lines[1])

    def test_log_records_query_detail_and_drops(self):
        q = nc.DRAFT_QUERIES[0]
        self.fake.drafts_by_query[q] = [{"id": "r-logged", "message": {"id": "m-logged"}}]
        self.processed_file.write_text("r-logged\n", encoding="utf-8")

        code, stdout, stderr = self.run_check()
        self.assertEqual(code, 0)
        self.assertTrue(self.log_file.exists())
        log_text = self.log_file.read_text(encoding="utf-8")
        self.assertIn("start notes_check run", log_text)
        self.assertIn("drop draft r-logged", log_text)
        self.assertIn("finished notes_check run", log_text)


if __name__ == "__main__":
    unittest.main()
