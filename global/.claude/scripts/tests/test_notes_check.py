"""test_notes_check.py: unit tests for notes_check.py.

Stdlib only, no network: replaces gmail_api's API calls with a fake.
"""

import base64
import io
import json
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

    def make_command(self, name, source):
        bindir = self.test_dir / "bin"
        bindir.mkdir(exist_ok=True)
        command = bindir / name
        command.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
        command.chmod(0o755)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + old_path
        self.addCleanup(lambda: os.environ.__setitem__("PATH", old_path))
        return command

    def test_published_record_reads_origin_main_and_fetches_once(self):
        root = self.test_dir / "repos"
        local = root / "hub/tower/peer-notes-processed.txt"
        local.parent.mkdir(parents=True)
        local.write_text("r-local\n", encoding="utf-8")
        calls = self.test_dir / "git-calls.jsonl"
        self.make_command("git", """
import json, os, sys
with open(os.environ["GIT_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[3:5] == ["fetch", "-q"]:
    raise SystemExit(0)
if sys.argv[3:5] == ["show", "origin/main:hub/tower/peer-notes-processed.txt"]:
    sys.stdout.write("r-main\\n0123456789abcdef\\n")
    raise SystemExit(0)
raise SystemExit(2)
""")
        old_calls = os.environ.get("GIT_CALLS")
        os.environ["GIT_CALLS"] = str(calls)
        self.addCleanup(lambda: os.environ.pop("GIT_CALLS", None) if old_calls is None
                        else os.environ.__setitem__("GIT_CALLS", old_calls))
        with mock.patch.object(nc, "READER_RECORD_REPO", str(root)):
            records, sources = nc.load_records({"NCC-74656": str(local)})
        self.assertEqual(records, {"NCC-74656": {"r-main", "0123456789abcdef"}})
        self.assertEqual(sources, {"NCC-74656": "origin/main"})
        commands = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum("fetch" in call for call in commands), 1)

    def test_reader_record_falls_back_to_local_and_status_names_source_and_count(self):
        root = self.test_dir / "repos"
        local = root / "hub/tower/peer-notes-processed.txt"
        local.parent.mkdir(parents=True)
        local.write_text("r-local\n0123456789abcdef\n", encoding="utf-8")
        self.make_command("git", "import sys\nsys.exit(1)\n")
        with mock.patch.object(nc, "READER_RECORD_REPO", str(root)):
            records, sources = nc.load_records({"NCC-74656": str(local)})
        status = self.test_dir / "status.json"
        nc.write_status(str(status), 0, [], [], records, {"NCC-74656": str(local)}, sources)
        details = json.loads(status.read_text(encoding="utf-8"))["records"]["NCC-74656"]
        self.assertEqual(records["NCC-74656"], {"r-local", "0123456789abcdef"})
        self.assertEqual(details["source"], "local")
        self.assertEqual(details["ids"], 2)

    def test_published_read_record_uses_two_hour_age(self):
        rec = {"draft_id": "r-123456", "message_id": "0123456789abcdef",
               "subject": "[NX-01>NCC-74656] batch", "recipients": ["owner@example.com"],
               "t": 0, "labels": ["DRAFT"]}
        action, _, reason = nc.decide(
            rec, set(), 3 * 3600, {}, "owner@example.com", mine={"NX-01"},
            records={"NCC-74656": {"r-123456"}},
        )
        self.assertEqual(action, "delete")
        self.assertIn("over 2 hours old", reason)

    def test_unlisted_record_still_needs_24_hour_proof(self):
        rec = {"draft_id": "r-123456", "message_id": "0123456789abcdef",
               "subject": "[NX-01>NCC-74656] batch", "recipients": ["owner@example.com"],
               "t": 0, "labels": ["DRAFT"]}
        action, _, _ = nc.decide(
            rec, set(), 25 * 3600, {}, "owner@example.com", mine={"NX-01"},
            records={"NCC-74656": set()},
        )
        self.assertEqual(action, "keep")

    def test_other_absorption_proof_keeps_24_hour_age(self):
        rec = {"draft_id": "r-123456", "message_id": "0123456789abcdef",
               "subject": "[NX-01>NCC-74656] batch", "recipients": ["owner@example.com"],
               "t": 0, "labels": ["DRAFT", nc.LABEL_CC_PROCESSED]}
        action, _, reason = nc.decide(
            rec, set(), 3 * 3600, {}, "owner@example.com", mine={"NX-01"},
            records={"NCC-74656": set()},
        )
        self.assertEqual(action, "keep")
        self.assertEqual(reason, "under 24 hours old")

    def make_fake_gh(self):
        log = self.test_dir / "gh-calls.jsonl"
        self.make_command("gh", """
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\\n")
if args[0:2] == ["api", "-X"] and args[2] == "GET":
    response = os.environ["GH_GET"]
    if response == "404":
        sys.stderr.write("Not Found (HTTP 404)")
        raise SystemExit(1)
    sys.stdout.write(response)
""")
        old_calls, old_get = os.environ.get("GH_CALLS"), os.environ.get("GH_GET")
        os.environ["GH_CALLS"] = str(log)
        self.addCleanup(lambda: os.environ.pop("GH_CALLS", None) if old_calls is None
                        else os.environ.__setitem__("GH_CALLS", old_calls))
        self.addCleanup(lambda: os.environ.pop("GH_GET", None) if old_get is None
                        else os.environ.__setitem__("GH_GET", old_get))
        return log

    def test_publish_read_skips_unchanged_file(self):
        record = self.test_dir / "processed.txt"
        content = b"r123\n0123456789abcdef\n"
        record.write_bytes(content)
        log = self.make_fake_gh()
        os.environ["GH_GET"] = json.dumps({
            "sha": "sha-current", "content": base64.b64encode(content).decode("ascii"),
        })
        count, changed = nc.publish_read(str(record), "hub/tower/peer-notes-processed.txt")
        self.assertEqual((count, changed), (2, False))
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_publish_read_refuses_non_id_lines(self):
        record = self.test_dir / "processed.txt"
        record.write_text("r123\nnot-an-id\n", encoding="utf-8")
        with self.assertRaisesRegex(nc.PublishReadError, "line 2"):
            nc.publish_read(str(record), "hub/tower/peer-notes-processed.txt")

    def test_publish_read_sends_current_sha(self):
        record = self.test_dir / "processed.txt"
        content = b"r-123\n0123456789abcdef\n"
        record.write_bytes(content)
        log = self.make_fake_gh()
        os.environ["GH_GET"] = json.dumps({
            "sha": "sha-current", "content": base64.b64encode(b"old\n").decode("ascii"),
        })
        count, changed = nc.publish_read(str(record), "hub/tower/peer-notes-processed.txt")
        self.assertEqual((count, changed), (2, True))
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertIn("sha=sha-current", calls[1])
        self.assertIn(f"content={base64.b64encode(content).decode('ascii')}", calls[1])
        self.assertIn("message=Tower read record: 2 ids", calls[1])

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
