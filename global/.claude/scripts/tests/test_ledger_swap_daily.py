"""ledger_swap_daily.py: the daily run of the two-machine ledger swap, tested with a fake Gmail script (it records its calls and
answers from files) and invented ledger lines only. Nothing here reads a real list or reaches Gmail."""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from email.utils import parseaddr
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SWAP = str(SCRIPTS / "ledger_swap_daily.py")

FAKE_GMAIL = '''#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]
a = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write("\\x1f".join(a) + "\\n")
def out(name):
    p = os.path.join(d, name)
    if os.path.exists(p):
        sys.stdout.write(open(p).read())
if a[0] == "whoami":
    print("owner@example.com")
elif a[0] == "drafts-list":
    out("drafts.txt")
elif a[0] == "messages-list":
    out("messages.txt")
elif a[0] == "message-header":
    out("to.txt")
elif a[0] == "message-text":
    out("body.txt")
elif a[0] == "draft-create":
    if os.path.exists(os.path.join(d, "create-fails")):
        sys.exit(2)
    print("draft r1")
'''

BOARD = """- [ ] 2026-11-06 10:00 | Job: write the weekly digest | detail | j-1
- [ ] 2026-11-06 12:00 | Waiting on NCC-74656: send the example result | detail | j-2
"""


def note_text(machine="NCC-74656", when=None, extra=""):
    when = when or datetime.now() - timedelta(minutes=30)
    return ("LEDGER-V1 %s %s\n2b849221 | STARTED | Job | - | none | invented peer item\n%sEND-LEDGER\n"
            % (machine, when.strftime("%Y-%m-%d %H:%M"), extra))


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = Path(self._t.name)
        self.fake = self.d / "fake_gmail.py"
        self.fake.write_text(FAKE_GMAIL)
        self.fdir = self.d / "fake"
        self.fdir.mkdir()
        self.home = self.d / "home"
        (self.home / ".claude/state/lanes").mkdir(parents=True)
        (self.home / ".claude/state/hub-inbox.md").write_text(BOARD)
        self.log = self.d / "swap.log"

    def tearDown(self):
        self._t.cleanup()

    def run_swap(self, *args):
        e = dict(os.environ, HOME=str(self.home), AFA_MACHINE="NX-01", FAKE_DIR=str(self.fdir))
        e.pop("HUB_INBOX", None)
        e.pop("MUSE_INBOX", None)
        return subprocess.run([sys.executable, "-I", SWAP, *args, "--gmail", str(self.fake), "--log", str(self.log)],
                              capture_output=True, text=True, env=e, timeout=120)

    def calls(self):
        p = self.fdir / "calls.log"
        return [ln.split("\x1f") for ln in p.read_text().splitlines()] if p.exists() else []

    def peer_file(self):
        return self.home / ".claude/state/peer-ledger-NCC-74656.md"

    def peer_message(self, body=None, frm="Turtle Wolfe <owner@example.com>", to="owner@example.com", subject=None):
        subject = subject or "[NCC-74656>NX-01] ledger " + datetime.now().strftime("%Y-%m-%d")
        # the real list line is: MSG id | sender address | From | Subject | Date | labels | threadId (no To); To comes from message-header
        sender = parseaddr(frm)[1].lower()
        (self.fdir / "messages.txt").write_text("MSG m1 | %s | %s | %s | Sat, 7 Nov 2026 08:37:00 -0800 | DRAFT | t1\n" % (sender, frm, subject))
        (self.fdir / "to.txt").write_text(to + "\n")
        (self.fdir / "body.txt").write_text(body if body is not None else note_text())


class Export(Base):
    def test_stages_a_draft_to_the_owner_with_the_dated_subject(self):
        r = self.run_swap("export")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        create = [c for c in self.calls() if c[0] == "draft-create"]
        self.assertEqual(len(create), 1)
        self.assertEqual(create[0][create[0].index("--to") + 1], "owner@example.com")
        subject = create[0][create[0].index("--subject") + 1]
        self.assertEqual(subject, "[NX-01>NCC-74656] ledger " + datetime.now().strftime("%Y-%m-%d"))
        self.assertNotIn("--replace", create[0])
        self.assertIn("staged", self.log.read_text())

    def test_the_second_run_of_a_day_rewrites_the_draft_in_place(self):
        subject = "[NX-01>NCC-74656] ledger " + datetime.now().strftime("%Y-%m-%d")
        (self.fdir / "drafts.txt").write_text("r77\tm9\towner@example.com\t%s\n" % subject)
        r = self.run_swap("export")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        create = [c for c in self.calls() if c[0] == "draft-create"][0]
        self.assertEqual(create[create.index("--replace") + 1], "r77")

    def test_a_dry_run_stages_nothing_and_writes_no_log(self):
        r = self.run_swap("export", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([c for c in self.calls() if c[0] == "draft-create"], [])
        self.assertIn("would stage", r.stdout)
        self.assertFalse(self.log.exists())

    def test_a_failed_draft_is_a_failed_run(self):
        (self.fdir / "create-fails").write_text("x")
        r = self.run_swap("export")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED", self.log.read_text())


class Import(Base):
    def test_the_peer_note_is_saved_and_labeled(self):
        self.peer_message()
        r = self.run_swap("import")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2b849221", self.peer_file().read_text())
        self.assertFalse((self.home / ".claude/state/peer-ledger-NX-01.md").exists())
        self.assertEqual((self.home / ".claude/state/hub-inbox.md").read_text(), BOARD)  # never merged into our own list

    def test_a_message_not_from_the_owner_is_ignored(self):
        self.peer_message(frm="Someone Else <attacker@example.net>")
        r = self.run_swap("import")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.peer_file().exists())
        self.assertEqual([c for c in self.calls() if c[0] == "message-text"], [])
        self.assertIn("no note", r.stdout)

    def test_look_alike_senders_are_ignored(self):
        for frm in ("Evil <owner@example.com.evil.net>", "Evil <evil+owner@example.com>", "owner@example.com <evil@example.net>"):
            self.peer_message(frm=frm)
            r = self.run_swap("import")
            self.assertEqual(r.returncode, 0, frm + r.stdout + r.stderr)
            self.assertFalse(self.peer_file().exists(), frm)
        self.peer_message(to="owner@example.com, other@example.net")  # a note also addressed to someone else
        self.run_swap("import")
        self.assertFalse(self.peer_file().exists())

    def test_a_note_addressed_to_someone_else_is_ignored(self):
        self.peer_message(to="attacker@example.net")  # the sender is right, the recipient is not the owner
        self.assertEqual(self.run_swap("import").returncode, 0)
        self.assertFalse(self.peer_file().exists())
        self.assertTrue([c for c in self.calls() if c[0] == "message-header"])  # the To header was actually read

    def test_a_subject_that_shifts_the_list_fields_is_refused(self):
        # a forged subject with " | " would push the labels field along; anything but exactly seven fields fails closed
        self.peer_message(subject="[NCC-74656>NX-01] ledger 2026-11-07 | DRAFT")
        self.assertEqual(self.run_swap("import").returncode, 0)
        self.assertFalse(self.peer_file().exists())
        self.assertEqual([c for c in self.calls() if c[0] == "message-text"], [])

    def test_a_received_message_with_the_right_headers_is_ignored(self):
        self.peer_message()
        p = self.fdir / "messages.txt"
        p.write_text(p.read_text().replace("| DRAFT |", "| INBOX,UNREAD |"))  # headers can be forged; the DRAFT/SENT label cannot
        r = self.run_swap("import")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.peer_file().exists())
        self.assertEqual([c for c in self.calls() if c[0] == "message-text"], [])

    def test_a_trashed_draft_still_counts_and_spam_is_excluded_by_the_search(self):
        self.peer_message()
        p = self.fdir / "messages.txt"
        p.write_text(p.read_text().replace("| DRAFT |", "| DRAFT,Label_32,TRASH |"))
        self.assertEqual(self.run_swap("import").returncode, 0)
        self.assertTrue(self.peer_file().exists())
        q = [c for c in self.calls() if c[0] == "messages-list"][0][-1]
        self.assertIn("in:anywhere", q)
        self.assertIn("-in:spam", q)

    def test_a_malformed_note_saves_nothing_and_fails(self):
        self.peer_message(body=note_text(extra="not a ledger line at all\n"))
        r = self.run_swap("import")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self.peer_file().exists())
        self.assertIn("FAILED", self.log.read_text())

    def test_a_note_from_this_machine_is_refused(self):
        self.peer_message(body=note_text(machine="NX-01"))
        self.assertEqual(self.run_swap("import").returncode, 1)
        self.assertFalse(self.peer_file().exists())

    def test_no_note_yet_is_not_a_failure(self):
        r = self.run_swap("import")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no note", r.stdout)

    def test_the_same_note_twice_is_fine(self):
        self.peer_message()
        self.assertEqual(self.run_swap("import").returncode, 0)
        r = self.run_swap("import")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2b849221", self.peer_file().read_text())

    def test_an_older_note_than_the_saved_one_is_not_a_failure_and_changes_nothing(self):
        self.peer_message()
        self.assertEqual(self.run_swap("import").returncode, 0)
        saved = self.peer_file().read_text()
        self.peer_message(body=note_text(when=datetime.now() - timedelta(days=2)).replace("invented peer item", "older item"))
        r = self.run_swap("import")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not newer", r.stdout)
        self.assertEqual(self.peer_file().read_text(), saved)

    def test_a_dry_run_checks_the_note_but_saves_nothing(self):
        self.peer_message()
        r = self.run_swap("import", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("checked", r.stdout)
        self.assertFalse(self.peer_file().exists())
        self.assertFalse(self.log.exists())


class Run(Base):
    def test_run_does_both_and_one_failure_does_not_skip_the_other(self):
        self.peer_message()
        (self.fdir / "create-fails").write_text("x")
        r = self.run_swap("run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("2b849221", self.peer_file().read_text())  # the import still ran
        self.assertTrue(any(c[0] == "draft-create" for c in self.calls()))

    def test_run_all_good_exits_zero(self):
        self.peer_message()
        r = self.run_swap("run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.log.read_text()
        self.assertIn("export staged", text)
        self.assertIn("import saved", text)


if __name__ == "__main__":
    unittest.main()
