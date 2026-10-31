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
        self.labelled = []
        self.calls = []
        self.fail_raw = False
        self.page_size = None  # set to page /drafts like Gmail does

    def add(self, did, subject, age_h, to="", labels=None, body="", mid=None):
        self.drafts[did] = dict(subject=subject, age_h=age_h, to=to, labels=labels or ["DRAFT"], body=body,
                                mid=mid or "m-" + did)

    def _msg(self, d, mid, fmt):
        headers = [{"name": "Subject", "value": d["subject"]}, {"name": "From", "value": d.get("sender", "Owner <owner@example.com>")}, {"name": "Date", "value": "Mon, 26 Oct 2026 10:00:00 -0400"}]
        if d.get("to"):
            headers.append({"name": "To", "value": d["to"]})
        m = {"id": mid, "labelIds": d["labels"], "internalDate": str(int((NOW - d["age_h"] * H) * 1000)),
             "payload": {"headers": headers}}
        if fmt == "full":
            m["payload"] = {"headers": headers, "mimeType": "text/plain", "body": {"data": b64(d.get("body", ""))}}
        if fmt == "raw":
            m["raw"] = b64("Subject: " + d["subject"] + "\n\n" + d["body"])
        return m

    def __call__(self, method, path, params=None, body=None):
        params = params or {}
        self.calls.append((method, path))
        if method == "GET" and path == "/profile":
            return {"emailAddress": "Owner@Example.com"}
        if method == "GET" and path == "/drafts":
            items = [{"id": k, "message": {"id": v["mid"]}} for k, v in self.drafts.items()]
            if self.page_size:
                start = int(params.get("pageToken") or 0)
                page = {"drafts": items[start:start + self.page_size]}
                if start + self.page_size < len(items):
                    page["nextPageToken"] = str(start + self.page_size)
                return page
            return {"drafts": items}
        if method == "GET" and path.startswith("/drafts/"):
            did = path.split("/")[-1]
            if did not in self.drafts:
                raise g.GmailError(f"Gmail API 404 on GET {path}: not found")
            d = self.drafts[did]
            if params.get("format") == "raw" and self.fail_raw:
                raise g.GmailError("Gmail API 500 on GET: boom")
            return {"id": did, "message": self._msg(d, d["mid"], params.get("format"))}
        if method == "POST" and path.endswith("/modify"):
            mid = path.split("/")[-2]
            self.labelled.append((mid, tuple(body["addLabelIds"])))
            return {"id": mid}
        if method == "DELETE" and path.startswith("/drafts/"):
            did = path.split("/")[-1]
            self.deleted.append(did)
            self.drafts.pop(did, None)
            return {}
        if method == "GET" and path == "/messages":
            q = params.get("q", "")
            needle = q.split('"')[1]
            return {"messages": [{"id": m} for m, d in self.mail.items() if needle in d["subject"] or needle in d.get("body", "")]}
        if method == "GET" and path.startswith("/messages/"):
            mid = path.split("/")[-1]
            return self._msg(self.mail[mid], mid, params.get("format") or "metadata")
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

    mine = {"NX-01", "CC"}

    def run_sweep(self, apply=False):
        out = io.StringIO()
        with redirect_stdout(out):
            res = nc.sweep(str(self.processed), self.log, apply=apply, now=NOW, sweep_dir=str(self.sweep_dir), out=out,
                           mine=self.mine)
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
        self.assertEqual(nc.classify("[NCC-74656>MUSE] not ours"), (None, None))

    def test_the_tower_reads_its_own_names(self):
        tower = {"NCC-74656"}
        self.assertEqual(nc.classify("[MUSE>NCC-74656] hi", tower), ("inbound", "[MUSE>NCC-74656]"))
        self.assertEqual(nc.classify("[NX-01>NCC-74656] hi", tower), ("inbound", "[NX-01>NCC-74656]"))
        self.assertEqual(nc.classify("[NCC-74656>MUSE] hi", tower), ("outbound", "[NCC-74656>MUSE]"))
        self.assertEqual(nc.classify("[MUSE>CC] not the tower's", tower), (None, None))

    def test_machine_file_picks_the_names(self):
        self.assertEqual(nc.my_names("NX-01"), {"NX-01", "CC"})
        self.assertEqual(nc.my_names("NCC-74656"), {"NCC-74656"})


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

    def test_archive_is_owner_only(self):
        self.fake.add("r-1", "[MUSE>CC] done thing", 5, body="the note text")
        self.mark("r-1")
        self.run_sweep(apply=True)
        self.assertEqual(self.sweep_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.sweep_dir / "2027-01-15.jsonl").stat().st_mode & 0o777, 0o600)

    def test_message_id_in_processed_file_counts(self):
        self.fake.add("r-1", "[NCC-74656>NX-01] tower note", 80, mid="mm1")
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
        self.assertEqual(res["unabsorbed_old"], 2)

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


class Ownership(SweepBase):
    """Each machine deletes only its own (31 Oct): notes to it once it has read them, notes from it once absorption is
    verified and 24 hours have passed. Another party's note is kept whatever its labels."""

    def test_the_tower_deletes_notes_to_it_that_it_has_read(self):
        self.mine = {"NCC-74656"}
        self.fake.add("t-1", "[NX-01>NCC-74656] ask", 5)
        self.fake.add("t-2", "[MUSE>NCC-74656] ask", 5)
        self.fake.add("t-3", "[NCC-74656>NX-01] its own", 5)
        self.mark("t-1", "t-2", "t-3")
        self.run_sweep(apply=True)
        self.assertEqual(sorted(self.fake.deleted), ["t-1", "t-2"])

    def test_notes_not_naming_the_tower_are_unaffected(self):
        self.fake.add("n-1", "[MUSE>CC] done thing", 5)
        self.mark("n-1")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["n-1"])

    def test_tower_note_to_us_is_deleted_once_read(self):
        self.fake.add("o-in", "[NCC-74656>NX-01] reply", 5)
        self.mark("o-in")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["o-in"])


class LabelStep(SweepBase):
    """The reader's half of the shared signal: label what we have read so the author can see it."""

    def test_read_note_to_us_gets_both_labels_before_the_cleanup(self):
        self.fake.add("r-1", "[MUSE>CC] fresh", 1.0)       # too young to delete, still gets the label
        self.mark("r-1")
        res, _ = self.run_sweep(apply=True)
        self.assertEqual(self.fake.labelled, [("m-r-1", ("Label_31", "Label_32"))])
        self.assertEqual(res["labelled"], 1)
        self.assertEqual(self.fake.deleted, [])

    def test_dry_run_labels_nothing(self):
        self.fake.add("r-1", "[MUSE>CC] fresh", 1.0)
        self.mark("r-1")
        res, out = self.run_sweep(apply=False)
        self.assertEqual(self.fake.labelled, [])
        self.assertEqual(res["labelled"], 1)
        self.assertIn("would label", out)

    def test_already_labelled_unread_and_not_ours_get_nothing(self):
        self.fake.add("a", "[MUSE>CC] done", 1.0, labels=["DRAFT", "Label_32"])
        self.fake.add("b", "[MUSE>CC] unread", 1.0)
        self.fake.add("c", "[NX-01>NCC-74656] ours", 1.0)
        self.fake.add("d", "[MUSE>NCC-74656] the tower's", 1.0)
        self.mark("a", "c", "d")
        res, _ = self.run_sweep(apply=True)
        self.assertEqual(self.fake.labelled, [])
        self.assertEqual(res["labelled"], 0)

    def test_message_id_in_processed_file_also_labels(self):
        self.fake.add("r-1", "[MUSE>CC] fresh", 1.0, mid="1a2b3c")
        self.mark("1a2b3c")
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.labelled, [("1a2b3c", ("Label_31", "Label_32"))])

    def test_a_processed_file_per_machine_can_be_listed(self):
        other = self.dir / "peer-processed.txt"
        other.write_text("t-9\n", encoding="utf-8")
        self.mine = {"NCC-74656"}
        self.fake.add("t-9", "[NX-01>NCC-74656] ask", 30)
        out = io.StringIO()
        with redirect_stdout(out):
            nc.sweep([str(self.processed), str(other)], self.log, apply=True, now=NOW, sweep_dir=str(self.sweep_dir),
                     out=out, mine=self.mine)
        self.assertEqual(self.fake.deleted, ["t-9"])


class Outbound(SweepBase):
    """Our own notes leave only once absorption is verified AND 24 hours have passed. Age alone never deletes."""

    def test_three_days_old_with_no_absorption_is_kept_and_flagged(self):
        self.fake.add("r-1", "[CC>MUSE] old ask", 73)
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("absorption not verified", out)
        self.assertEqual(res["unabsorbed_old"], 1)

    def test_young_note_is_kept_even_when_labelled(self):
        self.fake.add("r-1", "[CC>MUSE] ask", 10, labels=["DRAFT", "Label_32"])
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("under 24 hours old", out)
        self.assertEqual(res["unabsorbed_old"], 0)

    def test_absorbed_label_and_over_24_hours_deletes(self):
        self.fake.add("r-1", "[CC>MUSE] ask", 30, labels=["DRAFT", "Label_32"])
        self.fake.add("r-2", "[NX-01>NCC-74656] ask", 30, labels=["DRAFT", "Label_31", "Label_32"])
        res, _ = self.run_sweep(apply=True)
        self.assertEqual(sorted(self.fake.deleted), ["r-1", "r-2"])
        self.assertEqual(res["unabsorbed_old"], 0)

    def test_just_under_24_hours_is_kept(self):
        self.fake.add("r-1", "[NX-01>NCC-74656] ask", 23.9, labels=["DRAFT", "Label_32"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])

    def test_later_reply_on_the_topic_deletes(self):
        self.fake.add("r-1", "[CC>MUSE] Release links", 30)
        self.fake.mail["m-reply"] = dict(subject="Re: [CC>MUSE] Release links", age_h=2, labels=["DRAFT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_reply_on_a_note_under_24_hours_old_keeps_it(self):
        self.fake.add("r-1", "[CC>MUSE] Release links", 10)
        self.fake.mail["m-reply"] = dict(subject="Re: [CC>MUSE] Release links", age_h=2, labels=["DRAFT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])

    def test_untagged_re_on_the_topic_deletes(self):
        self.fake.add("r-1", "[CC>MUSE] Fix is up", 30)
        self.fake.mail["m-reply"] = dict(subject="Re: Fix is up", age_h=2, labels=["SENT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, ["r-1"])

    def test_reply_from_an_outside_sender_does_not_count(self):
        self.fake.add("r-1", "[CC>MUSE] Release links", 30)
        self.fake.mail["m-x"] = dict(subject="Re: [CC>MUSE] Release links", age_h=2, labels=["INBOX"],
                                     sender="Stranger <stranger@example.org>")
        res, out = self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("absorption not verified", out)

    def test_reply_older_than_our_note_does_not_count(self):
        self.fake.add("r-1", "[CC>MUSE] Release links", 30)
        self.fake.mail["m-old"] = dict(subject="Re: [CC>MUSE] Release links", age_h=60, labels=["DRAFT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])

    def test_a_second_note_of_ours_is_not_a_reply(self):
        self.fake.add("r-1", "[CC>MUSE] Release links", 30)
        self.fake.mail["m-ours"] = dict(subject="[CC>MUSE] Release links (again)", age_h=2, labels=["DRAFT"])
        self.run_sweep(apply=True)
        self.assertEqual(self.fake.deleted, [])


READ_RECORD = "t-99\n"


class Relay(SweepBase):
    """Notes between Hatch (MUSE) and the tower (NCC-74656): NX-01 sweeps them, but only once the note's READER has absorbed
    it (31 Oct). The tower cannot sweep (no Gmail token); nobody else would clear them."""

    def record(self, text):
        path = self.dir / "tower-record.txt"
        path.write_text(text, encoding="utf-8")
        return {"NCC-74656": str(path)}

    def sweep_with(self, records=None, apply=True, status=None):
        out = io.StringIO()
        with redirect_stdout(out):
            res = nc.sweep(str(self.processed), self.log, apply=apply, now=NOW, sweep_dir=str(self.sweep_dir), out=out,
                           mine=self.mine, reader_records=records if records is not None else {}, status_path=status)
        return res, out.getvalue()

    # --- the readers' proofs
    def test_tower_to_hatch_note_with_label_32_is_deleted(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] game bot account", 30, labels=["DRAFT", "Label_32"])
        self.sweep_with()
        self.assertEqual(self.fake.deleted, ["t-1"])

    def test_hatch_to_tower_note_is_deleted_when_the_tower_published_its_id(self):
        self.fake.add("t-99", "[MUSE>NCC-74656] power photos", 30)
        self.sweep_with(self.record(READ_RECORD))
        self.assertEqual(self.fake.deleted, ["t-99"])

    def test_the_message_id_in_the_towers_record_also_counts(self):
        self.fake.add("t-5", "[NX-01>NCC-74656] hub ask", 30, mid="mm5")
        self.sweep_with(self.record("mm5\n"))
        self.assertEqual(self.fake.deleted, ["t-5"])

    def test_the_towers_record_does_not_clear_a_note_Hatch_reads(self):
        self.fake.add("t-99", "[NCC-74656>MUSE] for Hatch", 30)
        self.sweep_with(self.record(READ_RECORD))
        self.assertEqual(self.fake.deleted, [])

    def test_the_hatch_note_is_kept_when_the_tower_record_is_missing(self):
        self.fake.add("t-99", "[MUSE>NCC-74656] power photos", 30)
        res, out = self.sweep_with({"NCC-74656": str(self.dir / "nope.txt")})
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(res["unabsorbed_old"], 1)

    def test_a_later_reply_from_hatch_clears_the_tower_note(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] Release links", 30)
        self.fake.mail["m-reply"] = dict(subject="Re: [NCC-74656>MUSE] Release links", age_h=2, labels=["DRAFT"])
        self.sweep_with()
        self.assertEqual(self.fake.deleted, ["t-1"])

    def test_a_tagged_note_from_hatch_to_the_tower_on_the_topic_counts_as_the_answer(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] Game nights", 30)
        self.fake.mail["m-n"] = dict(subject="[MUSE>NCC-74656] Game nights: account made", age_h=2, labels=["DRAFT"])
        self.sweep_with()
        self.assertEqual(self.fake.deleted, ["t-1"])

    def test_a_later_note_from_the_AUTHOR_is_not_the_readers_proof(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] Game nights", 30)
        self.fake.mail["m-n"] = dict(subject="[NCC-74656>MUSE] Game nights (second copy)", age_h=2, labels=["DRAFT"])
        self.fake.mail["m-r"] = dict(subject="Re: [MUSE>NCC-74656] Game nights", age_h=1, labels=["DRAFT"])
        self.fake.mail["m-r"]["sender"] = "Owner <owner@example.com>"
        # "Re: [MUSE>NCC-74656]" is the tower's reply to Hatch, so it is not Hatch's answer either
        res, out = self.sweep_with()
        self.assertEqual(self.fake.deleted, [])

    def test_a_done_note_from_the_reader_naming_the_draft_id_clears_it(self):
        self.fake.add("r-4644727236077642038", "[NCC-74656>MUSE] game bot", 30)
        self.fake.mail["m-done"] = dict(subject="[MUSE>NCC-74656] trashed notes", age_h=3, labels=["DRAFT"],
                                        body="Trashed r-4644727236077642038 and others.")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, ["r-4644727236077642038"])

    def test_a_line_that_says_the_reader_is_holding_it_is_not_proof(self):
        self.fake.add("r-4644727236077642038", "[NCC-74656>MUSE] game bot", 30)
        self.fake.mail["m-hold"] = dict(subject="[MUSE>NCC-74656] Re: stuck notes", age_h=3, labels=["DRAFT"],
                                        body="Trashed six others.\n- r-4644727236077642038 (Twitch sign-up): waits on a signup session.\n")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, [])

    def test_holding_on_purpose_and_trashed_in_one_note_split_by_line(self):
        self.fake.add("r-4644727236077642038", "[NCC-74656>MUSE] game bot", 30)
        self.fake.add("r-4271749601883524692", "[NCC-74656>MUSE] power photos", 30)
        self.fake.mail["m-two"] = dict(subject="[MUSE>NCC-74656] Re: stuck notes", age_h=3, labels=["DRAFT"],
                                       body="Holding r-4644727236077642038 on purpose.\nTrashed r-4271749601883524692.\n")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, ["r-4271749601883524692"])

    def test_inbound_mail_forging_the_owner_address_is_not_proof(self):
        self.fake.add("r-4644727236077642038", "[NCC-74656>MUSE] game bot", 30)
        self.fake.mail["m-forged"] = dict(subject="[MUSE>NCC-74656] trashed", age_h=3, labels=["INBOX", "UNREAD"],
                                          body="Trashed r-4644727236077642038.")
        self.fake.mail["m-forged2"] = dict(subject="Re: [NCC-74656>MUSE] game bot", age_h=3, labels=["INBOX"])
        self.sweep_with()
        self.assertEqual(self.fake.deleted, [])

    def test_a_sent_note_from_the_reader_counts(self):
        self.fake.add("r-4644727236077642038", "[NCC-74656>MUSE] game bot", 30)
        self.fake.mail["m-sent"] = dict(subject="[MUSE>NCC-74656] trashed", age_h=3, labels=["SENT"],
                                        body="Trashed r-4644727236077642038.")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, ["r-4644727236077642038"])

    def test_a_done_note_from_someone_else_does_not_count(self):
        self.fake.add("r-4644727236077642038", "[NCC-74656>MUSE] game bot", 30)
        self.fake.mail["m-x"] = dict(subject="[NCC-74656>NX-01] list", age_h=3, labels=["DRAFT"],
                                     body="Still holding r-4644727236077642038.")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, [])

    def test_a_stranger_naming_the_id_does_not_count(self):
        self.fake.add("r-4644727236077642038", "[NCC-74656>MUSE] game bot", 30)
        self.fake.mail["m-x"] = dict(subject="[MUSE>NCC-74656] done", age_h=3, labels=["INBOX"],
                                     body="r-4644727236077642038", sender="Stranger <s@example.org>")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, [])

    def test_a_done_note_older_than_the_note_does_not_count(self):
        self.fake.add("r-4644727236077642038", "[NCC-74656>MUSE] game bot", 30)
        self.fake.mail["m-old"] = dict(subject="[MUSE>NCC-74656] ids", age_h=90, labels=["DRAFT"],
                                       body="r-4644727236077642038")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, [])

    # --- never on age alone, never early
    def test_no_proof_means_kept_however_old(self):
        for i, subj in enumerate(["[NCC-74656>MUSE] a", "[MUSE>NCC-74656] b", "[NX-01>NCC-74656] c", "[NCC-74656>NX-01]x"]):
            self.fake.add(f"t-{i}", subj, 400)
        res, out = self.sweep_with(apply=True)
        # [NCC-74656>NX-01] is inbound for NX-01 (not handled yet); the other three have no proof
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(res["unabsorbed_old"], 4)

    def test_proof_but_under_24_hours_is_kept(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] fresh", 10, labels=["DRAFT", "Label_32"])
        self.sweep_with()
        self.assertEqual(self.fake.deleted, [])

    def test_every_deleted_relay_note_is_archived_first(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] x", 30, labels=["DRAFT", "Label_32"], body="the tower text")
        self.sweep_with()
        saved = [json.loads(x) for x in (self.sweep_dir / "2027-01-15.jsonl").read_text().splitlines()]
        self.assertEqual(saved[0]["draft_id"], "t-1")
        self.assertLess(self.fake.calls.index(("GET", "/drafts/t-1")), self.fake.calls.index(("DELETE", "/drafts/t-1")))

    def test_a_relay_note_addressed_outside_the_mailbox_is_kept(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] x", 30, labels=["DRAFT", "Label_32"], to="client@example.com")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, [])

    def test_relay_notes_are_not_labelled_or_touched_by_the_label_step(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] x", 30)
        self.mark("t-1")  # our own processed file says nothing about Hatch's notes
        self.sweep_with()
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(self.fake.labelled, [])

    def test_a_non_fleet_draft_is_never_touched(self):
        self.fake.add("g-1", "Gail: held reply", 900, labels=["DRAFT", "Label_32"])
        self.fake.add("g-2", "[Gail] not a pair", 900, labels=["DRAFT", "Label_32"])
        self.fake.add("g-3", "[Foo>Bar] not fleet names", 900, labels=["DRAFT", "Label_32"])
        res, _ = self.sweep_with()
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(res["other"], 3)

    # --- paging
    def test_every_page_of_drafts_is_swept(self):
        self.fake.page_size = 7
        for i in range(30):
            self.fake.add(f"t-{i}", f"[NCC-74656>MUSE] n{i}", 30, labels=["DRAFT", "Label_32"])
        res, _ = self.sweep_with()
        self.assertEqual(res["drafts"], 30)
        self.assertEqual(len(self.fake.deleted), 30)

    def test_the_tower_sweeping_its_own_side_deletes_hatchs_note_to_it_once_read(self):
        self.mine = {"NCC-74656"}
        self.fake.add("t-1", "[MUSE>NCC-74656] hi", 5)
        self.mark("t-1")
        self.sweep_with()
        self.assertEqual(self.fake.deleted, ["t-1"])

    def test_a_reply_the_tower_wrote_is_its_own_outbound_note(self):
        self.mine = {"NCC-74656"}
        self.assertEqual(nc.classify("Re: [NX-01>NCC-74656] ok", self.mine), ("outbound", "Re: [NX-01>NCC-74656]"))

    # --- the digest's status file
    def test_status_file_lists_unproven_notes_by_pair_without_bodies(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] one", 100, body="SECRET BODY")
        self.fake.add("t-2", "[NCC-74656>MUSE] two", 100, labels=["DRAFT", "Label_32"])
        self.fake.add("t-3", "[NX-01>NCC-74656] three", 80)
        status = self.dir / "status.json"
        self.sweep_with(status=str(status))
        doc = json.loads(status.read_text())
        self.assertEqual(sorted((u["id"], u["pair"]) for u in doc["unproven"]),
                         [("t-1", "[NCC-74656>MUSE]"), ("t-3", "[NX-01>NCC-74656]")])
        self.assertNotIn("SECRET", status.read_text())
        self.assertEqual(doc["fleet"], 3)

    def test_a_dry_run_writes_no_status_file(self):
        self.fake.add("t-1", "[NCC-74656>MUSE] one", 100)
        status = self.dir / "status.json"
        self.sweep_with(apply=False, status=str(status))
        self.assertFalse(status.exists())

    def test_the_reader_record_flag_adds_a_path(self):
        rec = self.dir / "r.txt"
        rec.write_text("t-9\n", encoding="utf-8")
        self.fake.add("t-9", "[MUSE>NCC-74656] x", 30)
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(nc.time, "time", return_value=NOW):
            nc.main(["--sweep", "--processed", str(self.processed), "--log", str(self.dir / "l.txt"),
                     "--reader-record", f"NCC-74656={rec}"])  # dry run: the lock file of --apply would touch the live state
        self.assertIn("would delete: 1", out.getvalue())
        self.assertEqual(self.fake.deleted, [])

class Modes(SweepBase):
    def test_dry_run_deletes_nothing_and_reports_counts_and_oldest(self):
        self.fake.add("r-1", "[MUSE>CC] a", 5)
        self.fake.add("r-2", "[MUSE>CC] b", 50)
        self.fake.add("r-3", "[CC>MUSE] c", 80, labels=["DRAFT", "Label_32"])
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
