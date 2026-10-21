"""The hub's inbox (hub-inbox.md): owner prefixes, the read-only `open` view, writes through the old symlink name,
the archive of old done lines, the session-start hook's per-owner counts, and the stale-wait flag in the digest.
Every fixture is invented and lives in a throwaway state folder; nothing here touches the live ~/.claude/state."""

import os
import subprocess
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
HOOKS = SCRIPTS.parent / "hooks"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
from tmpdirs import fresh_dir  # noqa: E402
import hub_inbox_archive as arch  # noqa: E402
import hub_digest as hd  # noqa: E402

REC = str(SCRIPTS / "muse_poll_record.sh")

BOARD = """- [x] 2026-09-20 10:00 | Job: Old finished thing | detail | aa11aa11
  - done 2026-09-21: handled
  - 2026-09-20 11:00: checked, waiting
- [x] 2026-10-18 09:00 | Waiting on Hatch: Recent done | done-note | bb22bb22
  - done 2026-10-19: Hatch posted it
- [ ] 2026-10-08 09:53 | Waiting on Hatch: Four notes | wants a receipt | r-1234567890
  - 2026-10-09 08:00: nudged
- [ ] 2026-10-12 08:21 | Waiting on tower: rebuild as a lane | answers a-d | r-22
- [ ] 2026-10-14 12:31 | Job: Taphouse model | run it as lanes | 1a2b3c4d5e6f7a8b
- [ ] 2026-10-15 08:00 | Client mail: Example Brewing | absorb lane | 1a2b3c4d5e6f7a8d
- [ ] 2026-10-15 09:00 | Question: pick the venture | his call | r-44
- [ ] 2026-10-16 09:00 | Hub: check the disk | self | r-45
"""


def run(args, state, **env):
    e = dict(os.environ, MUSE_STATE=str(state), **env)
    e.pop("HUB_INBOX", None)
    e.pop("MUSE_INBOX", None)
    e.update({k: v for k, v in env.items()})
    return subprocess.run(["bash", REC, *args], capture_output=True, text=True, env=e, timeout=60)


class Board(unittest.TestCase):
    def setUp(self):
        self.st = Path(fresh_dir(self))
        self.board = self.st / "hub-inbox.md"
        self.board.write_text(BOARD)
        self.link = self.st / "muse-inbox.md"
        self.link.symlink_to("hub-inbox.md")

    def test_open_prints_only_open_lines(self):
        out = run(["open"], self.st).stdout.splitlines()
        self.assertEqual(len(out), 6)
        self.assertTrue(all(ln.startswith("- [ ] ") for ln in out))

    def test_open_by_prefix_is_exact_and_case_blind(self):
        out = run(["open", "waiting on hatch"], self.st).stdout.splitlines()
        self.assertEqual(len(out), 1)
        self.assertIn("Four notes", out[0])
        self.assertEqual(run(["open", "Waiting on Jonathan"], self.st).stdout, "")

    def test_open_verbose_adds_notes_of_open_lines_only(self):
        out = run(["open", "Waiting on Hatch", "-v"], self.st).stdout.splitlines()
        self.assertEqual(len(out), 2)
        self.assertIn("nudged", out[1])
        self.assertNotIn("Hatch posted it", "\n".join(out))

    def test_unknown_prefix_is_refused_and_writes_nothing(self):
        before = self.board.read_text()
        r = run(["inbox", "Tower note: something"], self.st)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown prefix", r.stderr)
        self.assertEqual(self.board.read_text(), before)

    def test_every_canonical_prefix_is_accepted(self):
        for p in ("Waiting on Hatch", "Waiting on tower", "Job", "Client mail", "Client alert", "Question", "Hub"):
            self.assertEqual(run(["inbox", p + ": x | y | z"], self.st).returncode, 0, p)
        self.assertEqual(self.board.read_text().count("\n"), BOARD.count("\n") + 7)

    def test_done_through_the_old_name_keeps_the_symlink(self):
        r = run(["done", "r-1234567890"], self.st, HUB_INBOX=str(self.link))
        self.assertEqual(r.stdout.strip(), "ticked 1")
        self.assertTrue(self.link.is_symlink())
        self.assertIn("- [x] 2026-10-08 09:53 | Waiting on Hatch: Four notes", self.board.read_text())

    def test_muse_inbox_env_still_works(self):
        r = run(["inbox", "Hub: via the old env var"], self.st, MUSE_INBOX=str(self.link))
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.link.is_symlink())
        self.assertIn("via the old env var", self.board.read_text())

    def test_client_seen_ticks_through_the_symlink(self):
        r = run(["client-seen", "1a2b3c4d5e6f7a8d"], self.st, HUB_INBOX=str(self.link))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.link.is_symlink())
        self.assertIn("- [x] 2026-10-15 08:00 | Client mail: Example Brewing", self.board.read_text())

    def test_client_mail_dedupes_against_the_board(self):
        r = run(["client-mail", "Client mail", "Example Brewing", "1a2b3c4d5e6f7a8d"], self.st)
        self.assertEqual(r.stdout.strip(), "queued 0")


class Archive(unittest.TestCase):
    TODAY = date(2026, 10, 21)

    def setUp(self):
        self.st = Path(fresh_dir(self))
        self.board = self.st / "hub-inbox.md"
        self.board.write_text(BOARD)
        self.adir = self.st / "hub-inbox-archive"

    def go(self, **kw):
        return arch.run(self.board, self.adir, self.TODAY, 7, **kw)

    def test_moves_only_old_done_blocks_with_their_children(self):
        n, opened = self.go()
        self.assertEqual((n, opened), (1, 6))
        text = self.board.read_text()
        self.assertNotIn("Old finished thing", text)
        self.assertNotIn("handled", text)
        self.assertIn("Recent done", text)
        self.assertIn("Hatch posted it", text)
        arc = (self.adir / "2026-09.md").read_text()
        self.assertIn("Old finished thing", arc)
        self.assertIn("  - done 2026-09-21: handled", arc)
        self.assertIn("checked, waiting", arc)

    def test_open_lines_untouched_and_in_order(self):
        before = [ln for ln in BOARD.splitlines() if ln.startswith("- [ ] ")]
        self.go()
        after = [ln for ln in self.board.read_text().splitlines() if ln.startswith("- [ ] ")]
        self.assertEqual(before, after)

    def test_old_open_line_never_moves(self):
        self.board.write_text("- [ ] 2026-01-01 00:00 | Waiting on Hatch: ancient | x | id1\n  - 2026-01-02: note\n")
        self.assertEqual(self.go()[0], 0)
        self.assertIn("ancient", self.board.read_text())

    def test_recent_note_on_a_done_line_keeps_it(self):
        self.board.write_text("- [x] 2026-09-01 00:00 | Job: long one | x | id2\n  - 2026-10-20 08:00: still being touched\n")
        self.assertEqual(self.go()[0], 0)

    def test_second_run_is_a_no_op_and_dry_run_writes_nothing(self):
        self.assertEqual(self.go(dry_run=True)[0], 1)
        self.assertFalse(self.adir.exists())
        self.go()
        arc = (self.adir / "2026-09.md").read_text()
        self.assertEqual(self.go()[0], 0)
        self.assertEqual((self.adir / "2026-09.md").read_text(), arc)

    def test_a_block_already_archived_is_not_written_twice(self):
        self.adir.mkdir()
        (self.adir / "2026-09.md").write_text("- [x] 2026-09-20 10:00 | Job: Old finished thing | detail | aa11aa11\n")
        self.go()
        self.assertEqual((self.adir / "2026-09.md").read_text().count("Old finished thing"), 1)

    def test_symlink_survives_the_rewrite(self):
        link = self.st / "muse-inbox.md"
        link.symlink_to("hub-inbox.md")
        arch.run(link, self.adir, self.TODAY, 7)
        self.assertTrue(link.is_symlink())
        self.assertNotIn("Old finished thing", self.board.read_text())

    def test_record_sh_archive_wraps_it(self):
        r = subprocess.run(["bash", REC, "archive", "7"], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, MUSE_STATE=str(self.st), HUB_INBOX=str(self.board)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("open line(s) untouched", r.stdout)

    def test_session_prime_still_lists_the_weeks_done_items_from_the_archive(self):
        import session_prime_facts as spf
        # Recent done item stays on the board; a block archived last week is read back from the archive file.
        self.adir.mkdir()
        (self.adir / "2026-10.md").write_text("- [x] 2026-10-10 09:00 | Job: archived week item | d | cc33cc33\n  - done 2026-10-17 10:00: ok\n")
        args = type("A", (), {"inbox": str(self.board), "inbox_archive": str(self.adir)})()
        since = datetime(2026, 10, 14, 0, 0).astimezone()
        text = spf.inbox_text(args, since)
        titles = [t for t, _ in spf.ticked_since(text, since)]
        self.assertIn("Job: archived week item", titles)
        self.assertIn("Waiting on Hatch: Recent done", titles)


class Hook(unittest.TestCase):
    def test_counts_per_owner(self):
        home = Path(fresh_dir(self))
        (home / "repos").mkdir()
        st = home / ".claude/state"
        st.mkdir(parents=True)
        (st / "agent-notes-processed.txt").write_text("x\n")
        (st / "hub-inbox.md").write_text(BOARD + "- [ ] 2026-10-17 09:00 | Something unsorted | n | r-9\n")
        env = dict(os.environ, HOME=str(home), CLAUDE_PROJECT_DIR=str(home / "repos"))
        env.pop("HUB_INBOX", None)
        env.pop("MUSE_INBOX", None)
        env.pop("MUSE_POLL", None)
        r = subprocess.run(["bash", str(HOOKS / "muse-drafts.sh")], input="{}", capture_output=True, text=True, env=env, timeout=30)
        out = r.stdout
        self.assertIn("7 open on the hub's list: 1 waiting on Hatch, 1 on the tower, 1 jobs, 1 client lines, 3 others", out)
        self.assertNotIn("from Hatch", out.split("Also:")[1])
        self.assertIn('muse_poll_record.sh open "<prefix>"', out)


class Digest(unittest.TestCase):
    NOW = datetime(2026, 10, 21, 12, 0)

    def test_wait_age_days(self):
        self.assertEqual(hd.wait_age_days("10-08", self.NOW), 13)
        self.assertEqual(hd.wait_age_days("garbage", self.NOW), 0)

    def test_stale_wait_is_flagged_and_fresh_one_is_not(self):
        text = ("- [ ] 2026-10-08 09:53 | Waiting on Hatch: old ask | x | r-1\n"
                "- [ ] 2026-10-19 09:53 | Waiting on Hatch: fresh ask | x | r-2\n"
                "- [ ] 2026-10-08 09:53 | Job: old job | x | r-3\n")
        groups, _ = hd.open_inbox(text)
        old = [t for d, t in groups["Hatch"] if "old" in t]
        self.assertEqual(len(old), 1)
        self.assertEqual(len(groups["Hatch"]), 2)
        self.assertEqual(len(groups["jobs"]), 1)

    def test_rendered_page_says_chase_or_close_only_on_stale_waits(self):
        text = ("- [ ] 2026-10-08 09:53 | Waiting on Hatch: old ask | x | r-1\n"
                "- [ ] 2026-10-19 09:53 | Waiting on Hatch: fresh ask | x | r-2\n"
                "- [ ] 2026-10-08 09:53 | Job: old job | x | r-3\n")
        page = hd.render(hd.build_sections(roadmap_body="", lane_status="", lanes_dir=fresh_dir(self), inbox_text=text,
                                           git_log="", tidy_text="", budget="", now=self.NOW), self.NOW, "NX-01", hd.CAP_DEFAULT)
        self.assertIn("Open hub-inbox items", page)
        flagged = [ln for ln in page.splitlines() if "chase or close" in ln]
        self.assertEqual(len(flagged), 1)
        self.assertIn("old ask", flagged[0])
        self.assertIn("13 days", flagged[0])

    def test_a_line_with_many_fields_still_lands_in_its_owner_group(self):
        text = "- [ ] 2026-10-18 09:00 | Job: long one | a | b | c | d | e | f | r-9\n- [ ] 2026-10-18 09:00 | odd start | a | b | c | d | e | r-8\n"
        groups, _ = hd.open_inbox(text)
        self.assertEqual([t for _d, t in groups["jobs"]], ["Job: long one"])
        self.assertEqual(len(groups["other"]), 1)

    def test_question_and_hub_prefixes_have_groups(self):
        groups, _ = hd.open_inbox("- [ ] 2026-10-15 09:00 | Question: pick | x | r-1\n- [ ] 2026-10-16 09:00 | Hub: check | x | r-2\n")
        self.assertEqual(len(groups["Jonathan"]), 1)
        self.assertEqual(len(groups["hub"]), 1)
        self.assertEqual(groups["other"], [])


if __name__ == "__main__":
    unittest.main()
