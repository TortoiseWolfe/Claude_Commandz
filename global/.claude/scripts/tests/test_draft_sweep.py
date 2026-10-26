"""test_draft_sweep.py: unit tests for notes_check.py --sweep (deleting handled fleet-note drafts).

Stdlib only, no network: gmail_api.api is replaced by a fake Drafts folder.
"""

import base64
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import gmail_api as g  # noqa: E402
import notes_check as nc  # noqa: E402
from tmpdirs import fresh_dir  # noqa: E402

NOW = 1_800_000_000.0
H = 3600.0


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class FakeFolder:
    def __init__(self):
        self.drafts = {}      # draft id -> dict(subject, age_h, to, labels, body, mid)
        self.mail = {}        # message id -> dict(subject, age_h, labels)
        self.mail_by_topic = {}
        self.deleted = []
        self.calls = []
        self.fail_raw = False

    def add(self, did, subject, age_h, to="", labels=None, body="", mid=None):
        self.drafts[did] = dict(subject=subject, age_h=age_h, to=to, labels=labels or ["DRAFT"], body=body,
                                mid=mid or "m-" + did)

    def _msg(self, d, mid, fmt):
        headers = [{"name": "Subject", "value": d["subject"]}, {"name": "Date", "value": "Mon, 26 Oct 2026 10:00:00 -0400"}]
        if d.get("to"):
            headers.append({"name": "To", "value": d["to"]})
        m = {"id": mid, "labelIds": d["labels"], "internalDate": str(int((NOW - d["age_h"] * H) * 1000)),
             "payload": {"headers": headers}}
        if fmt == "full":
            m["payload"] = {"headers": headers, "mimeType": "text/plain", "body": {"data": b64(d["body"])}}
        if fmt == "raw":
            m["raw"] = b64("Subject: " + d["subject"] + "\n\n" + d["body"])
        return m

    def __call__(self, method, path, params=None, body=None):
        params = params or {}
        self.calls.append((method, path))
        if method == "GET" and path == "/profile":
            return {"emailAddress": "Owner@Example.com"}
        if method == "GET" and path == "/drafts":
            return {"drafts": [{"id": k, "message": {"id": v["mid"]}} for k, v in self.drafts.items()]}
        if method == "GET" and path.startswith("/drafts/"):
            did = path.split("/")[-1]
            if did not in self.drafts:
                raise g.GmailError(f"Gmail API 404 on GET {path}: not found")
            d = self.drafts[did]
            if params.get("format") == "raw" and self.fail_raw:
                raise g.GmailError("Gmail API 500 on GET: boom")
            return {"id": did, "message": self._msg(d, d["mid"], params.get("format"))}
        if method == "DELETE" and path.startswith("/drafts/"):
            did = path.split("/")[-1]
            self.deleted.append(did)
            self.drafts.pop(did, None)
            return {}
        if method == "GET" and path == "/messages":
            q = params.get("q", "")
            return {"messages": [{"id": m} for m, d in self.mail.items() if q.split('"')[1] in d["subject"]]}
        if method == "GET" and path.startswith("/messages/"):
            mid = path.split("/")[-1]
            return self._msg(self.mail[mid], mid, "metadata")
        raise g.GmailError(f"fake: unhandled {method} {path}")


class SweepBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(fresh_dir(self))
        self.processed = self.dir / "processed.txt"
        self.processed.write_text("", encoding="utf-8")
        self.sweep_dir = self.dir / "sweep"
        self.log = nc.Logger(str(self.dir / "log.txt"))
        self.fake = FakeFolder()
        p = mock.patch.object(g, "api", self.fake)
        p.start()
        self.addCleanup(p.stop)

    def run_sweep(self, apply=False):
        out = io.StringIO()
        with redirect_stdout(out):
            res = nc.sweep(str(self.processed), self.log, apply=apply, now=NOW, sweep_dir=str(self.sweep_dir), out=out)
        return res, out.getvalue()

    def mark(self, *ids):
        self.processed.write_text("".join(i + "\n" for i in ids), encoding="utf-8")


class Classify(unittest.TestCase):
    def test_tags(self):
        self.assertEqual(nc.classify("[MUSE>CC] hi"), ("inbound", "[MUSE>CC]"))
        self.assertEqual(nc.classify("[NCC-74656>NX-01] hi"), ("inbound", "[NCC-74656>NX-01]"))
        self.assertEqual(nc.classify("[CC>MUSE] hi"), ("outbound", "[CC>MUSE]"))
        self.assertEqual(nc.classify("[NX-01>NCC-74656] hi"), ("outbound", "[NX-01>NCC-74656]"))

    def test_reply_to_our_tag_is_inbound(self):
        self.assertEqual(nc.classify("Re: [CC>MUSE] topic"), ("inbound", "Re: [CC>MUSE]"))

    def test_no_tag_and_other_machines_tags_are_not_fleet(self):
        self.assertEqual(nc.classify("Tomorrow Tue 4:00 call"), (None, None))
        self.assertEqual(nc.classify("[MUSE>NCC-74656] not ours"), (None, None))


class Inbound(SweepBase):
    def test_processed_and_old_is_deleted_after_archiving(self):
        self.fake.add("r-1", "[MUSE>CC] done thing", 5, body="the note text")
        self.mark("r-1")
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])
        self.assertEqual(res["deleted"], 1)
        saved = [json.loads(x) for x in (self.sweep_dir / "2027-01-15.jsonl").read_text().splitlines()]
        self.assertEqual(saved[0]["draft_id"], "r-1")
        self.assertIn("the note text", base64.urlsafe_b64decode(saved[0]["raw"] + "==").decode())
        self.assertLess(self.fake.calls.index(("GET", "/drafts/r-1")), self.fake.calls.index(("DELETE", "/drafts/r-1")))

    def test_message_id_in_processed_file_counts(self):
        self.fake.add("r-1", "[NCC-74656>NX-01] tower note", 5, mid="mm1")
        self.mark("mm1")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_under_two_hours_is_kept(self):
        self.fake.add("r-1", "[MUSE>CC] fresh", 1.5)
        self.mark("r-1")
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("under 2 hours old", out)

    def test_unprocessed_is_kept_and_stale_is_flagged(self):
        self.fake.add("r-1", "[MUSE>CC] unread", 30)
        self.fake.add("r-2", "[MUSE>CC] very old unread", 100)
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("not handled yet", out)
        self.assertIn("never seen by notes_check", out)

    def test_label_32_counts_as_handled(self):
        self.fake.add("r-1", "[MUSE>CC] labelled", 5, labels=["DRAFT", "Label_32"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_reply_to_our_note_is_inbound(self):
        self.fake.add("r-1", "Re: [CC>MUSE] topic", 5)
        self.mark("r-1")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_recipient_outside_owner_is_kept(self):
        self.fake.add("r-1", "[MUSE>CC] odd", 5, to="client@example.com")
        self.mark("r-1")
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("addressed outside", out)

    def test_second_recipient_outside_is_kept(self):
        self.fake.add("r-1", "[MUSE>CC] odd", 5, to="Owner <owner@example.com>, other@example.com")
        self.mark("r-1")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])

    def test_owner_as_recipient_is_fine(self):
        self.fake.add("r-1", "[MUSE>CC] ok", 5, to="Owner <owner@example.com>")
        self.mark("r-1")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])


class Untagged(SweepBase):
    def test_non_fleet_draft_is_never_touched_even_if_id_listed(self):
        self.fake.add("r-1", "Re: Lunch on Friday", 200, body="plain mail, no signature")
        self.fake.add("r-2", "Tomorrow call", 10)
        self.mark("r-1", "r-2")
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(res["other"], 2)
        self.assertIn("not fleet notes remaining: 2", out)

    def test_untagged_h7ch_note_needs_processed_id_and_signature(self):
        self.fake.add("r-hatch", "Re: Weekly notes", 5, body="done.\n-- H-7CH")
        self.fake.add("r-unprocessed", "Re: other", 5, body="done.\n-- H-7CH")
        self.mark("r-hatch")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-hatch"])

    def test_untagged_h7ch_under_two_hours_is_kept(self):
        self.fake.add("r-hatch", "Re: fresh", 1, body="-- H-7CH")
        self.mark("r-hatch")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])


class Outbound(SweepBase):
    def test_three_days_old_is_deleted(self):
        self.fake.add("r-1", "[CC>MUSE] old ask", 73)
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_young_unanswered_is_kept(self):
        self.fake.add("r-1", "[NX-01>NCC-74656] ask", 10)
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("waiting for a reply", out)

    def test_picked_up_label_deletes(self):
        self.fake.add("r-1", "[CC>MUSE] ask", 10, labels=["DRAFT", "Label_32"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_later_reply_on_the_topic_deletes(self):
        self.fake.add("r-1", "[CC>MUSE] Release links", 10)
        self.fake.mail["m-reply"] = dict(subject="Re: [CC>MUSE] Release links", age_h=2, labels=["DRAFT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_untagged_re_on_the_topic_deletes(self):
        self.fake.add("r-1", "[CC>MUSE] Fix is up", 10)
        self.fake.mail["m-reply"] = dict(subject="Re: Fix is up", age_h=2, labels=["SENT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_reply_older_than_our_note_does_not_count(self):
        self.fake.add("r-1", "[CC>MUSE] Release links", 10)
        self.fake.mail["m-old"] = dict(subject="Re: [CC>MUSE] Release links", age_h=20, labels=["DRAFT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])

    def test_a_second_note_of_ours_is_not_a_reply(self):
        self.fake.add("r-1", "[CC>MUSE] Release links", 10)
        self.fake.mail["m-ours"] = dict(subject="[CC>MUSE] Release links (again)", age_h=2, labels=["DRAFT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])


class Modes(SweepBase):
    def test_dry_run_deletes_nothing_and_reports_counts_and_oldest(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5)
        self.fake.add("r-2", "[MUSE>CC] b", 50)
        self.fake.add("r-3", "[CC>MUSE] c", 80)
        self.fake.add("r-4", "Real draft", 5)
        self.mark("r-1", "r-2")
        res, out = self.run_sweep(apply=False)
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(res["would_delete"], 3)
        self.assertIn("DRY RUN", out)
        self.assertIn("[MUSE>CC]: 2 (delete 2, keep 0)", out)
        self.assertIn("[CC>MUSE]: 1 (delete 1, keep 0)", out)
        self.assertIn("oldest fleet draft:", out)
        self.assertIn("not fleet notes remaining: 1", out)
        self.assertFalse(self.sweep_dir.exists())

    def test_archive_failure_means_no_delete(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5)
        self.mark("r-1")
        self.fake.fail_raw = True
        # _api_retry only retries quota errors, so a 500 on the raw read surfaces as an archive failure
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(res["deleted"], 0)

    def test_draft_gone_while_running_is_skipped(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5)
        orig = self.fake.__call__
        calls = {"n": 0}

        def vanish(method, path, params=None, body=None):
            if method == "GET" and path == "/drafts/r-1" and (params or {}).get("format") == "metadata":
                raise g.GmailError("Gmail API 404 on GET /drafts/r-1: gone")
            return orig(method, path, params, body)

        with mock.patch.object(g, "api", vanish):
            res, out = self.run_sweep(apply=True)
        self.assertEqual(res["drafts"], 0)

    def test_main_sweep_flag_is_a_dry_run_without_apply(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5)
        self.mark("r-1")
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(nc.time, "time", return_value=NOW):
            code = nc.main(["--sweep", "--processed", str(self.processed), "--log", str(self.dir / "l.txt")])
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("DRY RUN", out.getvalue())


class OwnerLookup(SweepBase):
    def test_owner_is_read_from_the_account_once_per_run(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5)
        self.fake.add("r-2", "[MUSE>CC] b", 5)
        self.mark("r-1", "r-2")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.calls.count(("GET", "/profile")), 1)

    def test_owner_match_ignores_case(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5, to="OWNER@example.com")
        self.mark("r-1")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_owner_can_be_injected(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5, to="someone@example.org")
        self.mark("r-1")
        out = io.StringIO()
        with redirect_stdout(out):
            nc.sweep(str(self.processed), self.log, apply=True, now=NOW, sweep_dir=str(self.sweep_dir), out=out,
                     owner="someone@example.org")
        self.assertEqual(self.fake.deleted, ["r-1"])
        self.assertNotIn(("GET", "/profile"), self.fake.calls)

    def test_failed_owner_lookup_deletes_nothing(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5)
        self.mark("r-1")
        orig = self.fake.__call__

        def no_profile(method, path, params=None, body=None):
            if path == "/profile":
                raise g.GmailError("Gmail API 500 on GET /profile: boom")
            return orig(method, path, params, body)

        with mock.patch.object(g, "api", no_profile):
            with self.assertRaises(g.GmailError):
                self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])

    def test_no_address_is_written_into_the_source(self):
        src = (HERE.parent / "notes_check.py").read_text()
        self.assertNotRegex(src, r"[\w.+-]+@(?!example\.)[\w-]+\.(com|net|org|digital)")


class Wiring(unittest.TestCase):
    def test_the_15_minute_status_task_runs_the_sweep(self):
        sh = (HERE.parent / "openclaw_status_refresh.sh").read_text()
        line = next(x for x in sh.splitlines() if x.startswith("timeout") and "notes_check.py" in x)
        self.assertIn("--sweep --apply", line)
        self.assertIn("|| echo", line)


if __name__ == "__main__":
    unittest.main()
