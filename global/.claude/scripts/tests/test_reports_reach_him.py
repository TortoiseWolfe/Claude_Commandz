"""18 Oct: "it says I'll get reports back and then it just evolves into silence". The rules these tests hold:
  - every lane's report.md reaches his chat without the hub: one post per report version, the lane name as a heading, the report whole
    (numbered parts when long, never trimmed), a 12-hour stamp; the first run only records what exists, and the dispatcher's own lanes
    (whose reports its sweep already posts) are left alone;
  - a person the people index gives a role for is masked by that role ("[field partner]"), not as a bare "[person]";
  - the receptionist forwards "next step" questions as its whole reply;
  - a picture is described in words with the Windows path (the web chat draws only inline base64 images, and a post is cut into parts).
The PC and OpenClaw are fakes; the names in the people fixture are invented."""

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openclaw_dispatch as dp  # noqa: E402
import openclaw_status_digest as dg  # noqa: E402
from test_openclaw_dispatch import NOW, FakeHost  # noqa: E402
from test_openclaw_status_digest import PEOPLE, SENDERS, Base as DigestBase  # noqa: E402

HOOK = Path(__file__).resolve().parent.parent / "openclaw_ask_hook"


class ReportPosts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lanes = self.tmp / "lanes"
        self.ctx = dp.Ctx({}, self.tmp / "dispatch.json", self.lanes)
        self.host = FakeHost()

    def report(self, lane, text, age=300, date="2026-10-18"):
        p = self.lanes / ("%s-%s" % (date, lane)) / "report.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def run_posts(self, **kw):
        return dp.post_new_reports(self.ctx, self.host, time.time(), **kw)

    def seeded(self):
        """A state where every report now on disk counts as already posted: what the install does."""
        self.run_posts(seed=True)
        self.host.chats.clear()


class TheFirstRunOnlyRecords(ReportPosts):
    def test_reports_that_exist_when_the_state_is_new_are_not_posted(self):
        self.report("old-one", "Done: an old report.")
        self.report("old-two", "Done: another.")
        self.assertEqual(self.run_posts(), [])
        self.assertEqual(self.host.chats, [])
        self.report("new-one", "Done: written after the first run.")
        self.assertEqual(self.run_posts(), ["new-one"])

    def test_seeding_posts_nothing_and_records_every_report(self):
        self.report("a", "Done.")
        self.assertEqual(self.run_posts(seed=True), [])
        self.assertEqual(self.host.chats, [])
        self.assertEqual(self.run_posts(), [])


class TheStampLineNamesTheRepo(ReportPosts):
    """19 Oct: "I don't know what repo each is in reference to, you can put it at the top near the time stamps". The repo comes from
    the lane's folder in the lane index; a folder it cannot place leaves the stamp line as it was, never a guess."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "repos"
        self.root.mkdir()
        self.index = self.tmp / "index.tsv"
        self.ctx = dp.Ctx({}, self.tmp / "dispatch.json", self.lanes, index_path=self.index, root=self.root)

    def indexed(self, lane, dirpath, date="2026-10-18"):
        with open(self.index, "a") as fh:
            fh.write("\t".join([date, lane, str(dirpath), "sonnet", "subscription", "-", "-", "x", "?", "-"]) + "\n")

    def stamp_line(self, lane):
        self.seeded()
        self.report(lane, "Done: it works.")
        self.run_posts()
        return self.host.chats[0].split("\n")[1]

    def test_a_plain_repo_is_named_on_the_stamp_line(self):
        self.indexed("fix-nav", self.root / "ScriptHammer")
        self.assertRegex(self.stamp_line("fix-nav"), r"^\*ScriptHammer \u00b7 report written \d{1,2}:\d{2} (AM|PM)\*$")

    def test_a_folder_inside_a_repo_names_that_repo(self):
        self.indexed("deep", self.root / "TurtleWolfe" / "stream" / "can-we-build-it")
        self.assertTrue(self.stamp_line("deep").startswith("*TurtleWolfe \u00b7 report written "))

    def test_a_worktree_names_its_parent_repo(self):
        self.indexed("wt-lane", self.root / ".worktrees" / "SpokeToWork--fix-login")
        self.assertTrue(self.stamp_line("wt-lane").startswith("*SpokeToWork \u00b7 report written "))

    def test_a_director_worktree_names_its_parent_repo(self):
        self.indexed("wf-lane", self.root / ".worktrees" / "Claude_Commandz-wf-ab12cd")
        self.assertTrue(self.stamp_line("wf-lane").startswith("*Claude_Commandz \u00b7 report written "))

    def test_the_hub_is_the_workspace(self):
        self.indexed("hub-lane", self.root)
        self.assertTrue(self.stamp_line("hub-lane").startswith("*workspace (hub) \u00b7 report written "))

    def test_a_lane_not_in_the_index_keeps_the_old_stamp_line(self):
        self.assertRegex(self.stamp_line("nobody-knows"), r"^\*Report written \d{1,2}:\d{2} (AM|PM)\*$")

    def test_a_folder_outside_the_repos_keeps_the_old_stamp_line(self):
        self.indexed("elsewhere", "/tmp/somewhere/else")
        self.assertTrue(self.stamp_line("elsewhere").startswith("*Report written "))

    def test_the_same_name_in_two_places_without_a_date_match_is_not_guessed(self):
        self.indexed("again", self.root / "ScriptHammer", date="2026-10-01")
        self.indexed("again", self.root / "SpokeToWork", date="2026-10-02")
        self.assertTrue(self.stamp_line("again").startswith("*Report written "))

    def test_the_same_name_in_two_places_takes_the_row_of_the_report_date(self):
        self.indexed("again", self.root / "ScriptHammer", date="2026-10-18")
        self.indexed("again", self.root / "SpokeToWork", date="2026-10-02")
        self.assertTrue(self.stamp_line("again").startswith("*ScriptHammer \u00b7 "))

    def test_the_report_itself_is_unchanged(self):
        self.indexed("fix-nav", self.root / "ScriptHammer")
        self.seeded()
        self.report("fix-nav", "Done: it works.\n\n- one\n- two\n")
        self.run_posts()
        self.assertTrue(self.host.chats[0].endswith("\n\nDone: it works.\n\n- one\n- two"), self.host.chats[0])
        self.assertTrue(self.host.chats[0].startswith("### fix-nav\n"))


class OneFormattedPostPerReport(ReportPosts):
    def test_a_new_report_is_one_post_with_the_lane_as_heading_and_the_text_whole(self):
        self.seeded()
        self.report("harmonic-egg-mail", "Done: the renewal card is waiting.\n\n- Cards: 3\n- Next: his answers\n")
        self.assertEqual(self.run_posts(), ["harmonic-egg-mail"])
        self.assertEqual(len(self.host.chats), 1)
        text = self.host.chats[0]
        self.assertTrue(text.startswith("### harmonic-egg-mail\n"), text)
        self.assertIn("Done: the renewal card is waiting.\n\n- Cards: 3\n- Next: his answers", text)
        self.assertRegex(text, r"\b\d{1,2}:\d{2} (AM|PM)\b")
        self.assertNotRegex(text, r"\b(?:1[3-9]|2[0-3]):\d{2}\b")

    def test_a_second_run_posts_nothing(self):
        self.seeded()
        self.report("late-lane", "Done: x.")
        self.run_posts()
        self.assertEqual(self.run_posts(), [])
        self.assertEqual(len(self.host.chats), 1)

    def test_a_report_that_changes_is_posted_again_once(self):
        self.seeded()
        p = self.report("late-lane", "Done: first version.")
        self.run_posts()
        p.write_text("Done: second version.")
        os.utime(p, (time.time() - 100, time.time() - 100))
        self.assertEqual(self.run_posts(), ["late-lane"])
        self.assertEqual(self.run_posts(), [])
        self.assertEqual(len(self.host.chats), 2)
        self.assertIn("second version", self.host.chats[1])

    def test_a_long_report_goes_whole_in_numbered_parts_and_is_never_trimmed(self):
        self.seeded()
        body = "\n\n".join("Paragraph %d. %s" % (i, "word " * 60) for i in range(1, 30))
        self.report("long-lane", body)
        self.run_posts()
        self.assertGreater(len(self.host.chats), 1)
        for i, part in enumerate(self.host.chats, 1):
            self.assertTrue(part.endswith(" (part %d of %d)" % (i, len(self.host.chats))), part[-40:])
        got = "".join(re.sub(r" \(part \d+ of \d+\)$", "", p) for p in self.host.chats)
        for i in range(1, 30):
            self.assertIn("Paragraph %d." % i, got)
        self.assertNotIn("(trimmed)", got)

    def test_the_text_is_cleaned_of_control_characters(self):
        self.seeded()
        self.report("tty-lane", "Done:\x1b[31m red\x00 text.")
        self.run_posts()
        self.assertNotRegex(self.host.chats[0], r"[\x00-\x08\x0b-\x1f\x7f]")

    def test_an_empty_report_is_not_posted(self):
        self.seeded()
        self.report("blank-lane", "   \n")
        self.assertEqual(self.run_posts(), [])
        self.assertEqual(self.host.chats, [])

    def test_a_report_still_being_written_waits_for_the_next_run(self):
        self.seeded()
        self.report("busy-lane", "Done: half", age=2)
        self.assertEqual(self.run_posts(), [])
        p = self.lanes / "2026-10-18-busy-lane" / "report.md"
        t = time.time() - 60
        os.utime(p, (t, t))
        self.assertEqual(self.run_posts(), ["busy-lane"])

    def test_a_post_that_could_not_land_is_tried_again_next_run(self):
        self.seeded()
        self.report("retry-lane", "Done: x.")
        real = self.host.inject
        self.host.inject = lambda text: False
        self.assertEqual(self.run_posts(), [])
        self.host.inject = real
        self.assertEqual(self.run_posts(), ["retry-lane"])

    def test_a_lane_the_dispatcher_started_is_left_to_its_own_sweep(self):
        self.seeded()
        p = self.report("oc-own-app-1", "Done: its sweep posts this.")
        state = dp.load_state(self.ctx.state_path)
        state["lanes"]["oc-own-app-1"] = {"repo": "own-app", "status": "started", "report": str(p), "started": NOW}
        dp.save_state(self.ctx.state_path, state)
        self.assertEqual(self.run_posts(), [])
        self.assertEqual(self.host.chats, [])

    def test_folders_that_are_not_lanes_are_ignored(self):
        self.seeded()
        (self.lanes / "context").mkdir(parents=True)
        (self.lanes / "context" / "report.md").write_text("not a lane")
        (self.lanes / "queue").mkdir()
        self.assertEqual(self.run_posts(), [])

    def test_two_lanes_with_reports_are_two_posts(self):
        self.seeded()
        self.report("one", "Done: one.")
        self.report("two", "Done: two.")
        self.assertEqual(sorted(self.run_posts()), ["one", "two"])
        self.assertEqual(len(self.host.chats), 2)


class TheCommandLine(unittest.TestCase):
    def test_post_reports_is_a_verb_of_the_dispatcher(self):
        src = (Path(dp.__file__)).read_text()
        self.assertIn('argv[0] == "post-reports"', src)

    def test_the_status_task_runs_it(self):
        sh = (Path(dp.__file__).parent / "openclaw_status_refresh.sh").read_text()
        self.assertIn("post-reports", sh)


ROLE_PEOPLE = PEOPLE + """<details>
<summary>Percival Dunmore is Jonathan's field partner on the porch job.</summary>
```
GitHub    unconfirmed. dunmore-by-design is a candidate only.
Email     percy.dunmore@example.test
Role      Porch partner.
Mask      field partner
```
</details>
<details>
<summary>Ludmilla Carrow carries a bad mask line.</summary>
```
Mask      boss@example.test 555
```
</details>
"""


class RolesNotPerson(DigestBase):
    def setUp(self):
        super().setUp()
        (self.d / "people.md").write_text(ROLE_PEOPLE)
        self.names = dg.load_names([self.d / "people.md", self.d / "senders.tsv"])
        self.flt = dg.Filter(self.names)

    def test_a_person_with_a_mask_line_is_masked_by_that_role(self):
        self.assertEqual(self.flt.clean("porch waits on Percival tape"), "porch waits on [field partner] tape")
        self.assertEqual(self.flt.clean("Dunmore's tape"), "[field partner] tape")
        self.assertEqual(self.flt.clean("mail from percy"), "mail from [field partner]")

    def test_everyone_else_is_still_a_bare_person(self):
        self.assertEqual(self.flt.clean("Bartholomew Fitch keeps the books"), "[person] [person] keeps the books")

    def test_a_mask_line_that_is_not_plain_words_is_ignored(self):
        self.assertEqual(self.flt.clean("Ludmilla called"), "[person] called")

    def test_the_name_never_reaches_the_digest(self):
        roadmap = ("## Roadmap\n\n<details>\n<summary><b>Arc 1: Porch</b> (1 open, 0 done)</summary>\n\n"
                   "- [ ] **Porch measuring waits on Percival tape.** detail\n</details>\n")
        out = self.digest(roadmap_body=roadmap, flt=self.flt)
        self.assertIn("[field partner]", out)
        self.assertNotRegex(out, r"(?i)percival|dunmore")

    def test_load_names_exposes_the_roles(self):
        names = dg.load_names([self.d / "people.md"])
        self.assertEqual(names.roles.get("percival"), "field partner")
        self.assertNotIn("ludmilla", names.roles)


class TheReceptionistForwardsNextStepQuestions(unittest.TestCase):
    def test_a_next_step_question_is_forwarded_as_the_whole_reply(self):
        text = (HOOK / "agents_live.md").read_text()
        line = next((l for l in text.splitlines() if "next step" in l.lower() and "what's left" in l.lower()), "")
        self.assertTrue(line, "agents_live.md has no next-step rule")
        self.assertIn("whole reply", line)
        self.assertRegex(line, r"(?i)no (?:answer|snapshot)")


class PicturesInPosts(unittest.TestCase):
    def test_a_picture_line_has_a_plain_description_and_the_windows_path(self):
        line = dp.image_line("/home/TurtleWolfe/repos/x/reports/porch.png", "the porch rail, left side", distro="Debian")
        self.assertIn("the porch rail, left side", line)
        self.assertIn("\\\\wsl.localhost\\Debian\\home\\TurtleWolfe\\repos\\x\\reports\\porch.png", line)
        self.assertNotIn("![", line)

    def test_the_doc_says_why_there_is_no_embedding(self):
        self.assertIn("base64", dp.post_line.__doc__)
        self.assertIn("image_line", dp.post_line.__doc__)


if __name__ == "__main__":
    unittest.main()
