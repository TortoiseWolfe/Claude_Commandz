"""The notes ledger (notes_ledger.py) and the stale-aware `open` read in muse_poll_record.sh.

Every board, lane folder and report here is invented and lives in a throwaway folder; nothing touches the live ~/.claude/state.
The state of an item comes from facts a script can check (a lane folder, its report line 1, a pull request, a done note), so
these tests build those facts and check the word that comes out, and check that words in a note change nothing."""

import hashlib
import os
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
from tmpdirs import fresh_dir  # noqa: E402
import notes_ledger as nl  # noqa: E402

REC = str(SCRIPTS / "muse_poll_record.sh")
LEDGER = str(SCRIPTS / "notes_ledger.py")
NOW = "2026-11-07 09:00"

BOARD = """- [ ] 2026-11-07 08:31 | Job: ear-check the Estimify take | due: 2026-11-07 1:00 PM | e-1111111
- [ ] 2026-11-07 08:00 | Job: write the weekly digest, lane digestwork | detail | e-2222222
- [ ] 2026-11-07 07:00 | Job: fix the thing, the lane needed is not made yet | detail | e-3333333
- [ ] 2026-11-06 12:00 | Job: being built right now, honest | detail | e-4444444
- [ ] 2026-11-05 10:00 | Job: wait on the stuck one, lane stuckwork | detail | e-5555555
- [ ] 2026-11-04 10:00 | Job: finished one, lane donework | detail | e-6666666
- [ ] 2026-11-03 10:00 | Job: closed by a note | detail | e-7777777
  - 2026-11-06 done: yes, shipped
- [ ] 2026-10-10 09:00 | Question: an old open one | his call | e-8888888
- [ ] 2026-10-10 09:00 | Hub: old but a new note touched it | detail | e-9999999
  - 2026-11-06 10:00 hub: still wanted
- [ ] 2026-11-20 09:00 | Job: far off, due: 2026-11-09 | detail | e-1010101
- [x] 2026-11-02 09:00 | Job: already ticked | done | e-1212121
"""


def write_lane(lanes, name, line1=None):
    d = Path(lanes) / name
    d.mkdir(parents=True)
    if line1 is not None:
        (d / "report.md").write_text(line1 + "\n\nbody\n")
    return d


def make_item(text, notes=()):
    return nl.Item(["- [ ] " + text + "\n"] + [n + "\n" for n in notes])


def never(*_a):
    raise AssertionError("the resolver must not be asked here")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(fresh_dir(self))
        self.board = self.d / "hub-inbox.md"
        self.board.write_text(BOARD)
        self.lanes = self.d / "lanes"
        self.lanes.mkdir()

    def cli(self, *args, **env):
        e = dict(os.environ, HOME=str(self.d))
        e.pop("HUB_INBOX", None)
        e.pop("MUSE_INBOX", None)
        e.update(env)
        return subprocess.run(["python3", "-I", LEDGER, *args, "--inbox", str(self.board), "--lanes-dir", str(self.lanes),
                               "--now", NOW, "--no-net"], capture_output=True, text=True, env=e, timeout=60)

    def states(self):
        now = datetime.strptime(NOW, "%Y-%m-%d %H:%M")
        folders = nl.lane_folders(self.lanes)
        out = {}
        for it in nl.read_items(self.board):
            out[it.head.split("|")[-1].strip()] = nl.evaluate(it, now, folders, never, net=False)["state"]
        return out


class States(Base):
    def test_no_lane_stays_queued_whatever_the_line_says(self):
        s = self.states()
        self.assertEqual(s["e-3333333"], "QUEUED")  # says "lane needed", no such folder
        self.assertEqual(s["e-4444444"], "QUEUED")  # says "being built", no lane folder
        self.assertEqual(s["e-2222222"], "QUEUED")  # names lane digestwork, the folder is not there yet

    def test_a_lane_folder_makes_it_started(self):
        write_lane(self.lanes, "2026-11-07-digestwork", "WORKING")
        self.assertEqual(self.states()["e-2222222"], "STARTED")

    def test_a_lane_folder_with_no_report_is_started(self):
        write_lane(self.lanes, "2026-11-07-digestwork")
        self.assertEqual(self.states()["e-2222222"], "STARTED")

    def test_report_line_one_sets_done_and_blocked(self):
        write_lane(self.lanes, "2026-11-06-donework", "DONE PR 12 merged")
        write_lane(self.lanes, "2026-11-06-stuckwork", "BLOCKED the hub must ok it")
        s = self.states()
        self.assertEqual(s["e-6666666"], "DONE")
        self.assertEqual(s["e-5555555"], "BLOCKED")

    def test_step_for_jonathan_and_queue_empty(self):
        write_lane(self.lanes, "2026-11-06-stuckwork", "STEP-FOR-JONATHAN https://example.com/x")
        write_lane(self.lanes, "2026-11-06-donework", "QUEUE EMPTY")
        s = self.states()
        self.assertEqual(s["e-5555555"], "BLOCKED")
        self.assertEqual(s["e-6666666"], "DONE")

    def test_newest_folder_wins_when_a_name_was_reused(self):
        write_lane(self.lanes, "2026-11-01-donework", "DONE")
        write_lane(self.lanes, "2026-11-06-donework", "WORKING")
        self.assertEqual(self.states()["e-6666666"], "STARTED")

    def test_dated_folder_name_in_the_line_finds_it(self):
        write_lane(self.lanes, "2026-11-06-otherwork", "WORKING")
        it = make_item("Job: x 2026-11-06-otherwork was started")
        now = datetime(2026, 11, 7, 9, 0)
        self.assertEqual(nl.evaluate(it, now, nl.lane_folders(self.lanes), never, net=False)["state"], "STARTED")

    def test_a_done_note_makes_it_done(self):
        self.assertEqual(self.states()["e-7777777"], "DONE")

    def test_done_note_needs_the_word_done_after_the_dash(self):
        it = make_item("Job: x", ["  - 2026-11-06 undone, still going"])
        self.assertFalse(it.done_note())

    def test_pull_request_merged_is_done_open_is_started_unknown_stays_queued(self):
        now = datetime(2026, 11, 7, 9, 0)
        it = make_item("Job: ship TortoiseWolfe/example#42 today")
        self.assertEqual(nl.evaluate(it, now, [], lambda k, r, n: "merged")["state"], "DONE")
        self.assertEqual(nl.evaluate(it, now, [], lambda k, r, n: "open")["state"], "STARTED")
        self.assertEqual(nl.evaluate(it, now, [], lambda k, r, n: None)["state"], "QUEUED")
        self.assertEqual(nl.evaluate(it, now, [], lambda k, r, n: "closed")["state"], "QUEUED")

    def test_pull_request_url_and_commit_are_read(self):
        now = datetime(2026, 11, 7, 9, 0)
        seen = []

        def res(kind, repo, ref):
            seen.append((kind, repo, ref))
            return "exists" if kind == "commit" else "open"

        nl.evaluate(make_item("Job: see https://github.com/acme/widgets/pull/7"), now, [], res)
        e = nl.evaluate(make_item("Job: landed acme/widgets@abc1234def"), now, [], res)
        self.assertIn(("pr", "acme/widgets", "7"), seen)
        self.assertIn(("commit", "acme/widgets", "abc1234def"), seen)
        self.assertEqual(e["state"], "STARTED")

    def test_no_net_never_asks_github(self):
        now = datetime(2026, 11, 7, 9, 0)
        e = nl.evaluate(make_item("Job: ship acme/widgets#9"), now, [], never, net=False)
        self.assertEqual(e["state"], "QUEUED")

    def test_old_evidence_is_stamped(self):
        lane = write_lane(self.lanes, "2026-11-01-digestwork", "WORKING")
        old = datetime(2026, 11, 3, 9, 0).timestamp()
        os.utime(lane / "report.md", (old, old))
        it = make_item("Job: x, lane digestwork")
        e = nl.evaluate(it, datetime(2026, 11, 7, 9, 0), nl.lane_folders(self.lanes), never, net=False)
        self.assertEqual(e["state"], "STARTED")
        self.assertIn("evidence 96 h old", e["evidence"])


class Deadlines(Base):
    def due(self, text, created="2026-11-07 08:31"):
        return nl.parse_due(nl.Item(["- [ ] %s | Job: %s\n" % (created, text)]))

    def test_forms(self):
        self.assertEqual(self.due("go due: 2026-11-07 1:00 PM"), datetime(2026, 11, 7, 13, 0))
        self.assertEqual(self.due("go due: 2026-11-07 13:00"), datetime(2026, 11, 7, 13, 0))
        self.assertEqual(self.due("go due: 1 PM"), datetime(2026, 11, 7, 13, 0))
        self.assertEqual(self.due("go due: 12 AM"), datetime(2026, 11, 7, 0, 0))
        self.assertEqual(self.due("go due: 12 PM"), datetime(2026, 11, 7, 12, 0))
        self.assertEqual(self.due("go due: 2026-11-08"), datetime(2026, 11, 8, 17, 0))

    def test_not_a_deadline(self):
        self.assertIsNone(self.due("nothing to see"))
        self.assertIsNone(self.due("go due 3 things"))
        self.assertIsNone(self.due("go due: soon"))
        self.assertEqual(self.due("go due: 2026-11-08 12 items"), datetime(2026, 11, 8, 17, 0))

    def test_estimify_shaped_item_is_at_risk_at_nine(self):
        r = self.cli("risk")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, r.stdout)
        self.assertTrue(lines[0].startswith("AT-RISK "))
        self.assertIn("ear-check", lines[0])
        self.assertIn("1:00 PM", lines[0])
        self.assertIn("4 h left", lines[0])

    def test_the_same_item_with_a_lane_is_started_and_not_at_risk(self):
        self.board.write_text("- [ ] 2026-11-07 08:31 | Job: ear-check the take, lane earcheck | due: 2026-11-07 1:00 PM | e-1\n")
        write_lane(self.lanes, "2026-11-07-earcheck", "WORKING")
        self.assertEqual(self.cli("risk").stdout, "")
        self.assertIn("STARTED", self.cli("ledger").stdout)

    def test_not_flagged_before_the_margin(self):
        self.board.write_text("- [ ] 2026-11-07 08:31 | Job: ear-check | due: 2026-11-07 1:00 PM | e-1\n")
        r = subprocess.run(["python3", "-I", LEDGER, "risk", "--inbox", str(self.board), "--lanes-dir", str(self.lanes),
                            "--now", "2026-11-07 08:45", "--no-net"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout, "")  # 4 h 15 min left, margin is 4 h

    def test_a_deadline_set_days_ahead_uses_the_long_margin(self):
        # queued 2 Nov, due 12 Nov 5 PM: flags only within 24 h of the deadline
        self.board.write_text("- [ ] 2026-11-02 09:00 | Job: report | due: 2026-11-12 | e-1\n")
        run = lambda now: subprocess.run(["python3", "-I", LEDGER, "risk", "--inbox", str(self.board), "--lanes-dir", str(self.lanes),
                                          "--now", now, "--no-net", "--stale-days", "30"], capture_output=True, text=True, timeout=60).stdout
        self.assertEqual(run("2026-11-11 16:00"), "")
        self.assertIn("AT-RISK", run("2026-11-11 17:30"))

    def test_overdue_and_queued_is_at_risk(self):
        self.board.write_text("- [ ] 2026-11-07 06:00 | Job: late | due: 2026-11-07 8:00 AM | e-1\n")
        out = self.cli("risk").stdout
        self.assertIn("overdue by", out)


class Stale(Base):
    def test_stale_rule_uses_head_date_and_newer_note_date(self):
        r = self.cli("stale")
        self.assertIn("an old open one", r.stdout)
        self.assertNotIn("old but a new note touched it", r.stdout)  # a 2026-11-06 note keeps it fresh
        self.assertNotIn("ear-check", r.stdout)

    def test_ledger_and_order_leave_stale_out_unless_asked(self):
        self.assertNotIn("an old open one", self.cli("ledger").stdout)
        self.assertIn("an old open one", self.cli("ledger", "--include-stale").stdout)
        self.assertNotIn("an old open one", self.cli("order").stdout)

    def test_counts_prints_numbers_never_line_text(self):
        r = self.cli("counts")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("open items: 10", r.stdout)
        self.assertIn("stale (older than 14 days, no change since): 1", r.stdout)
        self.assertIn("by state:", r.stdout)
        for word in ("ear-check", "Estimify", "digest", "ticked"):
            self.assertNotIn(word, r.stdout)

    def test_a_line_without_a_readable_date_is_never_stale(self):
        it = nl.Item(["- [ ] not a date | Job: odd\n"])
        self.assertFalse(it.ok)  # not a board line at all, so it is not read


class Order(Base):
    def test_one_block_per_open_item_with_state_decision_deadline(self):
        self.board.write_text("- [ ] 2026-11-07 08:31 | Job: ear-check | due: 2026-11-07 1:00 PM | e-1\n  - 2026-11-07 08:40 decision: use the second take\n"
                              "- [ ] 2026-11-07 08:00 | Job: other | detail | e-2\n")
        out = self.cli("order").stdout
        blocks = [b for b in out.strip().split("\n\n") if b]
        self.assertEqual(len(blocks), 2)
        self.assertIn("STATE: AT-RISK", blocks[0])
        self.assertIn("decision already made: use the second take", blocks[0])
        self.assertIn("deadline: Sat 7 Nov 1:00 PM (4 h left)", blocks[0])
        self.assertIn("STATE: QUEUED", blocks[1])
        self.assertIn("decision already made: none recorded on the line", blocks[1])
        self.assertIn("deadline: none on the line", blocks[1])

    def test_filters(self):
        write_lane(self.lanes, "2026-11-06-donework", "DONE")
        self.assertEqual(len(self.cli("order", "--state", "DONE").stdout.strip().split("\n\n")), 2)  # lane done + done note
        self.assertIn("closed by a note", self.cli("order", "--match", "closed by").stdout)
        self.assertIn("no open items match", self.cli("order", "--owner", "Question").stdout)
        self.assertEqual(self.cli("order", "--limit", "1").stdout.count("STATE:"), 1)

    def test_done_item_says_close_it(self):
        self.assertIn("close it", self.cli("order", "--state", "DONE").stdout)

    def test_ledger_write_makes_a_snapshot_and_leaves_the_board_alone(self):
        snap = self.d / "ledger.txt"
        before = hashlib.sha256(self.board.read_bytes()).hexdigest()
        r = self.cli("ledger", "--write", str(snap))
        self.assertIn("wrote", r.stdout)
        self.assertTrue(snap.read_text().startswith("# Notes ledger"))
        self.assertEqual(hashlib.sha256(self.board.read_bytes()).hexdigest(), before)


class ReadOnly(Base):
    def test_no_command_changes_the_board(self):
        before = hashlib.sha256(self.board.read_bytes()).hexdigest()
        for cmd in (["counts"], ["stale"], ["ledger"], ["order"], ["risk"], ["order", "--include-stale"]):
            r = self.cli(*cmd)
            self.assertEqual(r.returncode, 0, "%s: %s" % (cmd, r.stderr))
        self.assertEqual(hashlib.sha256(self.board.read_bytes()).hexdigest(), before)

    def test_missing_board_is_not_a_crash(self):
        self.board.unlink()
        self.assertEqual(self.cli("counts").returncode, 0)
        self.assertEqual(self.cli("risk").stdout, "")


class OpenRead(Base):
    """muse_poll_record.sh open: the default read leaves stale items out, says how many, and --stale / --all show them."""

    def rec(self, *args):
        e = dict(os.environ, MUSE_STATE=str(self.d), HUB_TODAY="2026-11-07", HOME=str(self.d))
        e.pop("HUB_INBOX", None)
        e.pop("MUSE_INBOX", None)
        return subprocess.run(["bash", REC, *args], capture_output=True, text=True, env=e, timeout=60)

    def test_default_hides_stale_and_says_so(self):
        out = self.rec("open").stdout
        self.assertIn("ear-check", out)
        self.assertNotIn("an old open one", out)
        self.assertIn("old but a new note touched it", out)
        self.assertIn("1 stale open item(s) not shown", out)
        self.assertIn("open --stale", out)

    def test_stale_lists_only_stale(self):
        out = self.rec("open", "--stale").stdout
        self.assertIn("an old open one", out)
        self.assertNotIn("ear-check", out)

    def test_all_lists_both(self):
        out = self.rec("open", "--all").stdout
        self.assertIn("an old open one", out)
        self.assertIn("ear-check", out)
        self.assertNotIn("stale open item(s) not shown", out)

    def test_ticked_lines_are_never_in_open(self):
        self.assertNotIn("already ticked", self.rec("open", "--all").stdout)

    def test_stale_days_can_be_changed(self):
        e = dict(os.environ, MUSE_STATE=str(self.d), HUB_TODAY="2026-11-07", HUB_STALE_DAYS="1", HOME=str(self.d))
        e.pop("HUB_INBOX", None)
        e.pop("MUSE_INBOX", None)
        out = subprocess.run(["bash", REC, "open"], capture_output=True, text=True, env=e, timeout=60).stdout
        # a 1-day limit: 5 Nov and 4 Nov lines go; 6 Nov stays; the 3 Nov line stays because its note is dated 6 Nov
        self.assertNotIn("wait on the stuck one", out)
        self.assertNotIn("finished one", out)
        self.assertIn("being built right now", out)
        self.assertIn("closed by a note", out)

    def test_the_ledger_commands_reach_the_script(self):
        for cmd in ("counts", "stale", "ledger", "order", "risk"):
            r = self.rec(cmd, "--no-net", "--lanes-dir", str(self.d / "lanes"))
            self.assertEqual(r.returncode, 0, "%s: %s" % (cmd, r.stderr))


if __name__ == "__main__":
    unittest.main()
